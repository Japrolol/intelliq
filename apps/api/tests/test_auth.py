import httpx
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from src.app.config import Settings
from src.app.integrations.token_vault import ProcessTokenVault
from src.app.main import create_app
from src.app.persistence.store import SessionRecord, Store

ORG_ID = "22222222-2222-2222-2222-222222222222"


def blueprint_user_payload(*, organization_id: str = ORG_ID) -> dict[str, object]:
    """Return the current Blueprint ``UserResponse`` contract for live-session tests."""

    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "email": "manager@example.com",
        "firstName": "Manager",
        "lastName": "Example",
        "role": "USER",
        "isActive": True,
        "emailVerifiedAt": "2026-09-19T10:00:00Z",
        "preferredLanguage": "en",
        "lastLoginAt": None,
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-09-19T10:00:00Z",
        "organization": {"id": organization_id, "name": "Example Construction"},
        "appPermissions": ["app.access"],
        "organizationPermissions": [
            {
                "organizationId": organization_id,
                "role": "MANAGER",
                "permissions": [
                    "projects.read",
                    "tasks.read",
                    "workers.read",
                    "worklogs.read",
                ],
            }
        ],
    }


def make_client() -> TestClient:
    return TestClient(create_app(Settings(data_mode="fixture", database_url="sqlite:///:memory:")))


def test_fixture_login_uses_opaque_httponly_session_and_csrf_cookie() -> None:
    with make_client() as client:
        response = client.post(
            "/api/auth/login", json={"email": "demo@example.com", "password": "demo"}
        )

        assert response.status_code == 200
        assert "access_token" not in response.text
        assert client.cookies.get("intelliq_session")
        assert client.cookies.get("intelliq_csrf")
        assert "HttpOnly" in response.headers["set-cookie"]
        assert client.get("/api/auth/me").json()["user"]["synthetic"] is True
        assert client.get("/api/auth/csrf").json()["token"] == client.cookies.get("intelliq_csrf")


def test_runtime_is_public_and_labels_fixture_mode_before_login() -> None:
    with make_client() as client:
        response = client.get("/api/runtime")

        assert response.status_code == 200
        assert response.json() == {"sourceMode": "fixture", "synthetic": True}


def test_session_database_does_not_store_upstream_tokens_and_vault_isolated() -> None:
    store = Store("sqlite:///:memory:")
    store.create_schema()
    assert {column["name"] for column in inspect(store.engine).get_columns("sessions")}.isdisjoint(
        {"upstream_access", "upstream_refresh"}
    )
    record = SessionRecord(
        "session-one",
        "user-one",
        "one@example.com",
        True,
        ["org"],
        {"org": []},
        "live",
        "csrf-one",
    )
    store.create_session(record)
    assert store.get_session("session-one") is not None
    assert not hasattr(record, "upstream_access")
    assert not hasattr(record, "upstream_refresh")

    vault = ProcessTokenVault()
    vault.put("session-one", "access-one", "refresh-one")
    vault.put("session-two", "access-two", "refresh-two")
    assert vault.get("session-one").access == "access-one"
    assert vault.get("session-two").refresh == "refresh-two"
    vault.clear("session-one")
    assert vault.get("session-one") is None
    assert vault.get("session-two") is not None


def test_mutation_requires_csrf_and_origin_is_checked() -> None:
    with make_client() as client:
        client.post("/api/auth/login", json={"email": "demo@example.com", "password": "demo"})
        assert (
            client.post(
                "/api/organizations/demo-org/analyses",
                json={"projectId": "alpha", "mode": "baseline"},
            ).status_code
            == 403
        )
        token = client.cookies.get("intelliq_csrf")
        response = client.post(
            "/api/organizations/demo-org/analyses",
            json={"projectId": "alpha", "mode": "baseline"},
            headers={"X-CSRF-Token": token or "", "Origin": "https://evil.example"},
        )
        assert response.status_code == 403


def test_org_snapshot_requires_all_four_read_permissions() -> None:
    with make_client() as client:
        client.post("/api/auth/login", json={"email": "demo@example.com", "password": "demo"})
        record = client.app.state.store.get_session(client.cookies.get("intelliq_session", ""))
        assert record is not None
        client.app.state.store.update_session_identity(
            record.session_id,
            record.user_id,
            record.email,
            True,
            record.organization_ids,
            {"demo-org": ["projects.read", "tasks.read", "workers.read"]},
        )

        response = client.get("/api/organizations/demo-org/portfolio")
        assert response.status_code == 403
        assert "worklogs.read" in response.json()["detail"]["permissions"]


