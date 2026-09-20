from fastapi.testclient import TestClient

from src.app.config import Settings
from src.app.main import create_app


def test_login_rejects_cross_origin_browser_post() -> None:
    settings = Settings(data_mode="fixture", database_url="sqlite:///:memory:")
    with TestClient(create_app(settings)) as client:
        blocked = client.post(
            "/api/auth/login",
            json={"email": "demo@example.com", "password": "demo"},
            headers={"Origin": "https://untrusted.example"},
        )
        assert blocked.status_code == 403
        assert blocked.json()["detail"] == "origin_not_allowed"
        assert client.cookies.get("intelliq_session") is None

        accepted = client.post(
            "/api/auth/login",
            json={"email": "demo@example.com", "password": "demo"},
            headers={"Origin": "http://127.0.0.1:5173"},
        )
        assert accepted.status_code == 200


def logged_in_client() -> TestClient:
    settings = Settings(data_mode="fixture", database_url="sqlite:///:memory:")
    client = TestClient(create_app(settings))
    client.__enter__()
    response = client.post(
        "/api/auth/login", json={"email": "demo@example.com", "password": "demo"}
    )
    assert response.status_code == 200
    extracted = client.post(
        "/api/organizations/demo-org/projects/alpha/signals/refresh",
        headers={"X-CSRF-Token": client.cookies.get("intelliq_csrf", "")},
    )
    assert extracted.status_code == 200
    return client


def csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("intelliq_csrf", "")}


def test_fixture_portfolio_and_evidence_are_read_only_and_truthful() -> None:
    client = logged_in_client()
    try:
        portfolio = client.get("/api/organizations/demo-org/portfolio")
        assert portfolio.status_code == 200
        assert portfolio.json()["readinessStatus"] == "ready"
        assert portfolio.json()["sourceMode"] == "fixture"

        signals = client.get("/api/organizations/demo-org/projects/alpha/signals").json()
        observations = [item["observation"] for item in signals["pending"]]
        assert {item["kind"] for item in observations} == {"dependency_blocker", "resolution"}
        assert any(
            "cannot close the walls" in item["evidenceQuotes"][0]["text"] for item in observations
        )
        assert any(
            "cables have already arrived" in item["evidenceQuotes"][0]["text"]
            for item in observations
        )
    finally:
        client.__exit__(None, None, None)


def test_planning_inputs_use_optimistic_versioning() -> None:
    client = logged_in_client()
    try:
        current = client.get("/api/organizations/demo-org/planning-inputs").json()
        payload = {**current, "expectedVersion": current["version"]}
        updated = client.put(
            "/api/organizations/demo-org/planning-inputs", json=payload, headers=csrf(client)
        )
        assert updated.status_code == 200
        assert updated.json()["version"] == 1
        conflict = client.put(
            "/api/organizations/demo-org/planning-inputs", json=payload, headers=csrf(client)
        )
        assert conflict.status_code == 409
    finally:
        client.__exit__(None, None, None)


def test_planning_overlay_rejects_invented_workers_and_source_changes() -> None:
    client = logged_in_client()
    try:
        current = client.get("/api/organizations/demo-org/planning-inputs").json()
        invented_worker = {
            **current,
            "workers": [{**current["workers"][0], "id": "not-authorized"}],
            "expectedVersion": current["version"],
        }
        response = client.put(
            "/api/organizations/demo-org/planning-inputs",
            json=invented_worker,
            headers=csrf(client),
        )
        assert response.status_code == 422

        changed_status = {
            **current,
            "tasks": [{**current["tasks"][0], "status": "done"}],
            "expectedVersion": current["version"],
        }
        response = client.put(
            "/api/organizations/demo-org/planning-inputs",
            json=changed_status,
            headers=csrf(client),
        )
        assert response.status_code == 422
    finally:
        client.__exit__(None, None, None)


