from collections.abc import Mapping
from datetime import datetime

import httpx

from src.app.config import Settings
from src.app.integrations.timecue import LiveTimecueAdapter, TimecueAuthClient
from src.app.persistence.store import SessionRecord


def test_live_adapter_completes_worker_offset_worklog_cursor_and_assignment_reads() -> None:
    calls: list[tuple[str, str, Mapping[str, object] | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.url.params))
        path = request.url.path
        if path.endswith("/projects"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "alpha",
                        "name": "Alpha",
                        "status": "in_progress",
                        "clientEmail": "client@example.com",
                        "accessToken": "project-secret-token",
                    },
                    {
                        "id": "archived-townhouse",
                        "name": "Archived light-seed townhouse (unused)",
                        "status": "archived",
                    },
                ],
            )
        if path.endswith("/workforce/workers"):
            offset = request.url.params.get("offset")
            if offset == "0":
                return httpx.Response(
                    200,
                    json={
                        "items": [
                            {
                                "id": "w1",
                                "name": "Private Worker Name",
                                "email": "worker@example.com",
                                "specialtyIds": ["electrical"],
                                "homeProjectId": "alpha",
                                "workingWeekdays": [0, 1, 2, 3, 4],
                                "shiftStart": "08:00",
                                "shiftEnd": "16:00",
                                "canTransfer": True,
                                "updatedAt": "2026-09-20T08:00:00Z",
                                "privateEvidence": {"assetUrl": "https://private.test/a"},
                                "accessToken": "worker-secret-token",
                            }
                        ],
                        "nextOffset": 1,
                    },
                )
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "w2",
                            "name": "Another Private Worker",
                            "email": "worker-two@example.com",
                        }
                    ],
                    "nextOffset": None,
                },
            )
        if path.endswith("/workforce/specialties"):
            return httpx.Response(200, json=[{"id": "electrical"}])
        if "/projects/archived-townhouse/" in path:
            raise AssertionError("archived project must not be fetched")
        if path.endswith("/tasks"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "t1",
                        "projectId": "alpha",
                        "title": "Electrical installation",
                        "status": "assigned",
                        "assignedWorkerIds": ["w1"],
                        "plannedStartAt": "2026-09-21T08:00:00Z",
                        "estimatedMinutes": 960,
                        "updatedAt": "2026-09-20T08:00:00Z",
                        "evidenceAssets": [{"url": "https://private.test/evidence"}],
                        "assignedWorkers": [{"id": "w1", "name": "Private Worker Name"}],
                        "privateNotes": "worker@example.com",
                        "refreshToken": "task-secret-token",
                    }
                ],
            )
        if path.endswith("/assignments"):
            return httpx.Response(
                200,
                json={
                    "directWorkers": [],
                    "teams": [],
                    "effectiveWorkers": [
                        {
                            "workerProfileId": "w1",
                            "userId": "user-w1",
                            "firstName": "Private",
                            "lastName": "Worker Name",
                            "jobTitle": "Electrician",
                            "trade": "Electrical",
                            "isActive": True,
                            "sources": [],
                        }
                    ],
                    "effectiveWorkerCount": 1,
                },
            )
        if path.endswith("/worklogs"):
            cursor = request.url.params.get("cursor")
            if cursor is None:
                return httpx.Response(
                    200,
                    json={
                        "items": [
                            {
                                "id": "l1",
                                "projectId": "alpha",
                                "taskId": "t1",
                                "workerProfileId": "w1",
                                "workDate": "2026-09-21",
                                "startedAt": "2026-09-21T07:30:00Z",
                                "endedAt": "2026-09-21T09:00:00Z",
                                "updatedAt": "2026-09-21T08:00:00Z",
                                "durationMinutes": 90,
                                "grossDurationMinutes": 100,
                                "breakMinutes": 10,
                                "netDurationMinutes": 90,
                                "isClosed": True,
                                "workerFirstName": "Private",
                                "workerLastName": "Worker Name",
                                "description": (
                                    "Blocked by installation. Contact worker@example.com; "
                                    "access_token=worklog-secret-token. " + ("Evidence " * 1_500)
                                ),
                                "evidence": [{"id": "private-asset"}],
                                "tags": [{"name": "private-tag"}],
                                "apiKey": "worklog-api-key",
                            }
                        ],
                        "nextCursor": "next",
                    },
                )
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "l2",
                            "projectId": "alpha",
                            "updatedAt": "2026-09-21T09:00:00Z",
                            "netDurationMinutes": 30,
                        }
                    ],
                    "nextCursor": None,
                },
            )
        if path.endswith("/calendar"):
            range_start = request.url.params.get("rangeStart")
            range_end = request.url.params.get("rangeEnd")
            assert range_start is not None and range_end is not None
            assert datetime.fromisoformat(range_start).tzinfo is not None
            assert datetime.fromisoformat(range_end).tzinfo is not None
            assert (
                datetime.fromisoformat(range_end) - datetime.fromisoformat(range_start)
            ).days <= 28
            return httpx.Response(
                200,
                json={
                    "timezone": "Europe/Warsaw",
                    "rangeStart": range_start,
                    "rangeEnd": range_end,
                    "items": [
                        {
                            "id": "calendar-worker",
                            "projectId": "beta",
                            "accessToken": "calendar-secret-token",
                            "assignments": [
                                {"sourceType": "worker", "sourceId": "w2", "sourceName": "W2"}
                            ],
                        },
                        {
                            "id": "calendar-team",
                            "projectId": "beta",
                            "assignments": [
                                {"sourceType": "team", "sourceId": "team-1", "sourceName": "Crew"}
                            ],
                        },
                    ],
                },
            )
        raise AssertionError(f"unexpected route {path}")

    settings = Settings(data_mode="live", timecue_api_url="https://timecue.test")
    auth = TimecueAuthClient(settings, transport=httpx.MockTransport(handler))
    adapter = LiveTimecueAdapter(settings, auth, lambda session, failed_access: None)
    session = SessionRecord(
        "local",
        "u",
        "u@example.com",
        True,
        ["org"],
        {"org": ["projects.read"]},
        "live",
        "csrf",
    )
    auth.vault.put("local", "a", "r")

    snapshot = adapter.snapshot(session, "org")

    assert [project["id"] for project in snapshot.projects] == ["alpha"]
    assert snapshot.projects[0]["status"] == "in_progress"
    assert all(project.get("id") != "archived-townhouse" for project in snapshot.projects)
    assert all(
        not path.endswith("/projects/archived-townhouse/tasks") for _, path, _ in calls
    )

    assert [worker["id"] for worker in snapshot.workers] == ["w1", "w2"]
    assert len(snapshot.worklogs) == 2
    assert snapshot.effective_assignments == [{"taskId": "t1", "workerIds": ["w1"]}]
    assert any(item.get("workerId") == "w2" for item in snapshot.reservations)
    assert snapshot.worklogs[0]["netDurationMinutes"] == 90
    assert snapshot.worklogs[0]["sourceRevision"] == "2026-09-21T08:00:00Z"
    assert snapshot.worklogs[0]["startedAt"] == "2026-09-21T07:30:00Z"
    assert snapshot.worklogs[0]["endedAt"] == "2026-09-21T09:00:00Z"
    assert snapshot.worklogs[0]["grossDurationMinutes"] == 100
    assert snapshot.worklogs[0]["breakMinutes"] == 10
    assert snapshot.worklogs[0]["isClosed"] is True
    assert len(snapshot.worklogs[0]["description"]) <= 4_000
    assert "[REDACTED_EMAIL]" in snapshot.worklogs[0]["description"]
    assert "[REDACTED_SECRET]" in snapshot.worklogs[0]["description"]
    assert set(snapshot.workers[0]) == {
        "id",
        "specialtyIds",
        "homeProjectId",
        "workingWeekdays",
        "shiftStart",
        "shiftEnd",
        "canTransfer",
        "updatedAt",
    }
    assert "assignedWorkerIds" in snapshot.tasks[0]
    assert set(snapshot.tasks[0]) == {
        "id",
        "projectId",
        "title",
        "status",
        "assignedWorkerIds",
        "plannedStartAt",
        "estimatedMinutes",
        "updatedAt",
    }
    assert snapshot.tasks[0]["estimatedMinutes"] == 960
    snapshot_json = str(snapshot.model_dump(mode="json", by_alias=True))
    for excluded in (
        "Private Worker Name",
        "worker@example.com",
        "worker-two@example.com",
        "private-asset",
        "project-secret-token",
        "worker-secret-token",
        "task-secret-token",
        "worklog-api-key",
        "calendar-secret-token",
    ):
        assert excluded not in snapshot_json
    assert snapshot.complete is False
    assert "outside_project_team_assignments_unresolved" in snapshot.warnings
    task_call = next(params for method, path, params in calls if path.endswith("/tasks"))
    assert task_call.get("include_completed") == "true"
    calendar_call = next(params for method, path, params in calls if path.endswith("/calendar"))
    assert calendar_call.get("projectless") == "true"
    calendar_calls = [params for _, path, params in calls if path.endswith("/calendar")]
    assert len(calendar_calls) == 2
    assert calendar_calls[0]["rangeEnd"] == calendar_calls[1]["rangeStart"]
    assert (
        datetime.fromisoformat(calendar_calls[-1]["rangeEnd"])
        - datetime.fromisoformat(calendar_calls[0]["rangeStart"])
    ).days == 56
    worker_calls = [
        (path, params) for _, path, params in calls if path.endswith("/workforce/workers")
    ]
    assert [params.get("limit") for _, params in worker_calls] == ["100", "100"]


