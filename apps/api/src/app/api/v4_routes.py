"""Decision-first portfolio views and owner-approved execution workflows."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from src.app.api.auth_service import AuthenticatedSession
from src.app.api.dependencies import require_org_permissions
from src.app.api.routes import _ensure_analysis_fresh, _load_snapshot, _planning_for
from src.app.decisions.calibration import update_setup_hours
from src.app.integrations.timecue import UpstreamIntegrationError
from src.app.services.execution import (
    compare_readback_fields,
    execution_request_fingerprint,
    step_dependencies,
)

router = APIRouter(prefix="/api/organizations/{organization_id}")
READ = require_org_permissions("projects.read", "tasks.read", "workers.read", "worklogs.read")
WRITE = require_org_permissions(
    "projects.read", "tasks.read", "workers.read", "worklogs.read", "tasks.assign", write=True
)


class ScenarioChoice(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    # Older saved bundles include action descriptions in their identifiers.
    strategy_id: str = Field(alias="strategyId", min_length=1, max_length=1024)


class ApplyChoice(ScenarioChoice):
    review_hash: str = Field(alias="reviewHash", min_length=64, max_length=64)
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=128)


def _record(request: Request, org: str, kind: str, key: str) -> dict:
    result = request.app.state.store.get_record(org, kind, key)
    if result is None:
        raise HTTPException(404, f"{kind}_not_found")
    return result


def _source_invariant(snapshot: Any, steps: list[dict]) -> str:
    """Ignore only fields this approved bundle is allowed to change."""
    dated_tasks = {s["action"].get("taskId") for s in steps if s["kind"] == "api"}
    tasks = [
        {
            k: v
            for k, v in task.items()
            if not (
                task.get("id") in dated_tasks
                and k in {"plannedStartAt", "plannedEndAt", "updatedAt"}
            )
        }
        for task in snapshot.tasks
    ]
    material = {
        "tasks": tasks,
        "projects": snapshot.projects,
        "workers": snapshot.workers,
        "reservations": snapshot.reservations,
        "assignments": snapshot.effective_assignments,
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode()).hexdigest()


def _check_calibration(store: Any, org: str, analysis: dict, source_mode: str) -> None:
    calibration = store.get_calibration(org, source_mode)
    version = str(calibration["version"]) if calibration else "initial"
    if str(analysis.get("calibrationVersion", "initial")) != version:
        raise HTTPException(409, "calibration_changed_new_review_required")


@router.get("/overview")
def overview(
    organization_id: str, request: Request, session: AuthenticatedSession = Depends(READ)
) -> dict:
    snapshot = _load_snapshot(request, session, organization_id)
    analyses = request.app.state.store.list_analyses(organization_id)
    analysis = next((a for a in analyses if a.get("status") == "completed"), None)
    executions = request.app.state.store.list_records(organization_id, "execution")
    from src.app.services.portfolio import execution_revision
    from src.app.workers.service import public_job

    jobs = [
        job
        for job in request.app.state.store.list_records(organization_id, "job")
        if job.get("type") == "analysis"
    ]

    planning = _planning_for(request, snapshot)
    calibration = request.app.state.store.get_calibration(
        organization_id, snapshot.source_mode.value
    )
    stale = bool(
        analysis
        and (
            analysis.get("sourceRevision") != snapshot.source_revision
            or analysis.get("sourceMode") != snapshot.source_mode.value
            or analysis.get("assumptionsVersion") != planning.version
            or analysis.get("executionRevision")
            != execution_revision(request.app.state.store, organization_id)
            or str(analysis.get("calibrationVersion"))
            != (str(calibration["version"]) if calibration else "initial")
            or (datetime.now(UTC) - datetime.fromisoformat(analysis["completedAt"])).total_seconds()
            > 86400
        )
    )
    return {
        "analysis": analysis,
        "latestJob": public_job(jobs[0]) if jobs else None,
        "projects": snapshot.projects,
        "readinessIssues": analysis.get("readinessIssues", []) if analysis else [],
        "pendingSteps": [
            dict(step, executionId=e["id"])
            for e in executions
            for step in e.get("steps", [])
            if step["status"] in {"pending", "blocked", "needs_reconciliation"}
        ],
        "sourceMode": snapshot.source_mode.value,
        "generatedAt": analysis.get("completedAt") if analysis else None,
        "providerStatus": analysis.get("providerStatus", {}) if analysis else {},
        "stale": stale,
    }


@router.get("/projects/{project_id}")
def project_detail(
    organization_id: str,
    project_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(READ),
) -> dict:
    """Read canonical project work without turning it into an isolated simulation.

    The authorized Timecue snapshot is the operational source. Local planning
    inputs and portfolio forecasts are returned separately and labelled as such.
    """
    snapshot = _load_snapshot(request, session, organization_id)
    project = next((p for p in snapshot.projects if str(p.get("id")) == project_id), None)
    if project is None:
        raise HTTPException(404, "project_not_found")
    tasks = [t for t in snapshot.tasks if str(t.get("projectId")) == project_id]
    task_ids = {str(t["id"]) for t in tasks}
    assignments = [a for a in snapshot.effective_assignments if str(a.get("taskId")) in task_ids]
    worker_ids = {str(w) for a in assignments for w in a.get("workerIds", [])}
    worklogs = [
        w
        for w in snapshot.worklogs
        if str(w.get("projectId")) == project_id or str(w.get("taskId")) in task_ids
    ]
    workers = [w for w in snapshot.workers if str(w.get("id")) in worker_ids]
    planning = _planning_for(request, snapshot)
    analyses = request.app.state.store.list_analyses(organization_id)
    history = [
        {
            "id": a["id"],
            "completedAt": a.get("completedAt"),
            "outcome": a.get("baselineByProject", {}).get(project_id),
        }
        for a in analyses
        if a.get("status") == "completed"
        and a.get("sourceMode") == snapshot.source_mode.value
        and project_id in a.get("baselineByProject", {})
    ]
    return {
        "project": project,
        "tasks": tasks,
        "workers": workers,
        "assignments": assignments,
        "worklogs": worklogs,
        "specialties": snapshot.specialties,
        "planning": {
            "version": planning.version,
            "project": next((p for p in planning.projects if str(p.get("id")) == project_id), None),
            "tasks": [t for t in planning.tasks if str(t.get("id")) in task_ids],
        },
        "analyses": history,
        "sourceMode": snapshot.source_mode.value,
        "asOf": snapshot.as_of.isoformat(),
        "warnings": snapshot.warnings,
    }


@router.get("/jobs/{job_id}")
def job(
    organization_id: str,
    job_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(READ),
) -> dict:
    from src.app.workers.service import public_job

    return public_job(_record(request, organization_id, "job", job_id))


@router.get("/analyses/{analysis_id}/graph")
def graph(
    organization_id: str,
    analysis_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(READ),
) -> dict:
    return _record(request, organization_id, "analysis_input", analysis_id)["graph"]


def _review(
    org: str, analysis_id: str, strategy_id: str, request: Request, session: AuthenticatedSession
) -> dict:
    store = request.app.state.store
    analysis = store.get_analysis(org, analysis_id)
    if not analysis or analysis.get("status") != "completed":
        raise HTTPException(404, "completed_analysis_not_found")
    from src.app.services.portfolio import execution_revision

    if analysis.get("executionRevision") and analysis["executionRevision"] != execution_revision(
        store, org
    ):
        raise HTTPException(409, "portfolio_decisions_changed_refresh_required")
    if any(e.get("analysisId") == analysis_id for e in store.list_records(org, "execution")):
        raise HTTPException(409, "analysis_already_approved_refresh_required")
    if any(
        e.get("updatedAt", "") > analysis.get("completedAt", "")
        for e in store.list_records(org, "execution")
    ):
        raise HTTPException(409, "portfolio_decisions_changed_refresh_required")
    snapshot = _load_snapshot(request, session, org)
    _ensure_analysis_fresh(analysis, snapshot, _planning_for(request, snapshot), request, org)
    # Demo mode intentionally approves against the frozen analysis result. The
    # provider-context freshness guard re-fetches volatile weather/route data
    # from a different process and rejects otherwise unchanged analyses.
    strategy = next((s for s in analysis.get("strategies", []) if s.get("id") == strategy_id), None)
    if strategy is None:
        raise HTTPException(404, "strategy_not_found")
    from src.app.services.portfolio import approval_eligible

    if not approval_eligible(strategy):
        raise HTTPException(
            409,
            {
                "code": "strategy_not_approval_eligible",
                "reasons": strategy.get("rejectionReasons", []),
            },
        )
    requested_workers = {
        a.get("workerId") for a in strategy.get("actions", []) if a.get("workerId")
    }
    requested_tasks = {a.get("taskId") for a in strategy.get("actions", []) if a.get("taskId")}
    for execution in store.list_records(org, "execution"):
        for pending in execution.get("steps", []):
            if pending.get("status") in {
                "pending",
                "blocked",
                "applying",
                "needs_reconciliation",
            } and (
                pending.get("action", {}).get("workerId") in requested_workers
                or pending.get("action", {}).get("taskId") in requested_tasks
            ):
                raise HTTPException(409, "resolve_pending_worker_decision_before_approval")
    steps = []
    for index, action in enumerate(strategy.get("actions", [])):
        kind = str(action.get("type", "action"))
        step = {
            "id": f"step-{index + 1}",
            "title": kind.replace("_", " ").capitalize(),
            "kind": "manual",
            "status": "pending",
            "action": action,
            "detail": "Confirm implementation on site; approval alone changes no planning inputs.",
        }
        if kind == "transfer":
            step["title"] = f"Transfer {action.get('workerName') or action.get('workerId')}"
            step["detail"] = (
                f"Move from {action.get('fromProjectName') or action.get('fromProjectId')} "
                f"to {action.get('toProjectName') or action.get('toProjectId')} "
                f"for {action.get('startsAt')} – {action.get('endsAt')}. "
                "Arrange travel, induction and return. "
                "Timecue has no equivalent timed assignment API."
            )
        if kind in {"date_shift", "schedule_shift", "weather_shift", "material_shift"}:
            task = next((t for t in snapshot.tasks if t.get("id") == action.get("taskId")), None)
            if task and action.get("startsAt") and action.get("endsAt"):
                step.update(
                    title=f"Reschedule {task.get('title') or task.get('name') or 'task'}",
                    kind="api",
                    permission="tasks.write",
                    method="PATCH",
                    path=f"/organizations/{org}/projects/{task['projectId']}/tasks/{task['id']}",
                    body={"plannedStartAt": action["startsAt"], "plannedEndAt": action["endsAt"]},
                    detail="The task dates will be updated in Timecue and checked after saving.",
                )
                if snapshot.source_mode.value == "live":
                    current = request.app.state.adapter._get(session.record, step["path"])
                    step["before"] = {k: current.get(k) for k in step["body"]}
                else:
                    step["kind"] = "manual"
                    step["detail"] = "Synthetic fixture: no Timecue writes are performed."
        steps.append(step)
    if not steps:
        raise HTTPException(409, "option_has_no_changes")
    dependencies = step_dependencies(steps)
    for step in steps:
        step["dependsOn"] = list(dependencies[step["id"]])
    result = {
        "analysisId": analysis_id,
        "strategyId": strategy_id,
        "steps": steps,
        "explanation": strategy.get("explanation"),
        "sourceRevision": analysis["sourceRevision"],
        "assumptionsVersion": analysis["assumptionsVersion"],
        "sourceInvariant": _source_invariant(snapshot, steps),
        "warnings": [
            "Timecue changes are saved automatically. On-site steps remain pending until you confirm they happened."
        ]
        if any(step["kind"] == "manual" for step in steps)
        else [],
    }
    result["reviewHash"] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result


@router.post("/analyses/{analysis_id}/review")
def review(
    organization_id: str,
    analysis_id: str,
    body: ScenarioChoice,
    request: Request,
    session: AuthenticatedSession = Depends(WRITE),
) -> dict:
    return _review(organization_id, analysis_id, body.strategy_id, request, session)


@router.post("/analyses/{analysis_id}/apply")
def apply(
    organization_id: str,
    analysis_id: str,
    body: ApplyChoice,
    request: Request,
    session: AuthenticatedSession = Depends(WRITE),
) -> dict:
    store = request.app.state.store
    execution_id = hashlib.sha256(body.idempotency_key.encode()).hexdigest()
    token = store.acquire_lease(organization_id, "execution")
    if token is None:
        raise HTTPException(409, "another_execution_in_progress")
    try:
        existing = store.get_record(organization_id, "execution", execution_id)
        if existing:
            if (
                existing["analysisId"] != analysis_id
                or existing["reviewHash"] != body.review_hash
                or existing["strategyId"] != body.strategy_id
            ):
                raise HTTPException(409, "idempotency_key_conflict")
            if not any(
                s["kind"] == "api" and s["status"] in {"applying", "needs_reconciliation"}
                for s in existing["steps"]
            ):
                return existing
            return _continue_execution(
                organization_id, execution_id, existing, request, session, token, write=False
            )
        reviewed = _review(organization_id, analysis_id, body.strategy_id, request, session)
        if reviewed["reviewHash"] != body.review_hash:
            raise HTTPException(409, "review_changed_refresh_required")
        granted = set(session.user.organization_permissions.get(organization_id, []))
        if not session.user.app_admin and any(
            s.get("permission") not in granted for s in reviewed["steps"] if s["kind"] == "api"
        ):
            raise HTTPException(403, "missing_execution_permission")
        execution = dict(
            reviewed,
            status="applying",
            approvedBy=session.user.id,
            approvedAt=datetime.now(UTC).isoformat(),
            sourceMode=session.user.source_mode.value,
            requestFingerprint=execution_request_fingerprint(
                analysis_id, body.strategy_id, body.review_hash
            ),
        )
        store.save_record(organization_id, "execution", execution_id, execution)
        return _continue_execution(
            organization_id, execution_id, execution, request, session, token
        )
    finally:
        store.release_lease(organization_id, "execution", token)


def _continue_execution(
    org: str,
    execution_id: str,
    execution: dict,
    request: Request,
    session: AuthenticatedSession,
    token: str,
    *,
    write: bool = True,
) -> dict:
    """Read back uncertain writes; resume only the original approved operations."""
    store = request.app.state.store
    granted = set(session.user.organization_permissions.get(org, []))
    for step in execution["steps"]:
        if step["kind"] != "api" or step["status"] in {"applied", "cancelled", "failed"}:
            continue
        if not session.user.app_admin and step.get("permission") not in granted:
            raise HTTPException(403, "missing_execution_permission")
        complete_ids = {
            s["id"] for s in execution["steps"] if s["status"] in {"applied", "confirmed"}
        }
        if not set(step.get("dependsOn", [])).issubset(complete_ids):
            step["status"] = "blocked"
            continue
        try:
            current = request.app.state.adapter._get(session.record, step["path"])
            if compare_readback_fields(step["body"], current):
                step.update(
                    status="applied",
                    receipt={k: current.get(k) for k in ("id", "updatedAt", *step["body"])},
                )
                step.pop("error", None)
                continue
            if not compare_readback_fields(step["before"], current):
                step.update(status="failed", error="upstream_precondition_changed")
                break
            if not write:
                step.update(status="pending", reconciliation="verified_unchanged")
                continue
            if not store.renew_lease(org, "execution", token):
                raise HTTPException(409, "execution_lease_lost")
            step["status"] = "applying"
            store.save_record(org, "execution", execution_id, execution)
            request.app.state.adapter._request(
                session.record, "PATCH", step["path"], json=step["body"]
            )
            readback = request.app.state.adapter._get(session.record, step["path"])
            if not compare_readback_fields(step["body"], readback):
                step.update(status="needs_reconciliation", error="readback_mismatch")
                break
            step.update(
                status="applied",
                receipt={k: readback.get(k) for k in ("id", "updatedAt", *step["body"])},
            )
            step.pop("error", None)
        except UpstreamIntegrationError:
            step.update(status="needs_reconciliation", error="upstream_result_uncertain")
            break
        finally:
            store.save_record(org, "execution", execution_id, execution)
    states = {s["status"] for s in execution["steps"]}
    execution["status"] = (
        "cancelled"
        if states == {"cancelled"}
        else "needs_reconciliation"
        if states & {"needs_reconciliation", "applying"}
        else "partially_applied"
        if states & {"pending", "blocked", "failed", "cancelled"}
        else "applied"
    )
    execution["message"] = (
        "See verified receipts and remaining conditional steps; "
        "approval is not physical completion."
    )
    return store.save_record(org, "execution", execution_id, execution)


@router.post("/executions/{execution_id}/reconcile")
def reconcile_execution(
    organization_id: str,
    execution_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(WRITE),
) -> dict:
    store = request.app.state.store
    token = store.acquire_lease(organization_id, "execution")
    if not token:
        raise HTTPException(409, "another_execution_in_progress")
    try:
        execution = _record(request, organization_id, "execution", execution_id)
        execution = _continue_execution(
            organization_id, execution_id, execution, request, session, token, write=False
        )
        snapshot = _load_snapshot(request, session, organization_id)
        if execution.get("sourceInvariant") != _source_invariant(snapshot, execution["steps"]):
            raise HTTPException(409, "source_changed_new_review_required")
        planning = _planning_for(request, snapshot)
        if planning.version != execution["assumptionsVersion"]:
            raise HTTPException(409, "planning_changed_new_review_required")
        analysis = store.get_analysis(organization_id, execution["analysisId"])
        _check_calibration(store, organization_id, analysis, snapshot.source_mode.value)
        return _continue_execution(
            organization_id, execution_id, execution, request, session, token
        )
    finally:
        store.release_lease(organization_id, "execution", token)


@router.get("/executions")
def executions(
    organization_id: str,
    request: Request,
    analysisId: str | None = None,
    session: AuthenticatedSession = Depends(READ),
) -> list[dict]:
    return [
        e
        for e in request.app.state.store.list_records(organization_id, "execution")
        if analysisId is None or e.get("analysisId") == analysisId
    ]


@router.post("/executions/{execution_id}/steps/{step_id}/complete")
def complete(
    organization_id: str,
    execution_id: str,
    step_id: str,
    request: Request,
    body: dict[str, Any],
    session: AuthenticatedSession = Depends(WRITE),
) -> dict:
    store = request.app.state.store
    token = store.acquire_lease(organization_id, "execution")
    if not token:
        raise HTTPException(409, "another_execution_in_progress")
    try:
        execution = _record(request, organization_id, "execution", execution_id)
        step = next((s for s in execution["steps"] if s["id"] == step_id), None)
        if not step or step["kind"] != "manual":
            raise HTTPException(422, "manual_step_required")
        if step["status"] == "confirmed":
            return execution
        if step["status"] != "pending":
            raise HTTPException(409, "step_not_pending")
        snapshot = _load_snapshot(request, session, organization_id)
        if not any(s["status"] == "applied" for s in execution["steps"]) and (
            snapshot.source_revision != execution["sourceRevision"]
        ):
            raise HTTPException(409, "source_changed_refresh_before_manual_confirmation")
        action = step["action"]
        if action.get("workerId") and not any(
            str(w["id"]) == str(action["workerId"]) for w in snapshot.workers
        ):
            raise HTTPException(409, "worker_no_longer_available_refresh_required")
        step.update(
            status="confirmed",
            confirmedBy=session.user.id,
            confirmedAt=datetime.now(UTC).isoformat(),
            note=str(body.get("note", ""))[:2000],
        )
        if all(s["status"] in {"confirmed", "applied"} for s in execution["steps"]):
            execution["status"] = "applied"
        execution = store.save_record(organization_id, "execution", execution_id, execution)
        if any(
            s["kind"] == "api" and s["status"] in {"pending", "blocked"} for s in execution["steps"]
        ):
            analysis = store.get_analysis(organization_id, execution["analysisId"])
            try:
                _check_calibration(store, organization_id, analysis, snapshot.source_mode.value)
                if execution.get("sourceInvariant") != _source_invariant(
                    snapshot, execution["steps"]
                ):
                    raise HTTPException(409, "source_changed_new_review_required")
                if _planning_for(request, snapshot).version != execution["assumptionsVersion"]:
                    raise HTTPException(409, "planning_changed_new_review_required")
            except HTTPException:
                execution["message"] = (
                    "Manual completion recorded. Remaining API steps need a fresh review "
                    "because context changed."
                )
                return store.save_record(organization_id, "execution", execution_id, execution)
            return _continue_execution(
                organization_id, execution_id, execution, request, session, token
            )
        return execution
    finally:
        store.release_lease(organization_id, "execution", token)


@router.post("/executions/{execution_id}/cancel")
def cancel_execution(
    organization_id: str,
    execution_id: str,
    request: Request,
    body: dict[str, Any],
    session: AuthenticatedSession = Depends(WRITE),
) -> dict:
    """Cancel only unperformed work; never pretend to roll back verified effects."""
    note = str(body.get("note", "")).strip()
    if not note or len(note) > 2000:
        raise HTTPException(422, "cancellation_reason_required")
    store = request.app.state.store
    token = store.acquire_lease(organization_id, "execution")
    if not token:
        raise HTTPException(409, "another_execution_in_progress")
    try:
        execution = _record(request, organization_id, "execution", execution_id)
        if any(s["status"] in {"applying", "needs_reconciliation"} for s in execution["steps"]):
            raise HTTPException(409, "reconcile_uncertain_writes_before_cancelling")
        for step in execution["steps"]:
            if step["status"] in {"pending", "blocked"}:
                step.update(
                    status="cancelled",
                    cancelledBy=session.user.id,
                    cancelledAt=datetime.now(UTC).isoformat(),
                    cancellationReason=note,
                )
        execution["status"] = (
            "partially_applied"
            if any(s["status"] in {"confirmed", "applied"} for s in execution["steps"])
            else "cancelled"
        )
        return store.save_record(organization_id, "execution", execution_id, execution)
    finally:
        store.release_lease(organization_id, "execution", token)


class ScheduleSettings(BaseModel):
    timezone: str = "Europe/Warsaw"
    enabled: bool = True
    analysisTime: str = "06:00"


@router.get("/settings")
def settings(
    organization_id: str, request: Request, session: AuthenticatedSession = Depends(READ)
) -> dict:
    stored = request.app.state.store.get_record(organization_id, "schedule", "daily") or {}
    stored.pop("sessionId", None)
    return {
        "timezone": "Europe/Warsaw",
        "enabled": False,
        "analysisTime": "06:00",
        **stored,
        "providers": {
            "weather": bool(request.app.state.settings.weatherapi_api_key),
            "routes": bool(request.app.state.settings.openrouteservice_api_key),
            "llm": bool(request.app.state.settings.llm_api_key),
        },
    }


@router.put("/settings")
def save_settings(
    organization_id: str,
    body: ScheduleSettings,
    request: Request,
    session: AuthenticatedSession = Depends(WRITE),
) -> dict:
    try:
        ZoneInfo(body.timezone)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(422, "invalid_timezone") from exc
    if body.analysisTime != "06:00":
        raise HTTPException(422, "daily_publication_is_06_00")
    saved = request.app.state.store.save_record(
        organization_id,
        "schedule",
        "daily",
        {**body.model_dump(), "sessionId": session.record.session_id},
    )
    saved.pop("sessionId", None)
    return saved


class Outcome(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    setup_hours: float = Field(alias="setupHours", ge=0, le=24)
    event_at: datetime = Field(alias="eventAt")
    note: str = Field(min_length=1, max_length=2000)
    worker_id: str = Field(alias="workerId")


@router.post("/executions/{execution_id}/outcomes")
def outcome(
    organization_id: str,
    execution_id: str,
    body: Outcome,
    request: Request,
    session: AuthenticatedSession = Depends(WRITE),
) -> dict:
    execution = _record(request, organization_id, "execution", execution_id)
    if execution.get("sourceMode") != session.user.source_mode.value:
        raise HTTPException(409, "outcome_source_mode_mismatch")
    if body.event_at.tzinfo is None or body.event_at > datetime.now(UTC):
        raise HTTPException(422, "observed_event_requires_past_aware_timestamp")
    match = next(
        (
            s
            for s in execution["steps"]
            if s["status"] == "confirmed"
            and s["action"].get("type") == "transfer"
            and s["action"].get("workerId") == body.worker_id
        ),
        None,
    )
    if match is None:
        raise HTTPException(422, "confirmed_transfer_required")
    if session.user.source_mode.value == "live":
        earliest = max(
            datetime.fromisoformat(execution["approvedAt"]),
            datetime.fromisoformat(match["action"]["startsAt"].replace("Z", "+00:00")),
        )
        if body.event_at < earliest:
            raise HTTPException(422, "outcome_precedes_approved_transfer")
    store = request.app.state.store
    token = store.acquire_lease(organization_id, "calibration")
    if not token:
        raise HTTPException(409, "calibration_in_progress")
    try:
        observation_id = f"{execution_id}:{match['id']}:setup"
        existing = store.get_record(organization_id, "outcome", observation_id)
        if existing:
            return existing
        source = session.user.source_mode.value
        prior = store.get_calibration(organization_id, source) or {
            "meanHours": float(match["action"].get("setupHours", 1)),
            "priorStrength": 2,
            "totalSampleCount": 0,
            "version": 0,
        }
        updated = update_setup_hours(
            prior_mean_hours=float(prior["meanHours"]),
            prior_strength=float(prior["priorStrength"]),
            previous_sample_count=int(prior["totalSampleCount"]),
            accepted_observation_hours=(body.setup_hours,),
        )
        return store.record_calibrated_outcome(
            organization_id,
            source,
            int(prior["version"]) + 1,
            {
                "sourceMode": source,
                "meanHours": updated.updated_mean_hours,
                "priorMeanHours": updated.prior_mean_hours,
                "priorStrength": updated.prior_strength,
                "totalSampleCount": updated.total_sample_count,
                "observationIds": [observation_id],
                "status": "calibrated",
                "updatedAt": datetime.now(UTC).isoformat(),
            },
            observation_id,
            {
                **body.model_dump(mode="json", by_alias=True),
                "executionId": execution_id,
                "recordedBy": session.user.id,
                "sourceMode": source,
                "evidenceClass": "manager_attested_direct_measurement",
                "message": (
                    "Measured setup duration updates later transfer forecasts; "
                    "this does not prove global optimality."
                ),
            },
        )
    finally:
        store.release_lease(organization_id, "calibration", token)