def test_reviewed_signal_survives_snapshot_reingestion() -> None:
    client = logged_in_client()
    try:
        initial = client.get("/api/organizations/demo-org/projects/alpha/signals").json()
        signal = initial["pending"][0]
        reviewed = client.post(
            f"/api/organizations/demo-org/signals/{signal['id']}/review",
            json={"status": "confirmed"},
            headers=csrf(client),
        )
        assert reviewed.status_code == 200

        after = client.get("/api/organizations/demo-org/projects/alpha/signals").json()
        assert signal["id"] in {item["id"] for item in after["reviewed"]}
        assert signal["id"] not in {item["id"] for item in after["pending"]}
    finally:
        client.__exit__(None, None, None)


def test_rejected_signal_cannot_change_planning_inputs() -> None:
    client = logged_in_client()
    try:
        before = client.get("/api/organizations/demo-org/planning-inputs").json()
        signal = client.get("/api/organizations/demo-org/projects/alpha/signals").json()[
            "pending"
        ][0]
        response = client.post(
            f"/api/organizations/demo-org/signals/{signal['id']}/review",
            json={
                "status": "rejected",
                "planningChange": {"taskId": before["tasks"][0]["id"], "planningNotes": "unsafe"},
            },
            headers=csrf(client),
        )
        assert response.status_code == 422
        assert response.json()["detail"] == "planning_change_requires_confirmed_signal"
        after = client.get("/api/organizations/demo-org/planning-inputs").json()
        assert after == before
    finally:
        client.__exit__(None, None, None)


def test_signal_review_rejects_repeat_and_stale_source() -> None:
    client = logged_in_client()
    try:
        signals = client.get("/api/organizations/demo-org/projects/alpha/signals").json()
        first, second = signals["pending"][:2]
        review_path = f"/api/organizations/demo-org/signals/{first['id']}/review"
        confirmed = client.post(
            review_path, json={"status": "confirmed"}, headers=csrf(client)
        )
        assert confirmed.status_code == 200
        repeated = client.post(
            review_path, json={"status": "rejected"}, headers=csrf(client)
        )
        assert repeated.status_code == 409

        source = next(
            item
            for item in client.app.state.fixture.payload["worklogs"]
            if item["id"] == second["sourceId"]
        )
        source["updatedAt"] = "2026-09-19T12:00:00Z"
        source["description"] = "The source note was corrected after the initial review."
        stale_review = client.post(
            f"/api/organizations/demo-org/signals/{second['id']}/review",
            json={"status": "confirmed"},
            headers=csrf(client),
        )
        assert stale_review.status_code == 409
        assert stale_review.json()["detail"] == "signal_source_changed"
    finally:
        client.__exit__(None, None, None)


def test_confirmed_signal_cannot_change_a_different_task() -> None:
    client = logged_in_client()
    try:
        signals = client.get("/api/organizations/demo-org/projects/alpha/signals").json()
        signal = signals["pending"][0]
        task_ids = [task["id"] for task in client.get(
            "/api/organizations/demo-org/planning-inputs"
        ).json()["tasks"]]
        unrelated_task = next(task_id for task_id in task_ids if task_id != signal["taskId"])
        response = client.post(
            f"/api/organizations/demo-org/signals/{signal['id']}/review",
            json={
                "status": "confirmed",
                "planningChange": {"taskId": unrelated_task, "planningNotes": "unsafe"},
            },
            headers=csrf(client),
        )
        assert response.status_code == 422
        assert response.json()["detail"] == "planning_change_task_mismatch"
    finally:
        client.__exit__(None, None, None)


