"""Durable API-facing job records for portfolio analysis requests.

The API commits a job and an outbox record before Redis dispatch. This closes
the database/queue gap: a Redis outage leaves a visible queued job that the
worker relay can dispatch after restart. Only an opaque local session id is
stored; upstream cookies never enter the job payload.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from src.app.persistence.store import Store

JOB_KIND = "job"
OUTBOX_KIND = "outbox"
ANALYSIS_JOB_TYPE = "analysis"
MAX_JOB_ATTEMPTS = 3
_SENSITIVE_KEY = re.compile(
    r"(?:token|secret|password|authorization|cookie|api[_-]?key|credential)", re.IGNORECASE
)


def enqueue_analysis(
    store: Store,
    org_id: str,
    session_id: str,
    request_body: Mapping[str, Any] | dict[str, Any],
    *,
    job_type: str = ANALYSIS_JOB_TYPE,
) -> dict[str, Any]:
    """Persist one idempotent analysis job and its pending outbox dispatch.

    This function is intentionally synchronous because the current FastAPI
    analysis route is synchronous. Redis dispatch is performed by the ARQ
    outbox relay, so the request does not block on an unavailable broker.
    """

    safe_body = _safe_json(dict(request_body))
    idempotency_key = str(safe_body.get("idempotencyKey") or uuid4())
    for prior in store.list_records(org_id, JOB_KIND):
        if prior.get("type") == job_type and prior.get("idempotencyKey") == idempotency_key:
            return public_job(prior)

    now = datetime.now(UTC).isoformat()
    job_id = str(uuid4())
    records = store.save_records(
        org_id,
        [
            (
                JOB_KIND,
                job_id,
                {
                    "type": job_type,
                    "sessionId": session_id,
                    "requestBody": safe_body,
                    "idempotencyKey": idempotency_key,
                    "status": "queued",
                    "stage": "queued",
                    "progress": 0,
                    "attempts": 0,
                    "maxAttempts": MAX_JOB_ATTEMPTS,
                    "dispatchStatus": "pending",
                    "queuedAt": now,
                },
            ),
            (
                OUTBOX_KIND,
                f"job:{job_id}",
                {
                    "topic": job_type,
                    "jobId": job_id,
                    "sessionId": session_id,
                    "status": "pending",
                    "attempts": 0,
                    "nextAttemptAt": now,
                },
            ),
        ],
    )
    job = records[0]
    return public_job(job)


def enqueue_daily_analysis(
    store: Store,
    organization_id: str,
    schedule: Mapping[str, Any],
    local_date: str,
) -> dict[str, Any] | None:
    """Create the once-per-local-date analysis job for a schedule record."""

    session_id = schedule.get("sessionId")
    if not isinstance(session_id, str) or not session_id:
        return None
    request_body = schedule.get("requestBody")
    if not isinstance(request_body, Mapping):
        request_body = {
            "projectId": schedule.get("projectId"),
            "mode": "recovery",
        }
    body = {
        **dict(request_body),
        "trigger": "daily",
        "scheduledLocalDate": local_date,
        "idempotencyKey": f"daily:{organization_id}:{local_date}",
    }
    return enqueue_analysis(store, organization_id, session_id, body)


def public_job(job: Mapping[str, Any]) -> dict[str, Any]:
    """Return a job response without the stored session reference."""

    return {key: value for key, value in job.items() if key != "sessionId"}


def save_job(
    store: Store, organization_id: str, job: Mapping[str, Any], **changes: Any
) -> dict[str, Any]:
    """Persist a status transition while retaining the durable job envelope."""

    current = store.get_record(organization_id, JOB_KIND, str(job["id"])) or {}
    return store.save_record(
        organization_id, JOB_KIND, str(job["id"]), {**dict(job), **current, **changes}
    )


def save_outbox(
    store: Store, organization_id: str, outbox: Mapping[str, Any], **changes: Any
) -> dict[str, Any]:
    """Persist an outbox dispatch attempt without exposing payload secrets."""

    return store.save_record(
        organization_id,
        OUTBOX_KIND,
        str(outbox["id"]),
        {**dict(outbox), **changes},
    )


def _safe_json(value: Any) -> Any:
    """Keep job JSON serializable while dropping credential-shaped fields."""

    if isinstance(value, Mapping):
        return {
            str(key): _safe_json(item)
            for key, item in value.items()
            if not _SENSITIVE_KEY.search(str(key))
        }
    if isinstance(value, list):
        return [_safe_json(item) for item in value]
    if isinstance(value, tuple):
        return [_safe_json(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


__all__ = [
    "ANALYSIS_JOB_TYPE",
    "JOB_KIND",
    "MAX_JOB_ATTEMPTS",
    "OUTBOX_KIND",
    "enqueue_analysis",
    "enqueue_daily_analysis",
    "public_job",
    "save_job",
    "save_outbox",
]
