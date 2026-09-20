"""Real local pipeline checks; no live Timecue/provider claims."""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from src.app.config import Settings
from src.app.domain.contracts import PortfolioSnapshot
from src.app.main import create_app
from src.app.persistence.store import Store
from src.app.services.portfolio import planning_for


def test_workflow_records_and_leases_are_tenant_scoped():
    store = Store("sqlite:///:memory:")
    store.migrate_schema()
    store.save_record("a", "test", "same", {"value": 1})
    assert store.get_record("b", "test", "same") is None
    token = store.acquire_lease("a", "execute")
    assert token
    assert store.acquire_lease("a", "execute") is None
    assert store.acquire_lease("b", "execute")
    assert store.renew_lease("a", "execute", token)
    assert not store.renew_lease("a", "execute", "wrong")
    store.release_lease("a", "execute", "wrong")
    assert store.acquire_lease("a", "execute") is None
    store.release_lease("a", "execute", token)
    assert store.acquire_lease("a", "execute")


def test_project_detail_keeps_operational_data_scoped_and_planning_separate():
    settings = Settings(data_mode="fixture", database_url="sqlite:///:memory:", jobs_enabled=False)
    with TestClient(create_app(settings)) as client:
        client.post("/api/auth/login", json={"email": "demo@example.com", "password": "demo"})
        base = "/api/organizations/demo-org"
        project_id = client.get(f"{base}/portfolio").json()["projects"][0]["id"]
        response = client.get(f"{base}/projects/{project_id}")
        assert response.status_code == 200, response.text
        detail = response.json()
        assert detail["project"]["id"] == project_id
        assert all(task["projectId"] == project_id for task in detail["tasks"])
        task_ids = {task["id"] for task in detail["tasks"]}
        assert all(a["taskId"] in task_ids for a in detail["assignments"])
        assert all(t["id"] in task_ids for t in detail["planning"]["tasks"])
        assert detail["sourceMode"] == "fixture"
        assert client.get(f"{base}/projects/not-a-project").status_code == 404
        assert client.get(f"/api/organizations/other/projects/{project_id}").status_code == 403


def test_v4_analysis_graph_review_apply_and_learning():
    settings = Settings(
        data_mode="fixture",
        database_url="sqlite:///:memory:",
        jobs_enabled=False,
        weatherapi_api_key="",
        openrouteservice_api_key="",
    )
    with TestClient(create_app(settings)) as client:
        assert (
            client.post(
                "/api/auth/login", json={"email": "demo@example.com", "password": "demo"}
            ).status_code
            == 200
        )
        csrf = {"X-CSRF-Token": client.cookies["intelliq_csrf"]}
        base = "/api/organizations/demo-org"
        assert client.get(f"{base}/overview").json()["analysis"] is None
        response = client.post(f"{base}/analyses", json={"seed": 7}, headers=csrf)
        assert response.status_code == 200, response.text
        analysis = response.json()
        assert analysis["status"] == "completed"
        assert analysis["sourceMode"] == "fixture"
        assert client.get(f"{base}/analyses/{analysis['id']}/graph").json()["nodes"]
        assert client.get(f"{base}/overview").json()["analysis"]["id"] == analysis["id"]
        strategy = next(
            (
                s
                for s in analysis["strategies"]
                if any(a["type"] == "transfer" for a in s["actions"])
            ),
            None,
        )
        assert strategy, "Demo must offer an attainable transfer for the learning slice"
        path = f"{base}/analyses/{analysis['id']}"
        preview = client.post(f"{path}/review", json={"strategyId": strategy["id"]}, headers=csrf)
        assert preview.status_code == 200, preview.text
        assert client.get(f"{base}/executions").json() == []
        body = {
            "strategyId": strategy["id"],
            "reviewHash": preview.json()["reviewHash"],
            "idempotencyKey": "test-approval-1",
        }
        assert client.post(f"{path}/apply", json=body).status_code == 403
        applied = client.post(f"{path}/apply", json=body, headers=csrf)
        assert applied.status_code == 200, applied.text
        execution = applied.json()
        assert client.post(f"{path}/apply", json=body, headers=csrf).json()["id"] == execution["id"]
        step = next(s for s in execution["steps"] if s["action"]["type"] == "transfer")
        complete = client.post(
            f"{base}/executions/{execution['id']}/steps/{step['id']}/complete",
            json={"note": "Synthetic demo implementation confirmed"},
            headers=csrf,
        )
        assert complete.status_code == 200
        measured = client.post(
            f"{base}/executions/{execution['id']}/outcomes",
            headers=csrf,
            json={
                "setupHours": 2,
                "workerId": step["action"]["workerId"],
                "eventAt": datetime.now(UTC).isoformat(),
                "note": "Synthetic measured setup",
            },
        )
        assert measured.status_code == 200, measured.text
        assert measured.json()["calibration"]["version"] == 1
        duplicate = client.post(
            f"{base}/executions/{execution['id']}/outcomes",
            headers=csrf,
            json={
                "setupHours": 2,
                "workerId": step["action"]["workerId"],
                "eventAt": datetime.now(UTC).isoformat(),
                "note": "Repeated request",
            },
        )
        assert duplicate.json()["calibration"]["version"] == 1
        later = client.post(f"{base}/analyses", json={"seed": 7}, headers=csrf)
        assert later.status_code == 200, later.text
        assert later.json()["calibrationVersion"] == "1"
        settings = client.get(f"{base}/settings").json()
        assert "sessionId" not in settings
        assert settings["analysisTime"] == "06:00"
        assert client.get("/readyz").status_code == 200
        assert client.get(f"{base}/overview").headers["cache-control"] == "no-store"


