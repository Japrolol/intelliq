"""Prepare the local Timecue org so the Warsaw demo seed can run.

The Timecue light fixture does not expose every specialty the demo needs, and it
creates a target-less Townhouse Retrofit row. This script uses existing Timecue
routes only: it creates/activates specialties, widens existing worker profiles,
and deletes that leftover project so IntelliQ can create Mokotow with a target
date. It does not keep an archived stand-in, create members, or change Timecue
source code.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping
from typing import Any

import httpx

DEFAULT_TIMECUE_URL = "http://127.0.0.1:8000"
DEFAULT_ORGANIZATION_NAME = "Northstar Renovations"
NEEDED_SPECIALTIES = (
    "Electrical",
    "Carpentry",
    "Plumbing",
    "HVAC",
    "Finishing",
    "Inspection",
    "Demolition",
    "Tiling",
)
UNUSED_TOWNHOUSE_NAME = "Archived light-seed townhouse (unused)"
LIGHT_TOWNHOUSE_NAME = "Townhouse Retrofit"
LEFTOVER_TOWNHOUSE_NAMES = frozenset({LIGHT_TOWNHOUSE_NAME, UNUSED_TOWNHOUSE_NAME})


class PrepareError(RuntimeError):
    """Safe-to-print local preparation failure."""


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    email = args.email or os.getenv("TIMECUE_SEED_EMAIL")
    password = os.getenv("TIMECUE_SEED_PASSWORD")
    if not email or not password:
        raise PrepareError("Set TIMECUE_SEED_EMAIL and TIMECUE_SEED_PASSWORD.")
    with httpx.Client(
        base_url=_api_base(args.timecue_url),
        timeout=20.0,
        follow_redirects=False,
    ) as client:
        _request(client, "POST", "/auth/login", json={"email": email, "password": password})
        organizations = _items(_request(client, "GET", "/organizations"), "organizations")
        organization = next(
            (row for row in organizations if str(row.get("name") or "") == args.organization_name),
            None,
        )
        if organization is None:
            names = (
                ", ".join(str(row.get("name") or "<unnamed>") for row in organizations) or "none"
            )
            raise PrepareError(
                f"Organization {args.organization_name!r} was not found. Visible orgs: {names}."
            )
        organization_id = _text(organization.get("id"))
        if organization_id is None:
            raise PrepareError("The selected organization has no id.")
        specialty_ids = _ensure_specialties(client, organization_id)
        patched = _assign_specialties(client, organization_id, specialty_ids)
        removed = _remove_leftover_townhouse(
            client,
            organization_id,
            api_base=_api_base(args.timecue_url),
            password=password,
        )
    print(
        f"Prepared {args.organization_name} ({organization_id}): "
        f"{len(specialty_ids)} specialties, {patched} worker profiles, "
        f"{removed} leftover townhouse rows deleted."
    )
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timecue-url", default=os.getenv("TIMECUE_API_URL", DEFAULT_TIMECUE_URL))
    parser.add_argument("--organization-name", default=DEFAULT_ORGANIZATION_NAME)
    parser.add_argument("--email", default=os.getenv("TIMECUE_SEED_EMAIL"))
    return parser.parse_args(argv)


def _ensure_specialties(client: httpx.Client, organization_id: str) -> list[str]:
    listed = _items(
        _request(client, "GET", f"/organizations/{organization_id}/workforce/specialties"),
        "specialties",
    )
    try:
        inactive = _items(
            _request(
                client,
                "GET",
                f"/organizations/{organization_id}/workforce/specialties",
                params={"status": "inactive"},
            ),
            "specialties",
        )
    except PrepareError:
        inactive = []
    by_name = {
        str(row.get("name") or "").casefold(): row
        for row in (*listed, *inactive)
        if str(row.get("name") or "").strip()
    }
    for name in NEEDED_SPECIALTIES:
        row = by_name.get(name.casefold())
        if row is None:
            created = _request(
                client,
                "POST",
                f"/organizations/{organization_id}/workforce/specialties",
                json={"name": name, "description": f"Local demo specialty: {name}."},
            )
            if not isinstance(created, dict):
                raise PrepareError(f"Creating specialty {name!r} returned an unexpected shape.")
            by_name[name.casefold()] = created
            continue
        if row.get("isActive") is False:
            specialty_id = _text(row.get("id"))
            if specialty_id is None:
                raise PrepareError(f"Inactive specialty {name!r} has no id.")
            updated = _request(
                client,
                "PATCH",
                f"/organizations/{organization_id}/workforce/specialties/{specialty_id}",
                json={"isActive": True},
            )
            if isinstance(updated, dict):
                by_name[name.casefold()] = updated
    missing = [name for name in NEEDED_SPECIALTIES if name.casefold() not in by_name]
    if missing:
        raise PrepareError(f"Could not ensure specialties: {', '.join(missing)}.")
    return [_required_id(by_name[name.casefold()], name) for name in NEEDED_SPECIALTIES]


def _assign_specialties(
    client: httpx.Client, organization_id: str, specialty_ids: list[str]
) -> int:
    workers = _list_workers(client, organization_id)
    if len(workers) < 3:
        raise PrepareError(
            f"Need at least three existing worker profiles, found {len(workers)}."
        )
    for worker in workers:
        worker_id = _text(worker.get("id"))
        if worker_id is None:
            raise PrepareError("A worker profile is missing an id.")
        _request(
            client,
            "PATCH",
            f"/organizations/{organization_id}/workforce/workers/{worker_id}",
            json={"isActive": True, "specialtyIds": specialty_ids},
        )
    return len(workers)


def _remove_leftover_townhouse(
    client: httpx.Client,
    organization_id: str,
    *,
    api_base: str,
    password: str,
) -> int:
    """Delete the light-seed townhouse instead of leaving an archived blocker."""

    projects = _items(
        _request(client, "GET", f"/organizations/{organization_id}/projects"),
        "projects",
    )
    removed = 0
    for project in projects:
        name = str(project.get("name") or "")
        if name not in LEFTOVER_TOWNHOUSE_NAMES:
            continue
        project_id = _text(project.get("id"))
        if project_id is None:
            continue
        _delete_project_tree(
            client,
            organization_id,
            project_id,
            api_base=api_base,
            password=password,
        )
        removed += 1
    return removed


def _delete_project_tree(
    client: httpx.Client,
    organization_id: str,
    project_id: str,
    *,
    api_base: str,
    password: str,
) -> None:
    """Remove worklogs and tasks first so Timecue can delete the leftover project."""

    workers = {
        worker_id: worker
        for worker in _list_workers(client, organization_id)
        if (worker_id := _text(worker.get("id")))
    }
    worklogs = _list_project_worklogs(client, organization_id, project_id)
    emails_by_worklog: dict[str, list[str]] = {}
    for worklog in worklogs:
        worklog_id = _text(worklog.get("id"))
        profile_id = _text(worklog.get("workerProfileId"))
        if worklog_id is None or profile_id is None:
            continue
        worker = workers.get(profile_id, {})
        email = _text(worker.get("userEmail")) or _text(worker.get("email"))
        if email is None:
            continue
        emails_by_worklog.setdefault(email, []).append(worklog_id)
    for email, worklog_ids in emails_by_worklog.items():
        with httpx.Client(base_url=api_base, timeout=20.0, follow_redirects=False) as member:
            _request(member, "POST", "/auth/login", json={"email": email, "password": password})
            for worklog_id in worklog_ids:
                _try_delete_own_worklog(member, organization_id, worklog_id)
    tasks = _items(
        _request(
            client,
            "GET",
            f"/organizations/{organization_id}/projects/{project_id}/tasks",
            params={"include_completed": "true"},
        ),
        "tasks",
    )
    for task in tasks:
        task_id = _text(task.get("id"))
        if task_id is None:
            continue
        _request(
            client,
            "DELETE",
            f"/organizations/{organization_id}/projects/{project_id}/tasks/{task_id}",
        )
    zones = _items(
        _request(
            client,
            "GET",
            f"/organizations/{organization_id}/projects/{project_id}/zones",
        ),
        "zones",
    )
    for zone in sorted(zones, key=lambda row: 0 if row.get("parentZoneId") else 1):
        zone_id = _text(zone.get("id"))
        if zone_id is None:
            continue
        _request(
            client,
            "DELETE",
            f"/organizations/{organization_id}/projects/{project_id}/zones/{zone_id}",
        )
    _request(client, "DELETE", f"/organizations/{organization_id}/projects/{project_id}")


def _try_delete_own_worklog(
    client: httpx.Client, organization_id: str, worklog_id: str
) -> None:
    try:
        response = client.request(
            "DELETE",
            f"/organizations/{organization_id}/worklogs/mine/{worklog_id}",
        )
    except httpx.HTTPError as exc:
        raise PrepareError(
            "Could not reach Timecue DELETE "
            f"/organizations/{organization_id}/worklogs/mine/{worklog_id}."
        ) from exc
    if response.status_code in {204, 403, 404}:
        return
    if response.status_code >= 400:
        raise PrepareError(
            "Timecue DELETE worklog failed "
            f"({response.status_code}): {response.text[:300]}"
        )


def _list_project_worklogs(
    client: httpx.Client, organization_id: str, project_id: str
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        params: dict[str, str | int] = {"limit": 100, "project_id": project_id}
        if cursor:
            params["cursor"] = cursor
        payload = _request(
            client,
            "GET",
            f"/organizations/{organization_id}/worklogs",
            params=params,
        )
        page = _items(payload, "worklogs")
        rows.extend(row for row in page if str(row.get("projectId") or "") == project_id)
        if not isinstance(payload, dict) or not payload.get("nextCursor"):
            return rows
        next_cursor = payload.get("nextCursor")
        if not isinstance(next_cursor, str) or next_cursor == cursor:
            raise PrepareError("Timecue returned invalid worklog pagination.")
        cursor = next_cursor
        if not page:
            return rows


def _list_workers(client: httpx.Client, organization_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        payload = _request(
            client,
            "GET",
            f"/organizations/{organization_id}/workforce/workers",
            params={"offset": offset, "limit": 100},
        )
        rows.extend(_items(payload, "workers"))
        if not isinstance(payload, dict) or payload.get("nextOffset") is None:
            return rows
        next_offset = payload.get("nextOffset")
        if not isinstance(next_offset, int) or next_offset <= offset:
            raise PrepareError("Timecue returned invalid worker pagination.")
        offset = next_offset


def _request(client: httpx.Client, method: str, path: str, **kwargs: object) -> Any:
    try:
        response = client.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        raise PrepareError(f"Could not reach Timecue {method} {path}.") from exc
    if response.status_code >= 400:
        raise PrepareError(
            f"Timecue {method} {path} failed ({response.status_code}): "
            f"{response.text[:300]}"
        )
    if response.status_code == 204:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise PrepareError(f"Timecue {method} {path} returned invalid JSON.") from exc


def _items(payload: object, label: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        values = payload
    elif isinstance(payload, dict) and isinstance(payload.get("items"), list):
        values = payload["items"]
    else:
        raise PrepareError(f"Timecue returned an unexpected {label} response.")
    rows = [row for row in values if isinstance(row, dict)]
    if len(rows) != len(values):
        raise PrepareError(f"Timecue returned invalid {label} rows.")
    return rows


def _api_base(url: str) -> str:
    origin = url.rstrip("/")
    if origin.endswith("/api/v1"):
        return origin
    return f"{origin}/api/v1"


def _text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _required_id(row: Mapping[str, object], label: str) -> str:
    value = _text(row.get("id"))
    if value is None:
        raise PrepareError(f"Specialty {label!r} has no id.")
    return value


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PrepareError as exc:
        print(f"Prepare failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
