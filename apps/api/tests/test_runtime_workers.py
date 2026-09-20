"""Focused checks for encrypted sessions, durable jobs and daily scheduling."""

import asyncio
import threading
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.pool import StaticPool

from src.app.integrations.token_vault import (
    DurableTokenVault,
    TimecueConnectionRow,
    TokenVaultBase,
)
from src.app.persistence.store import SessionRecord, Store
from src.app.workers.scheduler import dispatch_due_schedules
from src.app.workers.service import enqueue_analysis
from src.app.workers.worker import _run_bounded, run_analysis_job


def _store() -> Store:
    store = Store("sqlite:///:memory:")
    store.migrate_schema()
    return store


def _vault() -> DurableTokenVault:
    from sqlalchemy import create_engine

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TokenVaultBase.metadata.create_all(engine)
    return DurableTokenVault(
        "sqlite:///:memory:", Fernet.generate_key().decode("ascii"), engine=engine
    )


def _session(
    store: Store,
    session_id: str,
    organization_id: str,
) -> SessionRecord:
    record = SessionRecord(
        session_id,
        "user-1",
        "worker@example.com",
        True,
        [organization_id],
        {organization_id: []},
        "live",
        "csrf-1",
    )
    store.create_session(record)
    return record


def test_durable_vault_encrypts_round_trip_and_clear() -> None:
    vault = _vault()
    vault.put(
        "session-1",
        "access-cookie",
        "refresh-cookie",
        user_id="user-1",
        organization_id="org-1",
    )

    assert vault.get("session-1") is not None
    assert vault.get("session-1").access == "access-cookie"
    with vault.engine.connect() as connection:
        row = connection.execute(
            select(TimecueConnectionRow.encrypted_access, TimecueConnectionRow.encrypted_refresh)
        ).one()
    assert "access-cookie" not in str(row.encrypted_access)
    assert "refresh-cookie" not in str(row.encrypted_refresh)

    vault.clear("session-1")
    assert vault.get("session-1") is None


def test_durable_vault_rejects_missing_key() -> None:
    with pytest.raises(ValueError, match="SESSION_ENCRYPTION_KEY"):
        DurableTokenVault("sqlite:///:memory:", "")


def test_enqueue_analysis_commits_job_and_outbox_without_credentials() -> None:
    store = _store()
    job = enqueue_analysis(
        store,
        "org-1",
        "session-1",
        {"projectId": "project-1", "access_token": "must-not-persist"},
    )

    assert job["status"] == "queued"
    assert "sessionId" not in job
    stored = store.get_record("org-1", "job", str(job["id"]))
    assert stored is not None
    assert stored["sessionId"] == "session-1"
    assert "access_token" not in stored["requestBody"]
    outbox = store.get_record("org-1", "outbox", f"job:{job['id']}")
    assert outbox is not None
    assert outbox["status"] == "pending"


def test_daily_scheduler_uses_local_date_and_deduplicates() -> None:
    store = _store()
    store.save_record(
        "org-1",
        "schedule",
        "daily",
        {
            "timezone": "Europe/Warsaw",
            "enabled": True,
            "analysisTime": "06:00",
            "sessionId": "session-1",
        },
    )
    now = datetime(2026, 9, 20, 3, 50, tzinfo=UTC)

    first = dispatch_due_schedules(store, now=now)
    second = dispatch_due_schedules(store, now=now + timedelta(minutes=1))

    assert len(first) == 1
    assert second == []
    schedule = store.get_record("org-1", "schedule", "daily")
    assert schedule is not None
    assert schedule["lastRunLocalDate"] == "2026-09-20"


def test_worker_marks_missing_scheduled_session_expired() -> None:
    store = _store()
    job = enqueue_analysis(store, "org-1", "missing-session", {"mode": "recovery"})
    app = SimpleNamespace(state=SimpleNamespace(store=store, settings=SimpleNamespace()))

    result = asyncio.run(run_analysis_job({"app": app}, "org-1", str(job["id"])))

    assert result is not None
    assert result["status"] == "expired"
    assert result["error"]["code"] == "reauthentication_required"


