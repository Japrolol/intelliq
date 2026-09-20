"""Exercise the real fixture adapter, readiness gate, engine, and decision API."""

from fastapi.testclient import TestClient

from src.app.config import Settings
from src.app.main import create_app


def test_fixture_analysis_compares_target_and_donor() -> None:
    settings = Settings(data_mode="fixture", database_url="sqlite:///:memory:")
    with TestClient(create_app(settings)) as client:
        login = client.post(
            "/api/auth/login",
            json={"email": "demo@example.com", "password": "demo"},
        )
        assert login.status_code == 200
        assert login.json()["synthetic"] is True

        csrf = client.cookies.get("intelliq_csrf", "")
        response = client.post(
            "/api/organizations/demo-org/analyses",
            json={"projectId": "alpha", "mode": "recovery", "seed": 20260919},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 200, response.text
        analysis = response.json()
        assert analysis["status"] == "completed"
        baseline = analysis["baselineByProject"]
        assert baseline["alpha"]["finishP50"].startswith("2026-09-24T16:00:00")
        assert baseline["beta"]["finishP50"].startswith("2026-09-22T16:00:00")

        alternatives = [
            strategy
            for strategy in analysis["strategies"]
            if strategy["id"] != "baseline" and strategy["feasible"]
        ]
        assert alternatives
        matching = [
            strategy
            for strategy in alternatives
            if strategy["outcomesByProject"]["alpha"]["finishP50"].startswith("2026-09-23T16:00:00")
            and strategy["outcomesByProject"]["beta"]["finishP50"].startswith("2026-09-23T16:00:00")
        ]
        assert matching
        assert matching[0]["outcomesByProject"]["beta"]["expectedPositiveDelayDays"] == 0

        decision = client.post(
            "/api/organizations/demo-org/decisions",
            json={
                "analysisId": analysis["id"],
                "strategyId": matching[0]["id"],
                "idempotencyKey": "fixture-transfer-decision",
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert decision.status_code == 201, decision.text
        assert decision.json()["implementationState"] == "not_applied"
