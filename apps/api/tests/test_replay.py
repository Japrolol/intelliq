"""Security and determinism tests for the fixture-only historical replay API."""

import httpx
from fastapi.testclient import TestClient

from src.app.config import Settings
from src.app.main import create_app

ORG = "demo-org"
DECISION_PATH = f"/api/organizations/{ORG}/decisions"


def _client() -> TestClient:
    return TestClient(create_app(Settings(data_mode="fixture", database_url="sqlite:///:memory:")))


def _csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("intelliq_csrf", "")}


def _logged_in_client() -> TestClient:
    client = _client()
    client.__enter__()
    response = client.post(
        "/api/auth/login", json={"email": "demo@example.com", "password": "demo"}
    )
    assert response.status_code == 200
    return client


def _create_decision(
    client: TestClient,
    *,
    guardrails: list[str] | None = None,
    with_checkpoint: bool = False,
) -> dict:
    analysis_response = client.post(
        "/api/organizations/demo-org/analyses",
        json={"projectId": "alpha", "mode": "recovery", "seed": 20260919},
        headers=_csrf(client),
    )
    assert analysis_response.status_code == 200, analysis_response.text
    analysis = analysis_response.json()
    strategy = next(
        item
        for item in analysis["strategies"]
        if str(item["id"]).startswith("transfer-eb-beta-alpha-")
    )
    checkpoints = (
        [
            {
                "id": "setup-check",
                "dueAt": "2026-09-21T09:00:00+02:00",
                "metric": "setup_hours",
                "comparator": "at_most",
                "threshold": 2,
            }
        ]
        if with_checkpoint
        else []
    )
    decision_response = client.post(
        DECISION_PATH,
        json={
            "analysisId": analysis["id"],
            "strategyId": strategy["id"],
            "guardrails": guardrails or [],
            "checkpoints": checkpoints,
            "idempotencyKey": f"replay-{analysis['id']}",
        },
        headers=_csrf(client),
    )
    assert decision_response.status_code == 201, decision_response.text
    return decision_response.json()


def _replay(client: TestClient, decision_id: str, as_of: str) -> httpx.Response:
    return client.post(
        f"{DECISION_PATH}/{decision_id}/replay",
        json={"asOf": as_of, "seed": 20260919, "samples": 25},
        headers=_csrf(client),
    )


def test_fixture_replay_is_labeled_uses_frozen_prediction_and_does_not_write_back() -> None:
    client = _logged_in_client()
    try:
        decision = _create_decision(client)
        response = _replay(client, decision["id"], "2026-09-21T10:00:00+02:00")

        assert response.status_code == 200, response.text
        replay = response.json()
        assert replay["sourceMode"] == "fixture"
        assert replay["synthetic"] is True
        assert replay["syntheticLabel"] == "Synthetic historical replay — fixture data only"
        assert replay["replayMode"] == "historical_synthetic_fixture"
        assert replay["appliedToTimecue"] is False
        assert replay["before"]["immutable"] is True
        assert replay["before"]["outcomes"] == decision["selectedOutcomes"]
        assert replay["originalPrediction"] == decision["selectedOutcomes"]
        assert replay["before"]["modelVersion"] == decision["modelVersion"]
        assert replay["after"]["modelVersion"] == decision["modelVersion"]
        assert any(item["sourceId"] == "demo-worklog-1" for item in replay["evidence"])

        stored = client.get(f"{DECISION_PATH}/{decision['id']}")
        assert stored.status_code == 200
        assert stored.json()["selectedOutcomes"] == decision["selectedOutcomes"]
    finally:
        client.__exit__(None, None, None)