def test_decision_rejects_analysis_after_planning_revision() -> None:
    client = logged_in_client()
    try:
        analysis = client.post(
            "/api/organizations/demo-org/analyses",
            json={"projectId": "alpha", "mode": "recovery"},
            headers=csrf(client),
        ).json()
        strategy = analysis["strategies"][0]
        current = client.get("/api/organizations/demo-org/planning-inputs").json()
        changed = {**current, "expectedVersion": current["version"]}
        assert (
            client.put(
                "/api/organizations/demo-org/planning-inputs",
                json=changed,
                headers=csrf(client),
            ).status_code
            == 200
        )
        response = client.post(
            "/api/organizations/demo-org/decisions",
            json={
                "analysisId": analysis["id"],
                "strategyId": strategy["id"],
                "rationale": "stale check",
                "idempotencyKey": "stale-decision-01",
            },
            headers=csrf(client),
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "analysis_stale"
    finally:
        client.__exit__(None, None, None)


def test_decision_closed_loop_calibration_and_checkpoint_are_append_only() -> None:
    client = logged_in_client()
    try:
        analysis_response = client.post(
            "/api/organizations/demo-org/analyses",
            json={"projectId": "alpha", "mode": "recovery"},
            headers=csrf(client),
        )
        assert analysis_response.status_code == 200
        analysis = analysis_response.json()
        strategy = next(
            item
            for item in analysis["strategies"]
            if str(item["id"]).startswith("transfer-eb-beta-alpha-")
        )
        decision_response = client.post(
            "/api/organizations/demo-org/decisions",
            json={
                "analysisId": analysis["id"],
                "strategyId": strategy["id"],
                "rationale": "Use the verified donor window.",
                "idempotencyKey": "closed-loop-01",
                "checkpoints": [
                    {
                        "id": "setup-check",
                        "dueAt": "2026-09-22T08:00:00+02:00",
                        "metric": "setup_hours",
                        "comparator": "at_most",
                        "threshold": 2,
                        "evidenceRequirement": "direct setup record",
                    }
                ],
            },
            headers=csrf(client),
        )
        assert decision_response.status_code == 201
        decision = decision_response.json()
        action = next(action for action in decision["exactActions"] if action["type"] == "transfer")

        implementation = client.post(
            f"/api/organizations/demo-org/decisions/{decision['id']}/implementation",
            json={"state": "implemented", "eventAt": "2026-09-22T08:00:00+02:00"},
            headers=csrf(client),
        )
        assert implementation.status_code == 200

        observation_body = {
            "eventAt": "2026-09-22T08:30:00+02:00",
            "knownAt": "2026-09-22T09:00:00+02:00",
            "workerId": action["workerId"],
            "fromProjectId": action["fromProjectId"],
            "toProjectId": action["toProjectId"],
            "setupHours": 2,
        }
        first = client.post(
            f"/api/organizations/demo-org/decisions/{decision['id']}/observations",
            json=observation_body,
            headers=csrf(client),
        )
        assert first.status_code == 200
        second = client.post(
            f"/api/organizations/demo-org/decisions/{decision['id']}/observations",
            json={**observation_body, "setupHours": 3},
            headers=csrf(client),
        )
        assert second.status_code == 200
        assert second.json()["calibration"]["meanHours"] == 1.75

        calibration = client.get("/api/organizations/demo-org/calibration/setup-hours")
        assert calibration.status_code == 200
        assert calibration.json()["current"]["meanHours"] == 1.75
        assert [item["version"] for item in calibration.json()["history"]] == [1, 2]

        missing = client.post(
            f"/api/organizations/demo-org/decisions/{decision['id']}/checkpoints/check",
            json={
                "checkpointId": "setup-check",
                "asOf": "2026-09-22T09:00:00+02:00",
                "observedValue": 1,
            },
            headers=csrf(client),
        )
        assert missing.status_code == 200
        assert missing.json()["evaluation"]["state"] == "needs_data"

        breached = client.post(
            f"/api/organizations/demo-org/decisions/{decision['id']}/checkpoints/check",
            json={
                "checkpointId": "setup-check",
                "asOf": "2026-09-22T09:00:00+02:00",
                "observedValue": 3,
                "evidence": {
                    "sourceType": "decision_observation",
                    "sourceId": second.json()["observation"]["id"],
                },
            },
            headers=csrf(client),
        )
        assert breached.status_code == 200
        assert breached.json()["evaluation"]["state"] == "breached"

        stored = client.get(f"/api/organizations/demo-org/decisions/{decision['id']}")
        assert stored.status_code == 200
        assert stored.json()["analysisId"] == analysis["id"]
        assert stored.json()["implementationState"] == "not_applied"
        assert len(stored.json()["observations"]) == 3
        assert len(stored.json()["checkpointEvaluations"]) == 2
    finally:
        client.__exit__(None, None, None)


def test_missing_engine_is_a_visible_integration_failure(monkeypatch) -> None:
    import src.app.services.portfolio as routes
    from src.app.domain.engine_bridge import SimulationEngineUnavailable

    def unavailable(_payload):
        raise SimulationEngineUnavailable("test engine unavailable")

    monkeypatch.setattr(routes, "evaluate_portfolio", unavailable)
    client = logged_in_client()
    try:
        response = client.post(
            "/api/organizations/demo-org/analyses",
            json={"projectId": "alpha", "mode": "baseline"},
            headers=csrf(client),
        )
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "simulation_engine_unavailable"
    finally:
        client.__exit__(None, None, None)


def test_real_fixture_engine_analysis_is_persisted_and_frontend_shaped() -> None:
    client = logged_in_client()
    try:
        response = client.post(
            "/api/organizations/demo-org/analyses",
            json={"projectId": "alpha", "mode": "recovery"},
            headers=csrf(client),
        )
        assert response.status_code == 200
        analysis = response.json()
        assert analysis["status"] == "completed"
        assert analysis["baselineByProject"]["alpha"]["expectedPositiveDelayDays"] == 2.0
        transfer = next(
            strategy
            for strategy in analysis["strategies"]
            if str(strategy["id"]).startswith("transfer-eb-beta-alpha-")
        )
        assert transfer["target"]["expectedPositiveDelayDays"] == 1.0
        assert transfer["donor"]["finishP50"] == "2026-09-23T16:00:00+02:00"
        assert transfer["transfer"]["workerId"] == "eb"
        stored = client.get(f"/api/organizations/demo-org/analyses/{analysis['id']}")
        assert stored.status_code == 200
        assert stored.json()["id"] == analysis["id"]
    finally:
        client.__exit__(None, None, None)


def test_invalid_engine_input_is_a_structured_422(monkeypatch) -> None:
    import src.app.services.portfolio as routes
    from src.app.domain.engine_bridge import SimulationInputError

    monkeypatch.setattr(
        routes,
        "evaluate_portfolio",
        lambda _payload: (_ for _ in ()).throw(SimulationInputError("bad payload")),
    )
    client = logged_in_client()
    try:
        response = client.post(
            "/api/organizations/demo-org/analyses",
            json={"projectId": "alpha", "mode": "baseline"},
            headers=csrf(client),
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "simulation_input_invalid"
    finally:
        client.__exit__(None, None, None)


def test_analysis_and_decision_use_stored_engine_result(monkeypatch) -> None:
    import src.app.services.portfolio as routes

    monkeypatch.setattr(
        routes,
        "evaluate_portfolio",
        lambda payload: {
            "baselineByProject": [{"projectId": "alpha", "expectedPositiveDelayDays": 2}],
            "strategies": [
                {
                    "id": "transfer-eb",
                    "actions": [{"type": "transfer"}],
                    "outcomesByProject": [{"projectId": "alpha", "expectedPositiveDelayDays": 1}],
                    "affectedProjectIds": ["alpha", "beta"],
                }
            ],
            "diagnostics": [],
            "seed": payload["seed"],
            "sampleCount": payload["samples"],
            "warnings": [],
        },
    )
    client = logged_in_client()
    try:
        analysis = client.post(
            "/api/organizations/demo-org/analyses",
            json={"projectId": "alpha", "mode": "recovery"},
            headers=csrf(client),
        )
        assert analysis.status_code == 200
        analysis_body = analysis.json()
        assert analysis_body["status"] == "completed"
        decision = client.post(
            "/api/organizations/demo-org/decisions",
            json={
                "analysisId": analysis_body["id"],
                "strategyId": "transfer-eb",
                "rationale": "Protect Alpha",
                "idempotencyKey": "decision-001",
            },
            headers=csrf(client),
        )
        assert decision.status_code == 201
        assert decision.json()["message"] == "Decision recorded. Not applied to Timecue."
        replay = client.post(
            "/api/organizations/demo-org/decisions",
            json={
                "analysisId": analysis_body["id"],
                "strategyId": "transfer-eb",
                "idempotencyKey": "decision-001",
            },
            headers=csrf(client),
        )
        assert replay.status_code == 201
        assert replay.json()["id"] == decision.json()["id"]
    finally:
        client.__exit__(None, None, None)