def test_atomic_analysis_input_and_outcome_records():
    store = Store("sqlite:///:memory:")
    store.migrate_schema()
    analysis_id = store.create_analysis(
        "a",
        "u",
        {"runKey": "r"},
        "completed",
        frozen_input={"planning": {}, "graph": {"nodes": []}},
    )
    assert store.get_record("a", "analysis_input", analysis_id)["graph"] == {"nodes": []}
    assert store.get_record("b", "analysis_input", analysis_id) is None
    assert store.create_analysis("a", "u", {"runKey": "r"}, "completed") == analysis_id
    assert store.get_record("a", "analysis_run", "r")["analysisId"] == analysis_id
    first = store.record_calibrated_outcome("a", "fixture", 1, {"meanHours": 2}, "o", {"value": 2})
    second = store.record_calibrated_outcome("a", "fixture", 2, {"meanHours": 9}, "o", {"value": 9})
    assert first == second
    assert len(store.list_calibration_history("a", "fixture")) == 1
    store.save_snapshot({"snapshotId": "same", "organizationId": "a", "sourceRevision": "r"})
    import pytest

    with pytest.raises(ValueError, match="snapshot_tenant_conflict"):
        store.save_snapshot({"snapshotId": "same", "organizationId": "b", "sourceRevision": "r"})


def test_get_evidence_does_not_extract_or_mutate():
    settings = Settings(data_mode="fixture", database_url="sqlite:///:memory:", jobs_enabled=False)
    with TestClient(create_app(settings)) as client:
        client.post("/api/auth/login", json={"email": "demo@example.com", "password": "demo"})
        base = "/api/organizations/demo-org/projects/alpha/signals"
        assert client.get(base).json()["signals"] == []
        response = client.post(
            f"{base}/refresh", headers={"X-CSRF-Token": client.cookies["intelliq_csrf"]}
        )
        assert response.status_code == 200
        assert client.get(base).json()["signals"]


def test_live_planning_keeps_confirmed_readiness_gate_and_canonical_entities():
    store = Store("sqlite:///:memory:")
    store.migrate_schema()
    store.save_planning(
        "org",
        {
            "projects": [{"id": "p", "name": "Old name"}],
            "tasks": [{"id": "t", "projectId": "p", "earliestStartAt": "2026-09-23T08:00:00Z"}],
            "workers": [],
            "transfers": [],
            "reservations": [],
        },
        0,
    )
    snapshot = PortfolioSnapshot(
        snapshotId="s",
        sourceRevision="r",
        organizationId="org",
        fetchedAt=datetime.now(UTC),
        asOf=datetime.now(UTC),
        sourceMode="live",
        projects=[{"id": "p", "name": "Timecue name"}],
        tasks=[{"id": "t", "projectId": "p", "plannedStartAt": "2026-09-21T08:00:00Z"}],
    )
    planning = planning_for(store, snapshot)
    assert planning.projects[0]["name"] == "Timecue name"
    assert planning.tasks[0]["earliestStartAt"] == "2026-09-23T08:00:00Z"
    assert planning.tasks[0]["plannedStartAt"] == "2026-09-21T08:00:00Z"


def test_live_planning_drops_archived_projects_and_their_tasks():
    store = Store("sqlite:///:memory:")
    store.migrate_schema()
    store.save_planning(
        "org",
        {
            "projects": [
                {"id": "p", "name": "Active"},
                {
                    "id": "archived",
                    "name": "Archived light-seed townhouse (unused)",
                    "status": "archived",
                },
            ],
            "tasks": [
                {"id": "t", "projectId": "p"},
                {"id": "t-archived", "projectId": "archived"},
            ],
            "workers": [],
            "transfers": [],
            "reservations": [],
        },
        0,
    )
    snapshot = PortfolioSnapshot(
        snapshotId="s",
        sourceRevision="r",
        organizationId="org",
        fetchedAt=datetime.now(UTC),
        asOf=datetime.now(UTC),
        sourceMode="live",
        projects=[
            {"id": "p", "name": "Active", "status": "in_progress"},
            {
                "id": "archived",
                "name": "Archived light-seed townhouse (unused)",
                "status": "archived",
            },
        ],
        tasks=[
            {"id": "t", "projectId": "p"},
            {"id": "t-archived", "projectId": "archived"},
        ],
    )
    planning = planning_for(store, snapshot)
    assert [project["id"] for project in planning.projects] == ["p"]
    assert [task["id"] for task in planning.tasks] == ["t"]


def test_readiness_ignores_archived_projects_missing_a_target_date():
    from src.app.domain.contracts import PlanningInputs
    from src.app.domain.readiness import readiness_issues

    snapshot = PortfolioSnapshot(
        snapshotId="s",
        sourceRevision="r",
        organizationId="org",
        fetchedAt=datetime.now(UTC),
        asOf=datetime.now(UTC),
        sourceMode="live",
        complete=True,
        projects=[],
        tasks=[],
    )
    planning = PlanningInputs(
        version=0,
        projects=[
            {
                "id": "live",
                "name": "Mokotow · Willa Pulawska 17",
                "status": "in_progress",
                "targetFinishAt": "2026-09-25T16:00:00+02:00",
            },
            {
                "id": "archived",
                "name": "Archived light-seed townhouse (unused)",
                "status": "archived",
            },
        ],
        tasks=[
            {
                "id": "t-live",
                "projectId": "live",
                "remainingPersonHours": {
                    "optimistic": 4,
                    "mostLikely": 8,
                    "pessimistic": 16,
                },
            },
            {"id": "t-archived", "projectId": "archived"},
        ],
    )
    assert readiness_issues(snapshot, planning) == []
