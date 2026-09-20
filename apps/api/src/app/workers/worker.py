"""ARQ worker entry point for durable analysis jobs and daily scheduling."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

from arq import Retry, cron
from arq.connections import RedisSettings
from fastapi import HTTPException

from src.app.api.auth_service import AuthenticationFailure
from src.app.integrations.timecue import UpstreamIntegrationError
from src.app.persistence.store import Store
from src.app.services.knowledge import KnowledgeServiceError
from src.app.workers.queue import close_arq_pool, enqueue_job
from src.app.workers.scheduler import dispatch_due_schedules, workflow_organizations
from src.app.workers.service import (
    ANALYSIS_JOB_TYPE,
    JOB_KIND,
    MAX_JOB_ATTEMPTS,
    OUTBOX_KIND,
    public_job,
    save_job,
    save_outbox,
)

logger = logging.getLogger(__name__)
_ANALYSIS_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="intelliq-analysis")
_ANALYSIS_SLOT = asyncio.Semaphore(1)
RELAY_INTERVAL_SECONDS = 10
OUTBOX_LEASE_SECONDS = 60
JOB_RETENTION_SECONDS = 24 * 60 * 60
JOB_LEASE_SECONDS = 660
JOB_LEASE_RENEW_INTERVAL_SECONDS = 120
JOB_LEASE_NAME_PREFIX = "analysis-job:"
ANALYSIS_READ_PERMISSIONS = frozenset(
    {"projects.read", "tasks.read", "workers.read", "worklogs.read"}
)


class JobAuthorizationFailure(RuntimeError):
    """The revalidated upstream identity cannot read the queued organization."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(code)
        self.code = code
        self.message = message


class JobLeaseLost(RuntimeError):
    """The database no longer recognizes this worker's job lease token."""


async def startup(ctx: dict[str, Any]) -> None:
    """Start the same FastAPI lifespan used by HTTP requests.

    The worker obtains its adapter, AuthService, Store and settings from the
    application state rather than fabricating request objects or duplicating
    authentication and portfolio wiring.
    """

    from src.app.config import get_settings
    from src.app.main import create_app

    settings = get_settings()
    app = create_app(settings)
    lifespan = app.router.lifespan_context(app)
    await lifespan.__aenter__()
    ctx["app"] = app
    ctx["lifespan"] = lifespan


async def shutdown(ctx: dict[str, Any]) -> None:
    """Close the application lifespan and the process-local ARQ pool."""

    await close_arq_pool()
    lifespan = ctx.get("lifespan")
    if lifespan is not None:
        await lifespan.__aexit__(None, None, None)


async def relay_outbox(ctx: dict[str, Any]) -> int:
    """Dispatch pending durable outbox rows to Redis using deterministic ids."""

    app = ctx["app"]
    settings = app.state.settings
    if not settings.jobs_enabled:
        return 0
    store: Store = app.state.store
    now = datetime.now(UTC)
    dispatched = 0
    for organization_id in workflow_organizations(store, OUTBOX_KIND):
        for outbox in store.list_records(organization_id, OUTBOX_KIND):
            if outbox.get("status") not in {"pending", "retrying"}:
                continue
            if _parse_datetime(outbox.get("nextAttemptAt")) > now:
                continue
            outbox_id = str(outbox.get("id", ""))
            lease_name = f"outbox:{outbox_id}"
            lease = store.acquire_lease(organization_id, lease_name, seconds=OUTBOX_LEASE_SECONDS)
            if lease is None:
                continue
            try:
                job_id = str(outbox.get("jobId", ""))
                if outbox.get("topic") not in {ANALYSIS_JOB_TYPE, "knowledge_import"} or not job_id:
                    save_outbox(
                        store,
                        organization_id,
                        outbox,
                        status="failed",
                        error={"code": "unsupported_outbox_topic", "retryable": False},
                    )
                    continue
                try:
                    await enqueue_job(
                        settings.redis_url,
                        "run_analysis_job",
                        organization_id,
                        job_id,
                        _job_id=f"intelliq:{organization_id}:{job_id}",
                        _expires=JOB_RETENTION_SECONDS,
                    )
                except Exception:
                    attempts = int(outbox.get("attempts", 0)) + 1
                    delay = min(300, 2 ** min(attempts, 8))
                    retry_at = now + timedelta(seconds=delay)
                    save_outbox(
                        store,
                        organization_id,
                        outbox,
                        status="retrying",
                        attempts=attempts,
                        nextAttemptAt=retry_at.isoformat(),
                        error={"code": "redis_dispatch_failed", "retryable": True},
                    )
                    continue

                save_outbox(
                    store,
                    organization_id,
                    outbox,
                    status="dispatched",
                    dispatchedAt=now.isoformat(),
                    attempts=int(outbox.get("attempts", 0)) + 1,
                )
                job = store.get_record(organization_id, JOB_KIND, job_id)
                if job is not None:
                    save_job(store, organization_id, job, dispatchStatus="dispatched")
                dispatched += 1
            finally:
                store.release_lease(organization_id, lease_name, lease)
    return dispatched