@pytest.mark.parametrize(
   ("organization_ids", "permissions", "error_code"),
   [
        (
            [],
            {"org-1": ["projects.read", "tasks.read", "workers.read", "worklogs.read"]},
            "organization_access_denied",
        ),
        (
            ["org-1"],
            {"org-1": ["projects.read", "tasks.read", "workers.read"]},
            "missing_permissions",
        ),
   ],
)
def test_worker_rechecks_membership_and_all_read_permissions(
    organization_ids: list[str],
    permissions: dict[str, list[str]],
    error_code: str,
) -> None:
    store = _store()
    record = _session(store, "session-1", "org-1")
    job = enqueue_analysis(store, "org-1", record.session_id, {"mode": "recovery"})
    authenticated = SimpleNamespace(
        record=record,
        user=SimpleNamespace(
            app_admin=False,
            organization_ids=organization_ids,
            organization_permissions=permissions,
        ),
    )
    app = SimpleNamespace(
        state=SimpleNamespace(
            store=store,
            auth=SimpleNamespace(current=lambda _: authenticated),
            settings=SimpleNamespace(),
        )
    )

    result = asyncio.run(run_analysis_job({"app": app}, "org-1", str(job["id"])))

    assert result is not None
    assert result["status"] == "failed"
    assert result["error"]["code"] == error_code
    stored = store.get_record("org-1", "job", str(job["id"]))
    assert stored is not None
    assert stored["status"] == "failed"


def test_worker_does_not_start_when_another_delivery_holds_job_lease() -> None:
    store = _store()
    job = enqueue_analysis(store, "org-1", "session-1", {"mode": "recovery"})
    lease_name = f"analysis-job:{job['id']}"
    lease = store.acquire_lease("org-1", lease_name)
    assert lease is not None
    app = SimpleNamespace(state=SimpleNamespace(store=store, settings=SimpleNamespace()))

    try:
        result = asyncio.run(run_analysis_job({"app": app}, "org-1", str(job["id"])))
    finally:
        store.release_lease("org-1", lease_name, lease)

    assert result is not None
    assert result["status"] == "queued"
    stored = store.get_record("org-1", "job", str(job["id"]))
    assert stored is not None
    assert stored["status"] == "queued"


def test_worker_restores_job_identity_fields_for_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store()
    record = _session(store, "session-1", "org-1")
    job = enqueue_analysis(
        store,
        "org-1",
        record.session_id,
        {"mode": "recovery", "projectId": "project-1"},
    )
    authenticated = SimpleNamespace(
        record=record,
        user=SimpleNamespace(
            app_admin=False,
            organization_ids=["org-1"],
            organization_permissions={
                "org-1": ["projects.read", "tasks.read", "workers.read", "worklogs.read"]
            },
        ),
    )
    request_bodies: list[dict[str, object]] = []

    def fake_run_analysis(
        state: object, org_id: str, session: object, body: dict, progress=None,
    ) -> dict:
        request_bodies.append(body)
        return {"id": "analysis-1", "status": "completed"}

    monkeypatch.setattr("src.app.services.portfolio.run_analysis", fake_run_analysis)
    app = SimpleNamespace(
        state=SimpleNamespace(
            store=store,
            auth=SimpleNamespace(current=lambda _: authenticated),
            settings=SimpleNamespace(),
        )
    )

    result = asyncio.run(run_analysis_job({"app": app}, "org-1", str(job["id"])))

    assert result is not None
    assert result["status"] == "completed"
    assert request_bodies == [
        {
            "mode": "recovery",
            "projectId": "project-1",
            "idempotencyKey": job["idempotencyKey"],
            "jobId": job["id"],
        }
    ]


def test_bounded_executor_waits_for_cancelled_thread() -> None:
    started = threading.Event()
    finished = threading.Event()

    def blocking_call() -> str:
        started.set()
        time.sleep(0.03)
        finished.set()
        return "done"

    async def exercise() -> None:
        task = asyncio.create_task(_run_bounded(blocking_call))
        assert await asyncio.to_thread(started.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert finished.is_set()