def test_replay_does_not_expose_future_observations_or_evidence_before_both_times() -> None:
    client = _logged_in_client()
    try:
        decision = _create_decision(client)
        action = next(item for item in decision["exactActions"] if item["type"] == "transfer")
        future_observation_response = client.post(
            f"{DECISION_PATH}/{decision['id']}/observations",
            json={
                "eventAt": "2026-09-22T09:00:00+02:00",
                "knownAt": "2026-09-22T10:00:00+02:00",
                "workerId": action["workerId"],
                "fromProjectId": action["fromProjectId"],
                "toProjectId": action["toProjectId"],
                "setupHours": 3,
            },
            headers=_csrf(client),
        )
        assert future_observation_response.status_code == 200
        future_observation = future_observation_response.json()["observation"]
        client.app.state.fixture.payload["worklogs"].append(
            {
                "id": "future-worklog",
                "projectId": "alpha",
                "taskId": "a1",
                "workDate": "2026-09-22",
                "knownAt": "2026-09-22T10:00:00+02:00",
                "description": "future evidence must remain hidden",
            }
        )

        early = _replay(client, decision["id"], "2026-09-22T09:30:00+02:00")
        assert early.status_code == 200, early.text
        early_body = early.json()
        early_text = early.text
        assert future_observation["id"] not in early_text
        assert "future evidence must remain hidden" not in early_text
        assert early_body["excludedFutureObservationCount"] >= 1
        assert early_body["excludedFutureEvidenceCount"] >= 1
        assert early_body["after"]["calibrationVersion"] == "initial"

        late = _replay(client, decision["id"], "2026-09-22T11:00:00+02:00")
        assert late.status_code == 200, late.text
        late_body = late.json()
        assert future_observation["id"] in late.text
        assert "future evidence must remain hidden" in late.text
        assert late_body["after"]["calibrationVersion"] == "1"
    finally:
        client.__exit__(None, None, None)


def test_fixture_replay_is_reproducible_for_same_clock_seed_and_sample_count() -> None:
    client = _logged_in_client()
    try:
        decision = _create_decision(client)
        first = _replay(client, decision["id"], "2026-09-21T10:00:00+02:00")
        second = _replay(client, decision["id"], "2026-09-21T10:00:00+02:00")
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert first.json() == second.json()
    finally:
        client.__exit__(None, None, None)


def test_replay_requires_auth_tenant_access_and_csrf() -> None:
    unauthenticated = _client()
    with unauthenticated:
        response = unauthenticated.post(
            f"/api/organizations/{ORG}/decisions/missing/replay",
            json={"asOf": "2026-09-21T10:00:00+02:00"},
        )
        assert response.status_code == 401

    client = _logged_in_client()
    try:
        decision = _create_decision(client)
        missing_csrf = client.post(
            f"{DECISION_PATH}/{decision['id']}/replay",
            json={"asOf": "2026-09-21T10:00:00+02:00"},
        )
        assert missing_csrf.status_code == 403

        wrong_tenant = client.post(
            f"/api/organizations/another-org/decisions/{decision['id']}/replay",
            json={"asOf": "2026-09-21T10:00:00+02:00"},
            headers=_csrf(client),
        )
        assert wrong_tenant.status_code == 403
    finally:
        client.__exit__(None, None, None)