async def dispatch_daily_jobs(ctx: dict[str, Any]) -> int:
    """Run the organization-local 05:50 scheduler once per minute."""

    app = ctx["app"]
    if not app.state.settings.jobs_enabled:
        return 0
    jobs = await asyncio.to_thread(
        dispatch_due_schedules,
        app.state.store,
        now=datetime.now(UTC),
    )
    return len(jobs)


async def run_analysis_job(
    ctx: dict[str, Any], organization_id: str, job_id: str
) -> dict[str, Any] | None:
    """Claim, authorize and execute one durable portfolio job.

    ARQ delivery is at-least-once, so the database lease is acquired before
    changing the job to running. The lease also lets a later retry recover a
    job left in running after a worker process terminated.
    """

    app = ctx["app"]
    store: Store = app.state.store
    job = store.get_record(organization_id, JOB_KIND, job_id)
    if job is None:
        return None
    if job.get("status") in {"completed", "needs_inputs", "failed", "expired"}:
        return public_job(job)

    lease_name = f"{JOB_LEASE_NAME_PREFIX}{job_id}"
    lease_token = store.acquire_lease(
        organization_id,
        lease_name,
        seconds=JOB_LEASE_SECONDS,
    )
    if lease_token is None:
        current = store.get_record(organization_id, JOB_KIND, job_id)
        return public_job(current or job)

    lease_lost = asyncio.Event()
    lease_renewal_task = asyncio.create_task(
        _renew_job_lease(store, organization_id, lease_name, lease_token, lease_lost)
    )
    try:
        job = store.get_record(organization_id, JOB_KIND, job_id)
        if job is None:
            return None
        if job.get("status") in {"completed", "needs_inputs", "failed", "expired"}:
            return public_job(job)

        attempt = int(job.get("attempts", 0)) + 1
        job = save_job(
            store,
            organization_id,
            job,
            status="running",
            stage="authenticating",
            progress=5,
            attempts=attempt,
            startedAt=datetime.now(UTC).isoformat(),
        )

        session_id = job.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            return public_job(
                save_job(
                    store,
                    organization_id,
                    job,
                    status="expired",
                    error={
                        "code": "reauthentication_required",
                        "message": "The scheduled session reference is missing.",
                        "retryable": False,
                        "requiredAction": "reauthenticate",
                    },
                )
            )

        session = store.get_session(session_id)
        if session is None:
            return public_job(
                save_job(
                    store,
                    organization_id,
                    job,
                    status="expired",
                    error={
                        "code": "reauthentication_required",
                        "message": "The scheduled session has expired.",
                        "retryable": False,
                        "requiredAction": "reauthenticate",
                    },
                )
            )

        authenticated = await _run_bounded(app.state.auth.current, session)
        job = save_job(store, organization_id, job, stage="authorizing", progress=10)
        try:
            _require_job_access(authenticated, organization_id)
        except JobAuthorizationFailure as exc:
            return public_job(
                save_job(
                    store,
                    organization_id,
                    job,
                    status="failed",
                    stage="failed",
                    error={
                        "code": exc.code,
                        "message": exc.message,
                        "retryable": False,
                    },
                    completedAt=datetime.now(UTC).isoformat(),
                )
            )

        if lease_lost.is_set():
            raise JobLeaseLost
        job = save_job(
            store,
            organization_id,
            job,
            stage="reading_document"
            if job.get("type") == "knowledge_import"
            else "reading_timecue",
            progress=15,
        )
        from src.app.services.portfolio import run_analysis
        from src.app.services.knowledge_jobs import run_knowledge_import

        def report_phase(
            stage: str, progress: int, simulation_progress: dict[str, Any] | None = None
        ) -> None:
            """Persist actual phase milestones, not simulated sample completion."""
            if lease_lost.is_set():
                raise JobLeaseLost
            current = store.get_record(organization_id, JOB_KIND, job_id)
            if current is None or current.get("status") != "running":
                raise JobLeaseLost
            extra = (
                {"simulationProgress": simulation_progress}
                if simulation_progress is not None
                else {}
            )
            save_job(store, organization_id, current, stage=stage, progress=progress, **extra)

        request_body = _job_request_body(job, job_id)
        result = await _run_bounded(
            run_knowledge_import if job.get("type") == "knowledge_import" else run_analysis,
            app.state,
            organization_id,
            authenticated,
            request_body,
            report_phase,
        )
        if lease_lost.is_set():
            raise JobLeaseLost
        result_payload = _json_value(result)
        result_status = result_payload.get("status") if isinstance(result_payload, dict) else None
        terminal_status = "needs_inputs" if result_status == "needs_inputs" else "completed"
        result_ref = {
            "analysisId": result_payload.get("id") if isinstance(result_payload, dict) else None,
            "status": result_status,
        }
        if job.get("type") == "knowledge_import" and isinstance(result_payload, dict):
            result_ref = result_payload
        if result_status == "needs_inputs" and isinstance(result_payload, dict):
            result_ref["readinessIssues"] = result_payload.get("readinessIssues", [])
        completed_job = save_job(
            store,
            organization_id,
            job,
            status=terminal_status,
            stage="completed",
            progress=100,
            result=result_ref,
            completedAt=datetime.now(UTC).isoformat(),
        )
        return public_job(completed_job)
    except JobLeaseLost:
        logger.warning("analysis job lease lost", extra={"organization_id": organization_id})
        raise Retry(defer=5) from None
    except AuthenticationFailure as exc:
        return public_job(
            save_job(
                store,
                organization_id,
                job,
                status="expired" if exc.status_code in {401, 403} else "failed",
                error={
                    "code": exc.code,
                    "message": (
                        "Reauthentication is required."
                        if exc.status_code in {401, 403}
                        else "The analysis could not authenticate."
                    ),
                    "retryable": False,
                    "requiredAction": ("reauthenticate" if exc.status_code in {401, 403} else None),
                },
                completedAt=datetime.now(UTC).isoformat(),
            )
        )
    except UpstreamIntegrationError as exc:
        if exc.status_code in {401, 403}:
            return public_job(
                save_job(
                    store,
                    organization_id,
                    job,
                    status="expired",
                    error={
                        "code": "reauthentication_required",
                        "message": "The upstream session must be renewed.",
                        "retryable": False,
                        "requiredAction": "reauthenticate",
                    },
                    completedAt=datetime.now(UTC).isoformat(),
                )
            )
        return await _retry_or_fail(store, organization_id, job, "upstream_unavailable")
    except KnowledgeServiceError as exc:
        return public_job(
            save_job(
                store,
                organization_id,
                job,
                status="failed",
                stage="failed",
                error={"code": exc.code, "message": exc.message, "retryable": False},
                completedAt=datetime.now(UTC).isoformat(),
            )
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        return public_job(
            save_job(
                store,
                organization_id,
                job,
                status="failed",
                error={
                    "code": "simulation_time_limit"
                    if exc.detail == "simulation_time_limit"
                    else detail.get("code", "analysis_rejected"),
                    "message": "The simulation reached its time limit. Your current plan is unchanged."
                    if exc.detail == "simulation_time_limit"
                    else detail.get(
                        "message", "The portfolio request was rejected by the shared pipeline."
                    ),
                    "retryable": False,
                },
                completedAt=datetime.now(UTC).isoformat(),
            )
        )
    except Exception:
        logger.exception("analysis job failed", extra={"organization_id": organization_id})
        return await _retry_or_fail(store, organization_id, job, "analysis_failed")
    finally:
        lease_renewal_task.cancel()
        try:
            await lease_renewal_task
        except asyncio.CancelledError:
            pass
        store.release_lease(organization_id, lease_name, lease_token)


async def _retry_or_fail(
    store: Store,
    organization_id: str,
    job: dict[str, Any],
    code: str,
) -> dict[str, Any] | None:
    attempts = int(job.get("attempts", 0))
    if attempts < MAX_JOB_ATTEMPTS:
        retry_at = datetime.now(UTC) + timedelta(seconds=min(60, 2**attempts))
        save_job(
            store,
            organization_id,
            job,
            status="retrying",
            stage="retrying",
            error={
                "code": code,
                "message": "A transient worker failure occurred.",
                "retryable": True,
            },
            nextAttemptAt=retry_at.isoformat(),
        )
        raise Retry(defer=min(60, 2**attempts))
    failed = save_job(
        store,
        organization_id,
        job,
        status="failed",
        stage="failed",
        error={
            "code": code,
            "message": "The worker exhausted its retry budget.",
            "retryable": False,
        },
        completedAt=datetime.now(UTC).isoformat(),
    )
    return public_job(failed)


async def _renew_job_lease(
    store: Store,
    organization_id: str,
    lease_name: str,
    lease_token: str,
    lease_lost: asyncio.Event,
) -> None:
    """Keep a long analysis claim alive without holding a SQL transaction."""

    while True:
        await asyncio.sleep(JOB_LEASE_RENEW_INTERVAL_SECONDS)
        try:
            renewed = await asyncio.to_thread(
                store.renew_lease, organization_id, lease_name, lease_token
            )
        except Exception:
            logger.exception(
                "analysis job lease renewal failed",
                extra={"organization_id": organization_id},
            )
            continue
        if not renewed:
            lease_lost.set()
            return


def _require_job_access(authenticated: Any, organization_id: str) -> None:
    """Recheck tenant membership and read permissions outside FastAPI DI."""

    user = getattr(authenticated, "user", None)
    if user is None:
        raise JobAuthorizationFailure(
            "organization_access_denied",
            "The signed-in account cannot access this organization.",
        )
    if bool(getattr(user, "app_admin", False)):
        return
    organization_ids = getattr(user, "organization_ids", ())
    if organization_id not in organization_ids:
        raise JobAuthorizationFailure(
            "organization_access_denied",
            "The signed-in account is no longer a member of this organization.",
        )
    permissions = getattr(user, "organization_permissions", {})
    granted = (
        set(permissions.get(organization_id, ())) if isinstance(permissions, Mapping) else set()
    )
    if ANALYSIS_READ_PERMISSIONS.difference(granted):
        raise JobAuthorizationFailure(
            "missing_permissions",
            "The signed-in account no longer has the required organization read permissions.",
        )


def _job_request_body(job: Mapping[str, Any], job_id: str) -> dict[str, Any]:
    """Restore retry identity fields that are stored beside the request body."""

    raw_body = job.get("requestBody")
    body = dict(raw_body) if isinstance(raw_body, Mapping) else {}
    idempotency_key = job.get("idempotencyKey")
    if isinstance(idempotency_key, str) and idempotency_key:
        body["idempotencyKey"] = idempotency_key
    body["jobId"] = job_id
    return body


async def _run_bounded(function: Any, *args: Any) -> Any:
    """Run synchronous auth/simulation code with one cancellation-safe slot.

    ``run_in_executor`` does not cancel Python work already running in a
    thread. Holding the slot until that future finishes prevents repeated ARQ
    cancellations from building an unbounded executor queue.
    """

    await _ANALYSIS_SLOT.acquire()
    loop = asyncio.get_running_loop()
    future = loop.run_in_executor(_ANALYSIS_EXECUTOR, lambda: function(*args))
    try:
        result = await asyncio.shield(future)
    except asyncio.CancelledError:
        await asyncio.shield(future)
        raise
    finally:
        _ANALYSIS_SLOT.release()
    return await result if inspect.isawaitable(result) else result


def _parse_datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        return datetime.min.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _json_value(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value


class WorkerSettings:
    """ARQ configuration loaded from the same API environment."""

    from src.app.config import get_settings

    _settings = get_settings()
    functions = [run_analysis_job]
    cron_jobs = [
        cron(relay_outbox, second={0, 10, 20, 30, 40, 50}, run_at_startup=True),
        cron(dispatch_daily_jobs, minute=set(range(60)), run_at_startup=True),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
    max_jobs = 1
    job_timeout = 600


__all__ = [
    "WorkerSettings",
    "dispatch_daily_jobs",
    "relay_outbox",
    "run_analysis_job",
    "shutdown",
    "startup",
]
