"""Per-user HTTP adapters for the existing Timecue API.

Every request creates a short-lived HTTPX client with an explicit cookie map.
There is no process-wide cookie jar and no upstream token is returned to the
browser.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from src.app.config import Settings
from src.app.domain.contracts import PortfolioSnapshot, SessionUser, SourceMode
from src.app.domain.status import archived_project_ids, is_archived_project
from src.app.integrations.token_vault import ProcessTokenVault
from src.app.persistence.store import SessionRecord

MAX_IDENTIFIER_CHARS = 256
MAX_TEXT_CHARS = 512
MAX_WORKLOG_DESCRIPTION_CHARS = 4_000
MAX_WORKLOG_DURATION_MINUTES = 7 * 24 * 60
MAX_ESTIMATED_MINUTES = 1_440
MAX_COLLECTION_ITEMS = 256
MAX_NUMERIC_VALUE = 1_000_000.0

_EMAIL_RE = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_SECRET_RE = re.compile(
    r"(?i)\b(?:access[_-]?token|refresh[_-]?token|api[_-]?key|authorization|password|secret|token)"
    r"\s*[:=]\s*[^\s,;]+|\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+"
)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")


class UpstreamIntegrationError(RuntimeError):
    """A visible failure while reading or authenticating against Timecue."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class TimecueAuthClient:
    """Authenticate through Timecue without forwarding its Token JSON."""

    def __init__(
        self,
        settings: Settings,
        transport: httpx.BaseTransport | None = None,
        vault: ProcessTokenVault | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport
        self.vault = vault if vault is not None else ProcessTokenVault()
        self.base_url = _api_base(settings.timecue_api_url)

    def login(self, email: str, password: str) -> tuple[SessionUser, str, str]:
        response = self._request("POST", "/auth/login", json={"email": email, "password": password})
        access, refresh = self._extract_auth_cookies(response)
        user = self._parse_user(
            self._request(
                "GET",
                "/auth/me",
                cookies={
                    self.settings.timecue_access_cookie: access,
                    self.settings.timecue_refresh_cookie: refresh,
                },
            ).json()
        )
        return user, access, refresh

    def refresh(self, session: SessionRecord) -> tuple[str, str]:
        tokens = self.vault.get(session.session_id)
        if tokens is None:
            raise UpstreamIntegrationError("upstream_refresh_cookie_missing", 401)
        response = self._request(
            "POST",
            "/auth/refresh",
            cookies={self.settings.timecue_refresh_cookie: tokens.refresh},
        )
        access, refresh = self._extract_auth_cookies(response)
        self.vault.put(session.session_id, access, refresh)
        return access, refresh

    def me(self, session: SessionRecord) -> SessionUser:
        tokens = self.vault.get(session.session_id)
        if tokens is None:
            raise UpstreamIntegrationError("upstream_access_cookie_missing", 401)
        response = self._request(
            "GET",
            "/auth/me",
            cookies={
                self.settings.timecue_access_cookie: tokens.access,
                self.settings.timecue_refresh_cookie: tokens.refresh,
            },
        )
        return self._parse_user(response.json())

    def logout(self, session: SessionRecord) -> None:
        tokens = self.vault.get(session.session_id)
        if tokens is None:
            return
        try:
            self._request(
                "POST",
                "/auth/logout",
                cookies={
                    self.settings.timecue_access_cookie: tokens.access,
                    self.settings.timecue_refresh_cookie: tokens.refresh,
                },
            )
        except UpstreamIntegrationError:
            # Local state is cleared by the caller even when upstream logout fails.
            return

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        cookies = kwargs.pop("cookies", None)
        try:
            with httpx.Client(
                base_url=self.base_url,
                transport=self.transport,
                timeout=10.0,
                follow_redirects=False,
                cookies=cookies,
            ) as client:
                response = client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise UpstreamIntegrationError("timecue_unreachable") from exc
        if response.status_code >= 400:
            raise UpstreamIntegrationError("timecue_authentication_failed", response.status_code)
        return response

    def _extract_auth_cookies(self, response: httpx.Response) -> tuple[str, str]:
        access = response.cookies.get(self.settings.timecue_access_cookie)
        refresh = response.cookies.get(self.settings.timecue_refresh_cookie)
        if not access or not refresh:
            raise UpstreamIntegrationError("timecue_auth_cookies_missing", 502)
        return access, refresh

    @staticmethod
    def _parse_user(payload: Mapping[str, Any]) -> SessionUser:
        permissions = _normalize_permissions(payload.get("organizationPermissions", []))
        organization_ids = [
            str(item.get("id"))
            for item in payload.get("organizations", [])
            if isinstance(item, Mapping) and item.get("id")
        ]
        organization = payload.get("organization")
        if isinstance(organization, Mapping) and organization.get("id"):
            organization_id = str(organization["id"])
            if organization_id not in organization_ids:
                organization_ids.append(organization_id)
        if payload.get("organizationId") and str(payload["organizationId"]) not in organization_ids:
            organization_ids.append(str(payload["organizationId"]))
        for organization_id in permissions:
            if organization_id not in organization_ids:
                organization_ids.append(organization_id)
        raw_id = payload.get("id") or payload.get("userId")
        raw_email = payload.get("email")
        if (
            not isinstance(raw_id, (str, int))
            or not str(raw_id).strip()
            or str(raw_id).strip().lower() == "none"
        ):
            raise UpstreamIntegrationError("timecue_identity_id_missing", 502)
        if (
            not isinstance(raw_email, str)
            or "@" not in raw_email
            or not raw_email.strip()
            or len(raw_email) > 320
        ):
            raise UpstreamIntegrationError("timecue_identity_email_invalid", 502)
        app_permissions = _permission_values(payload.get("appPermissions", []))
        app_admin = bool(payload.get("appAdmin")) or bool(
            {"app.admin", "app_admin", "app-admin", "admin"}.intersection(app_permissions)
        )
        try:
            user = SessionUser(
                id=str(raw_id).strip(),
                email=raw_email.strip(),
                emailVerifiedAt=payload.get("emailVerifiedAt"),
                organizationIds=organization_ids,
                organizationPermissions=permissions,
                appPermissions=app_permissions,
                appAdmin=app_admin,
                sourceMode=SourceMode.LIVE,
                synthetic=False,
            )
        except Exception as exc:
            raise UpstreamIntegrationError("timecue_identity_shape_invalid", 502) from exc
        return user


class LiveTimecueAdapter:
    """Compose current Timecue routes into the internal portfolio snapshot."""

    def __init__(
        self,
        settings: Settings,
        auth: TimecueAuthClient,
        refresh: Callable[[SessionRecord, str | None], None],
    ) -> None:
        self.settings = settings
        self.auth = auth
        self.refresh = refresh
        self.base_url = _api_base(settings.timecue_api_url)

    def organizations(self, session: SessionRecord) -> list[dict[str, Any]]:
        payload = self._get(session, "/organizations")
        return _sanitize_rows(_items(payload), _sanitize_organization)

    def snapshot(self, session: SessionRecord, organization_id: str) -> PortfolioSnapshot:
        started = datetime.now(UTC)
        projects = _sanitize_rows(
            _items(self._get(session, f"/organizations/{organization_id}/projects")),
            _sanitize_project,
        )
        skipped_archived_ids = archived_project_ids(projects)
        projects = [project for project in projects if not is_archived_project(project)]
        workers = self._paginate_workers(session, organization_id)
        specialties = _sanitize_rows(
            _items(self._get(session, f"/organizations/{organization_id}/workforce/specialties")),
            _sanitize_specialty,
        )
        tasks: list[dict[str, Any]] = []
        assignments: list[dict[str, Any]] = []
        for project in projects:
            project_id = str(project.get("id"))
            if not project_id:
                continue
            project_tasks = _sanitize_rows(
                _items(
                    self._get(
                        session,
                        f"/organizations/{organization_id}/projects/{project_id}/tasks",
                        params={"include_completed": "true"},
                    )
                ),
                _sanitize_task,
            )
            tasks.extend(project_tasks)
            for task in project_tasks:
                task_id = str(task.get("id"))
                if not task_id:
                    continue
                assignment_payload = self._get(
                    session,
                    f"/organizations/{organization_id}/projects/{project_id}/tasks/{task_id}/assignments",
                )
                effective = (
                    assignment_payload.get("effectiveWorkers", [])
                    if isinstance(assignment_payload, Mapping)
                    else []
                )
                assignments.append(
                    {
                        "taskId": task_id,
                        "workerIds": [
                            str(worker.get("id") or worker.get("workerProfileId"))
                            for worker in effective
                            if isinstance(worker, Mapping)
                            and (worker.get("id") or worker.get("workerProfileId"))
                        ],
                    }
                )

        worklogs = [
            worklog
            for worklog in self._paginate_worklogs(session, organization_id)
            if str(worklog.get("projectId") or "") not in skipped_archived_ids
        ]
        reservations, reservation_warning = self._calendar_reservations(
            session, organization_id, projects, workers
        )
        warnings = ["snapshot_composed_from_multiple_timecue_reads"]
        if reservation_warning:
            warnings.append(reservation_warning)
        assigned = {item["taskId"]: item["workerIds"] for item in assignments}
        for task in tasks:
            task["assignedWorkerIds"] = assigned.get(task["id"], [])
        source_revision = _revision(projects, tasks, workers, worklogs, reservations)
        payload = {
            "schemaVersion": 1,
            "snapshotId": f"live-{organization_id}-{source_revision[:16]}",
            "sourceRevision": source_revision,
            "organizationId": organization_id,
            "fetchedAt": started.isoformat(),
            "asOf": started.isoformat(),
            "sourceMode": "live",
            "complete": not reservation_warning,
            "warnings": warnings,
            "omittedSections": ["weather"],
            "projects": projects,
            "tasks": tasks,
            "workers": workers,
            "specialties": specialties,
            "effectiveAssignments": assignments,
            "worklogs": worklogs,
            "worklogTotals": _worklog_totals(worklogs),
            "externalReservations": reservations,
            "transferAssumptions": [],
        }
        return PortfolioSnapshot.model_validate(payload)

    def _paginate_workers(
        self, session: SessionRecord, organization_id: str
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        offset = 0
        while True:
            payload = self._get(
                session,
                f"/organizations/{organization_id}/workforce/workers",
                params={"offset": offset, "limit": 100},
            )
            page = _items(payload)
            items.extend(_sanitize_rows(page, _sanitize_worker))
            next_offset = payload.get("nextOffset") if isinstance(payload, Mapping) else None
            if next_offset is None:
                break
            if not page or not isinstance(next_offset, int) or next_offset <= offset:
                raise UpstreamIntegrationError("timecue_worker_pagination_invalid", 502)
            offset = next_offset
        return items

    def _paginate_worklogs(
        self, session: SessionRecord, organization_id: str
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, str | int] = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            payload = self._get(
                session, f"/organizations/{organization_id}/worklogs", params=params
            )
            page = _items(payload)
            items.extend(_sanitize_rows(page, _sanitize_worklog))
            next_cursor = payload.get("nextCursor") if isinstance(payload, Mapping) else None
            if not next_cursor:
                break
            if not page or not isinstance(next_cursor, str) or next_cursor == cursor:
                raise UpstreamIntegrationError("timecue_worklog_pagination_invalid", 502)
            cursor = next_cursor
        return items

    def _calendar_reservations(
        self,
        session: SessionRecord,
        organization_id: str,
        projects: list[dict[str, Any]],
        workers: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], str | None]:
        # Calendar records outside the selected projects can consume donor capacity.
        # The adapter keeps them visible but marks the snapshot incomplete unless all
        # team assignments were expanded by the upstream response.
        if not projects:
            return [], "calendar_reservations_unavailable"
        start_at = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        end_at = start_at + timedelta(days=56)
        # Timecue caps each calendar request at 42 local days. Smaller windows
        # leave room for DST shifts and retain the complete 56-day horizon.
        items: list[Any] = []
        seen: set[tuple[str, str]] = set()
        window_start = start_at
        while window_start < end_at:
            window_end = min(window_start + timedelta(days=28), end_at)
            payload = self._get(
                session,
                f"/organizations/{organization_id}/calendar",
                params={
                    "rangeStart": window_start.isoformat(),
                    "rangeEnd": window_end.isoformat(),
                    "projectless": "true",
                },
            )
            if not isinstance(payload, Mapping) or not isinstance(payload.get("items"), list):
                raise UpstreamIntegrationError("timecue_calendar_response_shape_invalid", 502)
            for item in payload["items"]:
                if isinstance(item, Mapping) and item.get("id"):
                    key = (str(item.get("recordType", "")), str(item["id"]))
                    if key in seen:
                        continue
                    seen.add(key)
                items.append(item)
            window_start = window_end
        reservations: list[dict[str, Any]] = []
        unresolved = False
        project_ids = {str(project.get("id")) for project in projects}
        worker_ids = {
            str(worker.get("id") or worker.get("workerProfileId"))
            for worker in workers
            if worker.get("id") or worker.get("workerProfileId")
        }
        for item in items:
            if not isinstance(item, Mapping):
                continue
            assignments = item.get("assignments", [])
            expanded = False
            if isinstance(assignments, list):
                for assignment in assignments:
                    if not isinstance(assignment, Mapping):
                        continue
                    source_type = assignment.get("sourceType")
                    source_id = assignment.get("sourceId")
                    if source_type == "worker" and source_id:
                        worker_id = str(source_id)
                        if worker_id not in worker_ids:
                            unresolved = True
                            continue
                        reservation = _sanitize_reservation(item)
                        reservation["workerId"] = worker_id
                        reservations.append(reservation)
                        expanded = True
                    elif source_type == "team":
                        unresolved = True
                    elif assignment.get("teamId") or assignment.get("team_id"):
                        unresolved = True
            direct_worker_id = item.get("workerProfileId") or item.get("workerId")
            if direct_worker_id and not expanded:
                reservation = _sanitize_reservation(item)
                worker_id = _source_id({"workerId": direct_worker_id}, "workerId")
                if worker_id is not None:
                    reservation["workerId"] = worker_id
                reservations.append(reservation)
                expanded = True
            if not expanded and (item.get("projectId") or item.get("taskId")):
                reservations.append(_sanitize_reservation(item))
            if (
                str(item.get("projectId")) not in project_ids
                and isinstance(assignments, list)
                and any(
                    isinstance(assignment, Mapping)
                    and (
                        assignment.get("sourceType") == "team"
                        or assignment.get("teamId")
                        or assignment.get("team_id")
                    )
                    for assignment in assignments
                )
            ):
                unresolved = True
        return reservations, "outside_project_team_assignments_unresolved" if unresolved else None

    def _get(
        self, session: SessionRecord, path: str, params: Mapping[str, Any] | None = None
    ) -> Mapping[str, Any] | list[Any]:
        for attempt in range(2):
            failed_access = self._tokens(session).access
            try:
                response = self._request(session, "GET", path, params=params)
            except UpstreamIntegrationError as exc:
                if exc.status_code != 401 or attempt == 1:
                    raise
                self.refresh(session, failed_access)
                continue
            payload = response.json()
            if not isinstance(payload, (Mapping, list)):
                raise UpstreamIntegrationError("timecue_response_shape_invalid", 502)
            return payload
        raise UpstreamIntegrationError("timecue_refresh_failed", 401)

    def _request(
        self, session: SessionRecord, method: str, path: str, **kwargs: Any
    ) -> httpx.Response:
        cookies = {
            self.settings.timecue_access_cookie: self._tokens(session).access,
            self.settings.timecue_refresh_cookie: self._tokens(session).refresh,
        }
        try:
            with httpx.Client(
                base_url=self.base_url,
                transport=self.auth.transport,
                timeout=10.0,
                follow_redirects=False,
                cookies=cookies,
            ) as client:
                response = client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise UpstreamIntegrationError("timecue_unreachable") from exc
        if response.status_code >= 400:
            raise UpstreamIntegrationError(
                "timecue_read_failed",
                response.status_code,
                detail=_public_error_detail(response),
            )
        return response

    def _tokens(self, session: SessionRecord):
        tokens = self.auth.vault.get(session.session_id)
        if tokens is None:
            raise UpstreamIntegrationError("upstream_access_cookie_missing", 401)
        return tokens


def _sanitize_rows(
    rows: list[dict[str, Any]], sanitizer: Callable[[Mapping[str, Any]], dict[str, Any]]
) -> list[dict[str, Any]]:
    """Apply a closed-field sanitizer before data can enter a snapshot."""

    sanitized: list[dict[str, Any]] = []
    for row in rows:
        value = sanitizer(row)
        if value:
            sanitized.append(value)
    return sanitized


def _sanitize_organization(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only the organization identity needed by the organization picker."""

    return _identity_fields(payload, include_name=True)


def _sanitize_project(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Keep planning fields while dropping client, billing, and arbitrary metadata.

    Status is retained so archived Timecue projects can be excluded from the
    snapshot before tasks, worklogs, or readiness gates see them.
    """

    result = _identity_fields(payload, include_name=True)
    location = (
        payload.get("defaultLocation")
        or payload.get("location")
        or payload.get("effectiveLocation")
    )
    if isinstance(location, Mapping):
        latitude = _safe_number(location.get("latitude"), minimum=-90, maximum=90)
        longitude = _safe_number(location.get("longitude"), minimum=-180, maximum=180)
        if latitude is not None and longitude is not None:
            result["location"] = {"latitude": latitude, "longitude": longitude}
    for key in ("status", "targetFinishAt", "timezone", "createdAt", "updatedAt"):
        value = _bounded_text(payload.get(key), MAX_TEXT_CHARS)
        if value is not None:
            result[key] = value
    priority = _safe_number(payload.get("priority"), minimum=0)
    if priority is not None:
        result["priority"] = priority
    return result


def _sanitize_specialty(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the specialty identifier and display label, never its raw metadata."""

    return _identity_fields(payload, include_name=True)


def _sanitize_worker(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return scheduling attributes and the explicit authorized display name, not contacts."""

    worker_id = _source_id(payload, "id", "workerProfileId")
    if worker_id is None:
        return {}
    result: dict[str, Any] = {"id": worker_id}
    name_parts = [
        part
        for key in ("userFirstName", "userLastName")
        if (part := _bounded_text(payload.get(key), 128))
    ]
    if name_parts:
        result["name"] = " ".join(name_parts)
    specialty_ids = _id_list(payload.get("specialtyIds"))
    if not specialty_ids:
        specialty_ids = _mapping_id_list(payload.get("specialties"))
    if specialty_ids:
        result["specialtyIds"] = specialty_ids
    _copy_id(result, payload, "homeProjectId")
    project_ids = _id_list(payload.get("projectIds", payload.get("assignedProjectIds")))
    if project_ids:
        result["projectIds"] = project_ids
    weekdays = _int_list(payload.get("workingWeekdays"), minimum=0, maximum=6)
    if weekdays:
        result["workingWeekdays"] = weekdays
    for key in ("shiftStart", "shiftEnd", "timezone"):
        value = _bounded_text(payload.get(key), MAX_TEXT_CHARS)
        if value is not None:
            result[key] = value
    for key in ("canTransfer", "active"):
        value = payload.get(key)
        if key == "active" and value is None:
            value = payload.get("isActive")
        if isinstance(value, bool):
            result[key] = value
    for key in ("createdAt", "updatedAt"):
        value = _bounded_text(payload.get(key), MAX_TEXT_CHARS)
        if value is not None:
            result[key] = value
    overtime = _sanitize_intervals(payload.get("permittedOvertimeSlots"))
    if overtime:
        result["permittedOvertimeSlots"] = overtime
    max_overtime = _safe_number(payload.get("maxOvertimeHours"), minimum=0)
    if max_overtime is not None:
        result["maxOvertimeHours"] = max_overtime
    return result


def _sanitize_task(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the task planning contract without assignee or evidence objects."""

    task_id = _source_id(payload, "id", "taskId")
    project_id = _source_id(payload, "projectId", "project_id")
    if task_id is None or project_id is None:
        return {}
    result: dict[str, Any] = {"id": task_id, "projectId": project_id}
    for key in ("title", "status", "requiredSpecialtyId"):
        value = _bounded_text(payload.get(key), MAX_TEXT_CHARS)
        if value is not None:
            result[key] = value
    predecessor_ids = _id_list(payload.get("predecessorIds"))
    if predecessor_ids:
        result["predecessorIds"] = predecessor_ids
    effort = _sanitize_effort(payload.get("remainingPersonHours"))
    if effort is not None:
        result["remainingPersonHours"] = effort
    estimated_minutes = _safe_number(
        payload.get("estimatedMinutes"),
        minimum=0,
        maximum=MAX_ESTIMATED_MINUTES,
        integer=True,
    )
    if estimated_minutes is not None:
        result["estimatedMinutes"] = estimated_minutes
    for key in ("minCrew", "maxCrew"):
        value = _safe_number(payload.get(key), minimum=1, maximum=100, integer=True)
        if value is not None:
            result[key] = value
    for key in (
        "earliestStartAt",
        "materialReadyAt",
        "completedAt",
        "plannedStartAt",
        "plannedEndAt",
        "createdAt",
        "updatedAt",
    ):
        value = _bounded_text(payload.get(key), MAX_TEXT_CHARS)
        if value is not None:
            result[key] = value
    assigned_worker_ids = _id_list(payload.get("assignedWorkerIds", payload.get("workerIds")))
    if assigned_worker_ids:
        result["assignedWorkerIds"] = assigned_worker_ids
    priority = _safe_number(payload.get("priority"), minimum=0)
    if priority is not None:
        result["priority"] = priority
    workability_mode = _bounded_text(
        payload.get("workabilityMode", payload.get("weatherMode")), MAX_TEXT_CHARS
    )
    if workability_mode is not None:
        result["workabilityMode"] = workability_mode
    return result


def _sanitize_worklog(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Retain bounded evidence and numeric/source metadata, excluding private assets."""

    worklog_id = _source_id(payload, "id", "worklogId")
    if worklog_id is None:
        return {}
    result: dict[str, Any] = {"id": worklog_id}
    for output_key, source_keys in (
        ("projectId", ("projectId", "project_id")),
        ("taskId", ("taskId", "task_id")),
        ("workerId", ("workerId", "workerProfileId", "worker_id")),
    ):
        value = _source_id(payload, *source_keys)
        if value is not None:
            result[output_key] = value
    for key in (
        "workDate",
        "knownAt",
        "updatedAt",
        "createdAt",
        "startAt",
        "endAt",
        "startedAt",
        "endedAt",
        "projectTimezone",
    ):
        value = _bounded_text(payload.get(key), MAX_TEXT_CHARS)
        if value is not None:
            result[key] = value
    source_revision = _source_id(payload, "sourceRevision", "revision", "updatedAt", "knownAt")
    if source_revision is not None:
        result["sourceRevision"] = source_revision
    duration = _safe_number(
        payload.get("netDurationMinutes", payload.get("durationMinutes")),
        minimum=0,
        maximum=MAX_WORKLOG_DURATION_MINUTES,
    )
    if duration is not None:
        result["netDurationMinutes"] = int(duration)
    for key in ("grossDurationMinutes", "breakMinutes"):
        value = _safe_number(payload.get(key), minimum=0, maximum=MAX_WORKLOG_DURATION_MINUTES)
        if value is not None:
            result[key] = int(value)
    is_closed = payload.get("isClosed")
    if isinstance(is_closed, bool):
        result["isClosed"] = is_closed
    description = _bounded_evidence(payload.get("description"))
    if description is not None:
        result["description"] = description
    return result


def _sanitize_reservation(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only interval identity fields used by the simulation engine."""

    result: dict[str, Any] = {}
    for output_key, source_keys in (
        ("id", ("id", "reservationId")),
        ("projectId", ("projectId", "project_id")),
        ("taskId", ("taskId", "task_id")),
        ("workerId", ("workerId", "workerProfileId", "worker_id")),
    ):
        value = _source_id(payload, *source_keys)
        if value is not None:
            result[output_key] = value
    for key in (
        "startsAt",
        "endsAt",
        "startAt",
        "endAt",
        "date",
        "startTime",
        "endTime",
    ):
        value = _bounded_text(payload.get(key), MAX_TEXT_CHARS)
        if value is not None:
            result[key] = value
    return result


def _sanitize_effort(value: Any) -> int | float | dict[str, int | float] | None:
    if isinstance(value, Mapping):
        result: dict[str, int | float] = {}
        for key in ("optimistic", "mostLikely", "pessimistic"):
            number = _safe_number(value.get(key), minimum=0)
            if number is not None:
                result[key] = number
        return result or None
    return _safe_number(value, minimum=0)


def _sanitize_intervals(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    for item in value[:MAX_COLLECTION_ITEMS]:
        if not isinstance(item, Mapping):
            continue
        interval: dict[str, str] = {}
        for key in ("startsAt", "endsAt", "startAt", "endAt", "date", "startTime", "endTime"):
            text = _bounded_text(item.get(key), MAX_TEXT_CHARS)
            if text is not None:
                interval[key] = text
        if interval:
            result.append(interval)
    return result


def _identity_fields(payload: Mapping[str, Any], *, include_name: bool) -> dict[str, Any]:
    source_id = _source_id(payload, "id", "organizationId", "specialtyId")
    if source_id is None:
        return {}
    result: dict[str, Any] = {"id": source_id}
    if include_name:
        name = _bounded_text(payload.get("name"), MAX_TEXT_CHARS)
        if name is not None:
            result["name"] = name
    return result


def _copy_id(result: dict[str, Any], payload: Mapping[str, Any], key: str) -> None:
    value = _source_id(payload, key)
    if value is not None:
        result[key] = value


def _source_id(payload: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (str, int)):
            normalized = str(value).strip()
            if normalized and len(normalized) <= MAX_IDENTIFIER_CHARS:
                return normalized
    return None


def _id_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value[:MAX_COLLECTION_ITEMS]:
        if isinstance(item, Mapping):
            continue
        if isinstance(item, bool) or not isinstance(item, (str, int)):
            continue
        normalized = str(item).strip()
        if normalized and len(normalized) <= MAX_IDENTIFIER_CHARS:
            result.append(normalized)
    return result


def _mapping_id_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value[:MAX_COLLECTION_ITEMS]:
        if isinstance(item, Mapping):
            source_id = _source_id(item, "id", "specialtyId")
            if source_id is not None:
                result.append(source_id)
    return result


def _int_list(value: Any, *, minimum: int, maximum: int) -> list[int]:
    if not isinstance(value, list):
        return []
    return [
        item
        for item in value[:MAX_COLLECTION_ITEMS]
        if isinstance(item, int) and not isinstance(item, bool) and minimum <= item <= maximum
    ]


def _bounded_text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized[:limit] if normalized else None


def _bounded_evidence(value: Any) -> str | None:
    text = _bounded_text(value, MAX_WORKLOG_DESCRIPTION_CHARS)
    if text is None:
        return None
    text = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    text = _SECRET_RE.sub("[REDACTED_SECRET]", text)
    text = _JWT_RE.sub("[REDACTED_SECRET]", text)
    return text[:MAX_WORKLOG_DESCRIPTION_CHARS]


def _safe_number(
    value: Any,
    *,
    minimum: float | None = None,
    maximum: float | None = MAX_NUMERIC_VALUE,
    integer: bool = False,
) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    numeric = float(value)
    if minimum is not None and numeric < minimum:
        return None
    if maximum is not None and numeric > maximum:
        return None
    if integer and not numeric.is_integer():
        return None
    return int(numeric) if integer else value


def _api_base(url: str) -> str:
    clean = url.rstrip("/")
    return clean if clean.endswith("/api/v1") else f"{clean}/api/v1"


def _items(payload: Mapping[str, Any] | list[Any]) -> list[dict[str, Any]]:
    """Extract a documented list response without silently treating drift as empty data."""

    if isinstance(payload, list):
        values: Any = payload
    elif isinstance(payload, Mapping):
        if "items" in payload:
            values = payload["items"]
        elif "data" in payload:
            values = payload["data"]
        else:
            raise UpstreamIntegrationError("timecue_collection_response_shape_invalid", 502)
    else:
        raise UpstreamIntegrationError("timecue_collection_response_shape_invalid", 502)
    if not isinstance(values, list) or any(not isinstance(value, Mapping) for value in values):
        raise UpstreamIntegrationError("timecue_collection_response_shape_invalid", 502)
    return [dict(value) for value in values]


def _normalize_permissions(raw: Any) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    if isinstance(raw, Mapping):
        for org_id, values in raw.items():
            result[str(org_id)] = _permission_values(values)
    elif isinstance(raw, list):
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            org_id = item.get("organizationId") or item.get("organization_id")
            if org_id:
                result[str(org_id)] = _permission_values(
                    item.get("permissions", item.get("permissionKeys", []))
                )
    return result


def _permission_values(values: Any) -> list[str]:
    if isinstance(values, Mapping):
        values = list(values.values())
    if not isinstance(values, (list, tuple, set, frozenset)):
        return []
    result: list[str] = []
    for value in values:
        if isinstance(value, str):
            result.append(value)
        elif isinstance(value, Mapping):
            permission = value.get("key") or value.get("permissionKey")
            if permission:
                result.append(str(permission))
    return sorted(set(result))


def _revision(*collections: list[dict[str, Any]]) -> str:
    raw = "|".join(repr(item) for collection in collections for item in collection)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _worklog_totals(worklogs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    totals: dict[str, int] = {}
    for worklog in worklogs:
        project_id = str(worklog.get("projectId") or "unlinked")
        value = worklog.get("netDurationMinutes", worklog.get("durationMinutes", 0))
        if isinstance(value, (int, float)):
            totals[project_id] = totals.get(project_id, 0) + int(value)
    return [
        {"projectId": project_id, "netDurationMinutes": minutes}
        for project_id, minutes in totals.items()
    ]


def _public_error_detail(response: httpx.Response) -> str:
    """Return a short Timecue error summary without request bodies or secrets."""

    try:
        payload = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    detail = payload.get("detail") if isinstance(payload, Mapping) else payload
    if isinstance(detail, list):
        parts: list[str] = []
        for item in detail[:8]:
            if isinstance(item, Mapping):
                loc = item.get("loc")
                field = loc[-1] if isinstance(loc, list) and loc else None
                message = item.get("msg")
                if isinstance(field, str) and isinstance(message, str) and message.strip():
                    parts.append(f"{field}: {message.strip()}")
                elif isinstance(message, str) and message.strip():
                    parts.append(message.strip())
            elif isinstance(item, str) and item.strip():
                parts.append(item.strip())
        if parts:
            return "; ".join(parts)[:500]
    if isinstance(detail, str) and detail.strip():
        return detail.strip()[:500]
    return f"HTTP {response.status_code}"


__all__ = ["LiveTimecueAdapter", "TimecueAuthClient", "UpstreamIntegrationError"]