def test_live_mode_blocks_replay_before_any_live_data_read() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(
                200,
                headers=[
                    ("set-cookie", "access_token=access; Path=/"),
                    ("set-cookie", "refresh_token=refresh; Path=/"),
                ],
            )
        if request.url.path.endswith("/auth/me"):
            return httpx.Response(
                200,
                json={
                    "id": "live-user",
                    "email": "live@example.com",
                    "emailVerifiedAt": "2026-09-01T00:00:00Z",
                    "organizations": [{"id": ORG}],
                    "organizationPermissions": {
                        ORG: ["projects.read", "tasks.read", "workers.read", "worklogs.read"]
                    },
                },
            )
        raise AssertionError(f"unexpected live request: {request.url.path}")

    settings = Settings(
        data_mode="live", database_url="sqlite:///:memory:", timecue_api_url="https://timecue.test"
    )
    client = TestClient(create_app(settings, transport=httpx.MockTransport(handler)))
    with client:
        login = client.post(
            "/api/auth/login", json={"email": "live@example.com", "password": "secret"}
        )
        assert login.status_code == 200
        response = client.post(
            f"/api/organizations/{ORG}/decisions/missing/replay",
            json={"asOf": "2026-09-21T10:00:00+02:00"},
            headers=_csrf(client),
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "fixture_replay_only"
        assert all(path.endswith("/auth/login") or path.endswith("/auth/me") for path in calls)


def test_decision_freezes_manager_guardrails_separately_from_engine_checks() -> None:
    client = _logged_in_client()
    try:
        guardrails = ["Keep donor coverage on Beta", "Recheck Alpha at the checkpoint"]
        decision = _create_decision(client, guardrails=guardrails)
        assert decision["guardrails"] == guardrails
        assert isinstance(decision["guardrailChecks"], dict)
        assert decision["guardrails"] != decision["guardrailChecks"]
    finally:
        client.__exit__(None, None, None)


def test_checkpoint_requires_same_decision_source_and_rejects_conflicting_setup_value() -> None:
    client = _logged_in_client()
    try:
        decision = _create_decision(client, with_checkpoint=True)
        action = next(item for item in decision["exactActions"] if item["type"] == "transfer")
        observation = client.post(
            f"{DECISION_PATH}/{decision['id']}/observations",
            json={
                "eventAt": "2026-09-21T08:30:00+02:00",
                "knownAt": "2026-09-21T09:00:00+02:00",
                "workerId": action["workerId"],
                "fromProjectId": action["fromProjectId"],
                "toProjectId": action["toProjectId"],
                "setupHours": 3,
            },
            headers=_csrf(client),
        ).json()["observation"]

        conflicting = client.post(
            f"{DECISION_PATH}/{decision['id']}/checkpoints/check",
            json={
                "checkpointId": "setup-check",
                "asOf": "2026-09-21T10:00:00+02:00",
                "observedValue": 1,
                "evidence": {
                    "sourceType": "decision_observation",
                    "sourceId": observation["id"],
                },
            },
            headers=_csrf(client),
        )
        assert conflicting.status_code == 422
        assert conflicting.json()["detail"] == "checkpoint_observed_value_mismatch"

        accepted = client.post(
            f"{DECISION_PATH}/{decision['id']}/checkpoints/check",
            json={
                "checkpointId": "setup-check",
                "asOf": "2026-09-21T10:00:00+02:00",
                "observedValue": 3,
                "evidence": {
                    "sourceType": "decision_observation",
                    "sourceId": observation["id"],
                },
            },
            headers=_csrf(client),
        )
        assert accepted.status_code == 200
        assert accepted.json()["evaluation"]["state"] == "breached"
        assert (
            accepted.json()["evaluation"]["evidence"]["evidenceKind"]
            == "verified_direct_observation"
        )
    finally:
        client.__exit__(None, None, None)


def test_checkpoint_manager_attestation_is_server_bound_and_not_upstream_verified() -> None:
    client = _logged_in_client()
    try:
        decision = _create_decision(client, with_checkpoint=True)
        mismatch = client.post(
            f"{DECISION_PATH}/{decision['id']}/checkpoints/check",
            json={
                "checkpointId": "setup-check",
                "asOf": "2026-09-21T10:00:00+02:00",
                "observedValue": 1,
                "evidence": {"managerNote": "Manager checked the setup log.", "actorId": "other"},
            },
            headers=_csrf(client),
        )
        assert mismatch.status_code == 422
        assert mismatch.json()["detail"] == "checkpoint_evidence_actor_mismatch"

        accepted = client.post(
            f"{DECISION_PATH}/{decision['id']}/checkpoints/check",
            json={
                "checkpointId": "setup-check",
                "asOf": "2026-09-21T10:00:00+02:00",
                "observedValue": 1,
                "evidence": {"note": "Manager checked the setup log.", "actorId": "fixture-user"},
            },
            headers=_csrf(client),
        )
        assert accepted.status_code == 200
        evidence = accepted.json()["evaluation"]["evidence"]
        assert evidence["evidenceKind"] == "manager_attested"
        assert evidence["actorId"] == "fixture-user"
        assert evidence["upstreamVerified"] is False
    finally:
        client.__exit__(None, None, None)
