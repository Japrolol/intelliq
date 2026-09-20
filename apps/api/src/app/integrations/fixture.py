"""Read-only synthetic Timecue adapter backed by the supplied demo fixture."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.app.domain.contracts import PortfolioSnapshot, SessionUser, SourceMode
from src.app.domain.status import without_archived_projects
from src.app.persistence.store import SessionRecord


class FixtureTimecueAdapter:
    """Expose fixture data through the same snapshot boundary as live Timecue."""

    def __init__(self, fixture_path: str | Path | None = None) -> None:
        path = (
            Path(fixture_path)
            if fixture_path
            else Path(__file__).resolve().parents[5] / "fixtures" / "portfolio-demo.json"
        )
        with path.open(encoding="utf-8") as fixture_file:
            self.payload: dict[str, Any] = json.load(fixture_file)

    def synthetic_user(self, email: str) -> SessionUser:
        organization_id = str(self.payload["organizationId"])
        permissions = {
            organization_id: [
                "projects.read",
                "projects.write",
                "tasks.read",
                "tasks.write",
                "tasks.assign",
                "workers.read",
                "worklogs.read",
            ]
        }
        return SessionUser(
            id="fixture-user",
            email=email,
            emailVerifiedAt=self.payload["asOf"],
            organizationIds=[organization_id],
            organizationPermissions=permissions,
            appPermissions=[],
            appAdmin=False,
            sourceMode=SourceMode.FIXTURE,
            synthetic=True,
        )

    def organizations(self, session: SessionRecord | None = None) -> list[dict[str, Any]]:
        return [
            {
                "id": self.payload["organizationId"],
                "name": "Synthetic demo organization",
                "synthetic": True,
            }
        ]

    def snapshot(self, session: SessionRecord, organization_id: str) -> PortfolioSnapshot:
        if organization_id != str(self.payload["organizationId"]):
            raise ValueError("fixture_organization_not_found")
        payload = dict(self.payload)
        payload["projects"], payload["tasks"], payload["worklogs"] = without_archived_projects(
            list(payload.get("projects") or []),
            list(payload.get("tasks") or []),
            list(payload.get("worklogs") or []),
        )
        payload["snapshotId"] = f"fixture-{organization_id}-v1"
        payload["sourceRevision"] = "fixture-portfolio-demo-v1"
        payload["fetchedAt"] = payload["asOf"]
        payload["complete"] = True
        payload["warnings"] = ["synthetic_fixture_data"]
        payload["omittedSections"] = ["weather"]
        payload["effectiveAssignments"] = self._assignments(payload)
        payload["externalReservations"] = payload.get("reservations", [])
        payload["worklogTotals"] = self._worklog_totals(payload.get("worklogs", []))
        return PortfolioSnapshot.model_validate(payload)

    @staticmethod
    def _assignments(payload: dict[str, Any]) -> list[dict[str, Any]]:
        by_project: dict[str, list[str]] = {}
        for worker in payload.get("workers", []):
            if worker.get("homeProjectId"):
                by_project.setdefault(str(worker["homeProjectId"]), []).append(str(worker["id"]))
        return [
            {"taskId": task["id"], "workerIds": by_project.get(str(task.get("projectId")), [])}
            for task in payload.get("tasks", [])
        ]

    @staticmethod
    def _worklog_totals(worklogs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        totals: dict[str, int] = {}
        for worklog in worklogs:
            project_id = str(worklog.get("projectId") or "unlinked")
            totals[project_id] = totals.get(project_id, 0) + int(
                worklog.get("netDurationMinutes", worklog.get("durationMinutes", 0))
            )
        return [
            {"projectId": project_id, "netDurationMinutes": minutes}
            for project_id, minutes in totals.items()
        ]


__all__ = ["FixtureTimecueAdapter"]
