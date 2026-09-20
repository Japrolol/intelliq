"""Read-only audit of live Timecue records and IntelliQ planning completeness.

Uses an existing authorized local session without printing credentials. It does
not seed, repair, apply decisions, or invent missing operational data.
"""

import argparse
import asyncio
import json

from sqlalchemy import select

from src.app.domain.readiness import readiness_issues
from src.app.main import create_app
from src.app.persistence.store import SessionRow
from src.app.services.portfolio import apply_estimates, planning_for


async def audit(organization_id: str) -> int:
    app = create_app()
    async with app.router.lifespan_context(app):
        with app.state.store._session_factory() as db:
            row = db.scalars(
                select(SessionRow)
                .where(
                    SessionRow.source_mode == "live",
                    SessionRow.organizations_json.contains(organization_id),
                )
                .order_by(SessionRow.created_at.desc())
            ).first()
            session_id = row.id if row else None
        if session_id is None:
            print("Sign in to IntelliQ for this organization before auditing.")
            return 2
        record = app.state.store.get_session(session_id)
        if record is None:
            print("The session expired. Sign in again.")
            return 2
        session = app.state.auth.current(record)
        if organization_id not in session.user.organization_ids:
            print("The signed-in user cannot access this organization.")
            return 2
        snapshot = app.state.adapter.snapshot(session.record, organization_id)
        planning = planning_for(app.state.store, snapshot)
        estimated_inputs = apply_estimates(planning)
        issues = readiness_issues(snapshot, planning)
        active_tasks = [
            t
            for t in planning.tasks
            if t.get("status") not in {"done", "canceled", "cancelled", "completed"}
        ]
        workers = {w["id"]: w for w in planning.workers}
        warnings = []
        for project in planning.projects:
            location = project.get("location") or {}
            if location.get("latitude") is None or location.get("longitude") is None:
                warnings.append(
                    f"{project.get('name', project['id'])}: missing location for weather/routes"
                )
        for task in active_tasks:
            label = task.get("title", task["id"])
            specialty = task.get("requiredSpecialtyId")
            if not specialty:
                warnings.append(f"{label}: no required specialty")
            elif not any(specialty in w.get("specialtyIds", []) for w in workers.values()):
                warnings.append(f"{label}: no worker has the required specialty")
            if task.get("workabilityMode") == "outdoor" and not task.get("weatherRules"):
                warnings.append(f"{label}: outdoor task without explicit weather rules")
        print(
            json.dumps(
                {
                    "organizationId": organization_id,
                    "sourceMode": snapshot.source_mode.value,
                    "projects": len(planning.projects),
                    "workers": len(workers),
                    "tasks": len(planning.tasks),
                    "worklogs": len(snapshot.worklogs),
                    "reservations": len(planning.reservations),
                    "transferAssumptions": len(planning.transfers),
                    "readinessIssues": issues,
                    "estimatedInputs": estimated_inputs,
                    "qualityWarnings": warnings,
                },
                indent=2,
            )
        )
        return 1 if issues or warnings else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization-id", required=True)
    raise SystemExit(asyncio.run(audit(parser.parse_args().organization_id)))