def test_live_login_preserves_timecue_validation_status_without_leaking_details() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/auth/login"
        return httpx.Response(
            422,
            json={"detail": [{"loc": ["body", "email"], "msg": "value is not a valid email"}]},
        )

    settings = Settings(
        data_mode="live",
        database_url="sqlite:///:memory:",
        timecue_api_url="http://127.0.0.1:8001",
    )
    with TestClient(create_app(settings, transport=httpx.MockTransport(handler))) as client:
        response = client.post(
            "/api/auth/login",
            json={"email": "invalid@example.invalid", "password": "test-only-password"},
        )

        assert response.status_code == 422
        assert response.json()["detail"] == "timecue_authentication_failed"
        assert "value is not a valid email" not in response.text


def test_live_login_accepts_blueprint_cookies_and_denies_other_tenants() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.startswith("/api/v1/")
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(
                200,
                json={"access_token": "body-token-must-not-leak", "token_type": "bearer"},
                headers=[
                    ("set-cookie", "access_token=access-live; Path=/; HttpOnly"),
                    ("set-cookie", "refresh_token=refresh-live; Path=/; HttpOnly"),
                ],
            )
        if request.url.path.endswith("/auth/me"):
            assert "access_token=access-live" in request.headers.get("cookie", "")
            return httpx.Response(200, json=blueprint_user_payload())
        raise AssertionError(f"unexpected route {request.url.path}")

    settings = Settings(
        data_mode="live",
        database_url="sqlite:///:memory:",
        timecue_api_url="http://127.0.0.1:8001",
    )
    with TestClient(create_app(settings, transport=httpx.MockTransport(handler))) as client:
        response = client.post(
            "/api/auth/login",
            json={"email": "manager@example.com", "password": "test-only-password"},
        )

        assert response.status_code == 200
        assert "body-token-must-not-leak" not in response.text
        assert response.json()["sourceMode"] == "live"
        assert response.json()["user"]["email"] == "manager@example.com"
        assert response.json()["user"]["organizationIds"] == [ORG_ID]

        denied = client.get("/api/organizations/not-the-user-org/portfolio")
        assert denied.status_code == 403
        assert denied.json()["detail"] == "organization_access_denied"


def test_live_auth_retries_one_upstream_401_after_single_refresh() -> None:
    me_calls = 0
    refresh_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal me_calls, refresh_calls
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(
                200,
                json={"access_token": "body-token", "token_type": "bearer"},
                headers=[
                    ("set-cookie", "access_token=access-old; Path=/; HttpOnly"),
                    ("set-cookie", "refresh_token=refresh-old; Path=/; HttpOnly"),
                ],
            )
        if request.url.path.endswith("/auth/me"):
            me_calls += 1
            cookie = request.headers.get("cookie", "")
            if me_calls == 1:
                assert "access_token=access-old" in cookie
                return httpx.Response(200, json=blueprint_user_payload())
            if me_calls == 2:
                assert "access_token=access-old" in cookie
                return httpx.Response(401, json={"detail": "Not authenticated"})
            assert "access_token=access-new" in cookie
            return httpx.Response(200, json=blueprint_user_payload())
        if request.url.path.endswith("/auth/refresh"):
            refresh_calls += 1
            assert "refresh_token=refresh-old" in request.headers.get("cookie", "")
            return httpx.Response(
                200,
                json={"access_token": "body-token-new", "token_type": "bearer"},
                headers=[
                    ("set-cookie", "access_token=access-new; Path=/; HttpOnly"),
                    ("set-cookie", "refresh_token=refresh-new; Path=/; HttpOnly"),
                ],
            )
        raise AssertionError(f"unexpected route {request.url.path}")

    settings = Settings(
        data_mode="live",
        database_url="sqlite:///:memory:",
        timecue_api_url="http://127.0.0.1:8001",
    )
    with TestClient(create_app(settings, transport=httpx.MockTransport(handler))) as client:
        assert client.post(
            "/api/auth/login",
            json={"email": "manager@example.com", "password": "test-only-password"},
        ).status_code == 200

        response = client.get("/api/auth/me")

        assert response.status_code == 200
        assert me_calls == 3
        assert refresh_calls == 1