def test_timecue_refresh_rotates_only_the_matching_session_vault_entry() -> None:
    seen_refresh: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/auth/refresh")
        refresh = request.headers.get("cookie", "")
        seen_refresh.append(refresh)
        return httpx.Response(
            200,
            headers=[
                ("set-cookie", "access_token=access-one-rotated; Path=/"),
                ("set-cookie", "refresh_token=refresh-one-rotated; Path=/"),
            ],
        )

    settings = Settings(data_mode="live", timecue_api_url="https://timecue.test")
    auth = TimecueAuthClient(settings, transport=httpx.MockTransport(handler))
    session_one = SessionRecord(
        "session-one", "u1", "u1@example.com", True, ["org"], {}, "live", "csrf-1"
    )
    session_two = SessionRecord(
        "session-two", "u2", "u2@example.com", True, ["org"], {}, "live", "csrf-2"
    )
    auth.vault.put(session_one.session_id, "access-one", "refresh-one")
    auth.vault.put(session_two.session_id, "access-two", "refresh-two")

    auth.refresh(session_one)

    assert "refresh_token=refresh-one" in seen_refresh[0]
    assert auth.vault.get(session_one.session_id).access == "access-one-rotated"
    assert auth.vault.get(session_one.session_id).refresh == "refresh-one-rotated"
    assert auth.vault.get(session_two.session_id).access == "access-two"
    assert auth.vault.get(session_two.session_id).refresh == "refresh-two"


def test_timecue_auth_parser_accepts_blueprint_user_response_shape() -> None:
    user = TimecueAuthClient._parse_user(
        {
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
            "organization": {
                "id": "22222222-2222-2222-2222-222222222222",
                "name": "Example Construction",
            },
            "appPermissions": ["app.access"],
            "organizationPermissions": [
                {
                    "organizationId": "22222222-2222-2222-2222-222222222222",
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
    )

    assert user.id == "11111111-1111-1111-1111-111111111111"
    assert user.email_verified_at is not None
    assert user.organization_ids == ["22222222-2222-2222-2222-222222222222"]
    assert user.organization_permissions["22222222-2222-2222-2222-222222222222"] == [
        "projects.read",
        "tasks.read",
        "workers.read",
        "worklogs.read",
    ]
