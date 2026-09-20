"""Organization-local daily portfolio scheduling and restart catch-up."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text

from src.app.persistence.store import Store
from src.app.workers.service import enqueue_daily_analysis

SCHEDULE_KIND = "schedule"
DAILY_SCHEDULE_ID = "daily"
SCHEDULE_LEASE_SECONDS = 300
DISPATCH_MINUTES_BEFORE_PUBLICATION = 10
DEFAULT_TIMEZONE = "Europe/Warsaw"
DEFAULT_ANALYSIS_TIME = "06:00"


def dispatch_due_schedules(
    store: Store,
    *,
    organization_ids: Iterable[str] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Enqueue at most one daily job per organization and local calendar date.

    The schedule record is the v4 API contract: ``kind='schedule'``,
    ``id='daily'``, ``timezone``, ``enabled``, ``analysisTime`` and ``sessionId``.
    A portable Store lease prevents duplicate dispatch across worker processes;
    the job idempotency key remains the second line of defence after a crash
    between enqueue and schedule checkpointing.
    """

    current = _as_utc(now or datetime.now(UTC))
    ids = list(organization_ids) if organization_ids is not None else workflow_organizations(
        store, SCHEDULE_KIND
    )
    jobs: list[dict[str, Any]] = []
    for organization_id in ids:
        schedule = store.get_record(organization_id, SCHEDULE_KIND, DAILY_SCHEDULE_ID)
        if schedule is None or not bool(schedule.get("enabled", False)):
            continue
        local_date = due_local_date(schedule, current)
        if local_date is None:
            continue
        lease_name = f"daily:{local_date.isoformat()}"
        lease = store.acquire_lease(
            organization_id,
            lease_name,
            seconds=SCHEDULE_LEASE_SECONDS,
        )
        if lease is None:
            continue
        try:
            job = enqueue_daily_analysis(
                store,
                organization_id,
                schedule,
                local_date.isoformat(),
            )
            checkpoint = {
                **schedule,
                "lastRunLocalDate": local_date.isoformat(),
                "nextRunAt": next_dispatch_at(schedule, current).isoformat(),
                "lastDispatchAt": current.isoformat(),
            }
            if job is None:
                checkpoint["lastError"] = "scheduled_session_missing"
            else:
                checkpoint.pop("lastError", None)
                checkpoint["lastJobId"] = job.get("id")
                jobs.append(job)
            store.save_record(
                organization_id,
                SCHEDULE_KIND,
                DAILY_SCHEDULE_ID,
                checkpoint,
            )
        finally:
            store.release_lease(organization_id, lease_name, lease)
    return jobs


def due_local_date(schedule: Mapping[str, Any], now: datetime) -> date | None:
    """Return today's local date when the publication window is due.

    Comparing in the configured IANA timezone means DST changes and a worker
    restart after 05:50 still produce one catch-up job for the current local
    date, rather than replaying every missed UTC interval.
    """

    timezone = _schedule_timezone(schedule)
    local_now = _as_utc(now).astimezone(timezone)
    local_date = local_now.date()
    if str(schedule.get("lastRunLocalDate", "")) == local_date.isoformat():
        return None
    dispatch_time = _dispatch_time(schedule)
    if local_now >= datetime.combine(local_date, dispatch_time, tzinfo=timezone):
        return local_date
    return None


def next_dispatch_at(schedule: Mapping[str, Any], now: datetime) -> datetime:
    """Calculate the next 05:50-style local dispatch as an absolute instant."""

    timezone = _schedule_timezone(schedule)
    local_now = _as_utc(now).astimezone(timezone)
    candidate = datetime.combine(local_now.date(), _dispatch_time(schedule), tzinfo=timezone)
    if candidate <= local_now:
        candidate += timedelta(days=1)
    return candidate.astimezone(UTC)


def _dispatch_time(schedule: Mapping[str, Any]) -> time:
    raw = str(schedule.get("analysisTime") or DEFAULT_ANALYSIS_TIME)
    try:
        publication = time.fromisoformat(raw)
    except ValueError:
        publication = time.fromisoformat(DEFAULT_ANALYSIS_TIME)
    publication_minutes = publication.hour * 60 + publication.minute
    dispatch_minutes = max(0, publication_minutes - DISPATCH_MINUTES_BEFORE_PUBLICATION)
    return time(hour=dispatch_minutes // 60, minute=dispatch_minutes % 60)


def _schedule_timezone(schedule: Mapping[str, Any]) -> ZoneInfo:
    name = str(schedule.get("timezone") or DEFAULT_TIMEZONE)
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_TIMEZONE)


def workflow_organizations(store: Store, kind: str) -> list[str]:
    """Discover tenants with a workflow record kind through the existing table.

    Store intentionally exposes tenant-scoped record methods. The scheduler is
    the one cross-tenant internal process and uses a read-only named projection
    solely to discover organization ids before returning to that Store seam.
    """

    with store.engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT DISTINCT organization_id "
                "FROM workflow_records WHERE kind = :kind"
            ),
            {"kind": kind},
        )
        return [str(row.organization_id) for row in rows]


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


__all__ = [
    "DAILY_SCHEDULE_ID",
    "DEFAULT_ANALYSIS_TIME",
    "DEFAULT_TIMEZONE",
    "SCHEDULE_KIND",
    "dispatch_due_schedules",
    "due_local_date",
    "next_dispatch_at",
    "workflow_organizations",
]
