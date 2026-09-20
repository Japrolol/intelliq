"""Shared portfolio pipeline used by HTTP requests and durable jobs."""

import copy
import hashlib
import json
import math
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from src.app.domain.contracts import AnalysisRequest, PlanningInputs
from src.app.domain.engine_bridge import evaluate_portfolio
from src.app.domain.readiness import readiness_issues
from src.app.domain.status import without_archived_projects

TASK_STATUS_DONE = "done"
TASK_STATUS_CANCELED = "canceled"
ESTIMATE_STATUS_ESTIMATED = "estimated"
SOURCE_DEFAULT_PRIOR = "default_prior_v1"
SOURCE_TIMECUE_ESTIMATED_MINUTES = "timecue_estimated_minutes"
SOURCE_TIMECUE_PLANNED_WINDOW = "timecue_planned_window"
DEFAULT_MOST_LIKELY_HOURS = 8.0
FIELD_REMAINING_PERSON_HOURS = "remainingPersonHours"


def execution_revision(store: Any, org_id: str) -> str:
    versions = sorted(
        (e["id"], e.get("updatedAt", "")) for e in store.list_records(org_id, "execution")
    )
    return hashlib.sha256(json.dumps(versions).encode()).hexdigest()


def approval_eligible(strategy: dict) -> bool:
    return (
        strategy.get("id") != "baseline"
        and bool(strategy.get("actions"))
        and strategy.get("feasible") is True
        and strategy.get("rejectionReasons") == []
        and strategy.get("approvalBlockers", []) == []
        and strategy.get("permitsApproval", True) is True
    )


def apply_estimates(planning: PlanningInputs) -> list[dict[str, Any]]:
    """Fill missing remaining hours from Timecue fields; never infer them from worklogs.

    Confirmed overlay remainingPersonHours always wins. Otherwise Timecue's
    estimatedMinutes is converted to a triangular prior, then the planned
    start/end window, then a visible 4/8/16 default. All fallbacks stay
    labelled estimated so they cannot look like confirmed planning.
    """
    assumptions: list[dict[str, Any]] = []
    for task in planning.tasks:
        if task.get(FIELD_REMAINING_PERSON_HOURS):
            continue
        if task.get("status") in {TASK_STATUS_DONE, TASK_STATUS_CANCELED}:
            task[FIELD_REMAINING_PERSON_HOURS] = _effort_triangle(0.0)
            continue
        hours, source, detail = _estimated_remaining_hours(task)
        task[FIELD_REMAINING_PERSON_HOURS] = _effort_triangle(hours)
        assumptions.append(
            {
                "entityId": task["id"],
                "field": FIELD_REMAINING_PERSON_HOURS,
                "status": ESTIMATE_STATUS_ESTIMATED,
                "source": source,
                "detail": detail,
            }
        )
    return assumptions


def _effort_triangle(most_likely: float) -> dict[str, float]:
    return {
        "optimistic": most_likely / 2,
        "mostLikely": most_likely,
        "pessimistic": most_likely * 2,
    }


def _estimated_remaining_hours(task: Mapping[str, Any]) -> tuple[float, str, str]:
    """Choose a visible remaining-hours prior from Timecue layout fields."""
    minutes = task.get("estimatedMinutes")
    if isinstance(minutes, (int, float)) and minutes > 0 and math.isfinite(minutes):
        hours = float(minutes) / 60.0
        return (
            hours,
            SOURCE_TIMECUE_ESTIMATED_MINUTES,
            (
                f"No confirmed remaining hours: used Timecue estimatedMinutes "
                f"({int(minutes)} min → {hours / 2:g} / {hours:g} / {hours * 2:g} person-hours). "
                "This is elapsed-time, not remaining crew effort. Confirm before "
                "relying on this forecast."
            ),
        )
    window_hours = _planned_window_hours(task)
    if window_hours is not None:
        return (
            window_hours,
            SOURCE_TIMECUE_PLANNED_WINDOW,
            (
                f"No confirmed remaining hours: used the Timecue planned start/end "
                f"window ({window_hours:g} elapsed hours → "
                f"{window_hours / 2:g} / {window_hours:g} / {window_hours * 2:g}). "
                "Elapsed calendar time is not remaining crew effort. Confirm before "
                "relying on this forecast."
            ),
        )
    hours = DEFAULT_MOST_LIKELY_HOURS
    return (
        hours,
        SOURCE_DEFAULT_PRIOR,
        (
            "No effort estimate: assumed 4 / 8 / 16 person-hours. "
            "Confirm before relying on this forecast."
        ),
    )


def _planned_window_hours(task: Mapping[str, Any]) -> float | None:
    start = _parse_iso_datetime(task.get("plannedStartAt"))
    end = _parse_iso_datetime(task.get("plannedEndAt"))
    if start is None or end is None or end <= start:
        return None
    hours = (end - start).total_seconds() / 3600.0
    if hours <= 0 or not math.isfinite(hours):
        return None
    return hours


def _parse_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


ASSUMPTION_SOURCE_LABELS = {
    SOURCE_TIMECUE_ESTIMATED_MINUTES: "Timecue estimatedMinutes",
    SOURCE_TIMECUE_PLANNED_WINDOW: "Timecue planned start/end",
    SOURCE_DEFAULT_PRIOR: "Default remaining-hours prior",
}


def _assumption_source(
    task: Mapping[str, Any],
    assumed_inputs: list[dict[str, Any]],
    planning_version: int,
) -> str:
    assumed = next((item for item in assumed_inputs if item["entityId"] == task["id"]), None)
    if assumed is not None:
        return ASSUMPTION_SOURCE_LABELS.get(str(assumed["source"]), str(assumed["source"]))
    if planning_version:
        return "Manager planning overlay"
    return "Source planning inputs"


def planning_for(store: Any, snapshot: Any) -> PlanningInputs:
    """Keep live entities authoritative; attach only missing planning attributes."""
    saved = store.get_planning(snapshot.organization_id)
    if saved is None:
        projects, tasks = without_archived_projects(list(snapshot.projects), list(snapshot.tasks))
        return PlanningInputs(
            version=0,
            projects=projects,
            tasks=tasks,
            workers=snapshot.workers,
            transfers=snapshot.transfer_assumptions,
            reservations=snapshot.reservations,
        )
    overlay = PlanningInputs.model_validate(saved)
    if snapshot.source_mode.value == "fixture":
        overlay.projects, overlay.tasks = without_archived_projects(
            overlay.projects, overlay.tasks
        )
        return overlay
    for field in ("projects", "tasks", "workers"):
        by_id = {str(item["id"]): item for item in getattr(overlay, field)}
        merged = []
        for source in getattr(snapshot, field):
            assumptions = by_id.get(str(source["id"]), {})
            # Operational values from Timecue must never be replaced by stale copies.
            item = {**assumptions, **{k: v for k, v in source.items() if v is not None}}
            if field == "tasks" and source.get("plannedStartAt"):
                # A confirmed readiness gate may delay the canonical plan; it must
                # not silently move Timecue's scheduled start earlier.
                starts = [source["plannedStartAt"]]
                if assumptions.get("earliestStartAt"):
                    starts.append(assumptions["earliestStartAt"])
                item["earliestStartAt"] = max(
                    starts,
                    key=lambda value: datetime.fromisoformat(str(value).replace("Z", "+00:00")),
                )
            merged.append(item)
        setattr(overlay, field, merged)
    overlay.projects, overlay.tasks = without_archived_projects(overlay.projects, overlay.tasks)
    overlay.reservations = snapshot.reservations
    return overlay


def run_analysis(
    state: Any,
    org_id: str,
    session: Any,
    request_body: dict,
    progress: Callable[..., None] | None = None,
) -> dict:
    """Serialize portfolio publication, including direct HTTP and queued calls."""
    token = state.store.acquire_lease(org_id, "portfolio_analysis", seconds=900)
    if not token:
        raise HTTPException(409, "portfolio_analysis_already_running")
    try:
        return _run_analysis(state, org_id, session, request_body, progress)
    finally:
        state.store.release_lease(org_id, "portfolio_analysis", token)


def _run_analysis(
    state: Any,
    org_id: str,
    session: Any,
    request_body: dict,
    progress: Callable[..., None] | None = None,
) -> dict:
    """Freeze one source snapshot, enrich once, simulate, and persist the result."""
    from src.app.domain.readiness import has_cycle
    from src.app.integrations.context import enrich_context
    from src.app.integrations.evidence_bridge import EvidenceIntegrationError
    from src.app.services.analysis_payload import _engine_payload, _frontend_engine_result
    from src.app.services.worklog_evidence import ingest_worklogs
    from src.app.services.analysis_memory import (
        retrieve_analysis_memory,
        calibrate_transfer_context,
    )
    from src.app.services.candidate_proposals import propose_priority_candidates
    from src.app.simulation.presentation import build_graph, enrich_result

    permissions = {"projects.read", "tasks.read", "workers.read", "worklogs.read"}
    if not session.user.app_admin and (
        org_id not in session.user.organization_ids
        or not permissions.issubset(session.user.organization_permissions.get(org_id, []))
    ):
        raise HTTPException(403, "analysis_organization_access_denied")
    run_key = request_body.get("idempotencyKey")
    if run_key:
        published = state.store.get_record(org_id, "analysis_run", str(run_key))
        if published:
            existing = state.store.get_analysis(org_id, published["analysisId"])
            if existing:
                return existing
        existing = next(
            (
                a
                for a in state.store.list_analyses(org_id)
                if a.get("runKey") == run_key and a.get("status") == "completed"
            ),
            None,
        )
        if existing:
            return existing
    body = AnalysisRequest.model_validate(request_body)
    decisions_revision = execution_revision(state.store, org_id)
    if progress:
        progress("reading_timecue", 15)
    snapshot = state.adapter.snapshot(session.record, org_id)
    state.store.save_snapshot(snapshot.model_dump(mode="json", by_alias=True))
    planning = planning_for(state.store, snapshot)
    if progress:
        progress("validating_inputs", 25)
    assumed_inputs = apply_estimates(planning)
    exceeded_limits = [
        f"{count} {name} (configured limit: {limit})"
        for name, count, limit in (
            ("projects", len(planning.projects), state.settings.simulation_max_projects),
            ("workers", len(planning.workers), state.settings.simulation_max_workers),
            ("tasks", len(planning.tasks), state.settings.simulation_max_tasks),
        )
        if limit > 0 and count > limit
    ]
    if exceeded_limits:
        raise HTTPException(
            422,
            {
                "code": "portfolio_size_limit",
                "message": "Portfolio size exceeds configuration: " + "; ".join(exceeded_limits),
            },
        )
    if body.project_id is None:
        focus = sorted(
            planning.projects,
            key=lambda p: (
                -float(p.get("priority") or 0),
                str(p.get("targetDate") or "9999"),
                str(p["id"]),
            ),
        )
        body.project_id = str(focus[0]["id"]) if focus else None
    if body.project_id and not any(str(p["id"]) == body.project_id for p in planning.projects):
        raise HTTPException(404, "project_not_found")
    if (
        body.expected_snapshot_revision
        and body.expected_snapshot_revision != snapshot.source_revision
    ):
        raise HTTPException(409, "snapshot_revision_conflict")
    issues = readiness_issues(snapshot, planning)
    if has_cycle(planning.tasks):
        issues.append({"code": "dependency_cycle", "message": "Task dependencies contain a cycle."})
    if issues:
        return {
            "status": "needs_inputs",
            "readinessIssues": issues,
            "snapshotId": snapshot.snapshot_id,
            "sourceRevision": snapshot.source_revision,
        }
    calibration = state.store.get_calibration(org_id, snapshot.source_mode.value)
    payload = _engine_payload(
        snapshot,
        planning,
        body,
        setup_hours=None,
        samples=min(
            state.settings.simulation_default_samples,
            state.settings.simulation_max_samples,
        ),
    )
    payload["horizonDays"] = max(28, payload["horizonDays"])
    payload["decisionScope"] = "portfolio" if request_body.get("projectId") is None else "project"
    executions = state.store.list_records(org_id, "execution")
    outcomes = state.store.list_records(org_id, "outcome")
    learning_updates = calibrate_transfer_context(
        payload, executions, outcomes, snapshot.source_mode.value
    )
    knowledge_context = retrieve_analysis_memory(
        state.store, org_id, payload, snapshot.source_mode.value
    )
    payload["implementedActions"] = list(
        {
            json.dumps(step["action"], sort_keys=True): step["action"]
            for execution in executions
            if execution.get("sourceMode") == snapshot.source_mode.value
            for step in execution.get("steps", [])
            if step.get("status") == "confirmed"
            and step.get("confirmedAt")
            and step.get("kind") == "manual"
        }.values()
    )
    if progress:
        progress("reviewing_evidence", 35)
    try:
        evidence_status = ingest_worklogs(state, snapshot)
    except EvidenceIntegrationError:
        evidence_status = {
            "status": "failed",
            "detail": "Evidence provider unavailable; no new claims applied.",
        }
    context_input = copy.deepcopy(payload)
    if progress:
        progress("fetching_context", 45)
    context = enrich_context(payload, state.settings)
    payload = context["planning"]
    proposals = propose_priority_candidates(payload, knowledge_context, state.settings)
    payload["proposedCandidates"] = proposals["candidates"]
    if progress:
        progress("simulating", 55)
    live_nodes: dict[str, dict[str, Any]] = {}
    project_names = {str(p["id"]): p.get("name", "Project") for p in payload.get("projects", [])}

    def simulation_progress(event: dict[str, Any]) -> None:
        if progress is None:
            return
        node_id = str(event["id"])
        node = live_nodes.setdefault(node_id, {"id": node_id})
        if "label" not in node or ("actions" in event and "actions" not in node):
            presented = enrich_result({"candidateEvaluations": [event]}, payload)[
                "candidateEvaluations"
            ]
            presented_event = presented[0] if presented else {}
            if "label" not in node:
                node["label"] = (
                    "Current plan"
                    if node_id == "baseline"
                    else presented_event.get("summary", "Evaluating an option")
                )
            if "actions" in event:
                node["actions"] = presented_event.get("actions") or event["actions"]
        node.update(
            {
                key: event[key]
                for key in ("status", "completedSamples", "totalSamples", "feasible")
                if key in event
            }
        )
        if event.get("outcomesByProject"):
            node["outcomes"] = [
                {
                    "projectId": project_id,
                    "projectName": project_names.get(project_id, "Project"),
                    **{
                        key: outcome.get(key)
                        for key in ("delayProbability", "finishP50", "unfinishedCount")
                    },
                }
                for project_id, outcome in event["outcomesByProject"].items()
            ]
        total = int(event.get("totalCandidates", 0))
        completed = int(event.get("completedCandidates", 0))
        progress(
            "simulating",
            55 + int(25 * completed / max(total, 1)),
            {
                "samplesPerCandidate": event.get("totalSamples", 0),
                "totalCandidates": total,
                "completedCandidates": completed,
                "nodes": list(live_nodes.values()),
            },
        )

    if state.settings.simulation_process_enabled and (
        state.settings.jobs_enabled or snapshot.source_mode.value == "live"
    ):
        from src.app.services.simulation_executor import (
            SimulationExecutionError,
            SimulationTimeoutError,
            run_simulation,
        )

        try:
            numerical = run_simulation(
                payload,
                process_enabled=True,
                timeout_seconds=state.settings.simulation_timeout_seconds,
                progress=simulation_progress,
            )
        except SimulationTimeoutError as exc:
            raise HTTPException(503, "simulation_time_limit") from exc
        except SimulationExecutionError as exc:
            raise HTTPException(503, "simulation_process_unavailable") from exc
    else:
        numerical = evaluate_portfolio(payload, progress=simulation_progress)
    result = enrich_result(_frontend_engine_result(numerical, body.project_id), payload)
    strategies = result.get("strategies", [])
    for strategy in strategies:
        strategy["permitsApproval"] = approval_eligible(strategy)
        strategy["approvalBlockers"] = strategy.get("rejectionReasons", ["missing_feasibility"])
    eligible = [s for s in strategies if approval_eligible(s)]
    recommended = next((s for s in eligible if s.get("rank") == 1), None)
    if recommended is None:
        recommended = next((s for s in eligible if "balanced" in s.get("strategyRoles", [])), None)
    if recommended is None:
        recommended = next((s for s in eligible if s.get("id") != "baseline"), None)
    if recommended is None:
        recommended = next(iter(eligible), {})
    completed = datetime.now(UTC).isoformat()
    evidence = [
        {
            "id": claim["id"],
            "label": claim.get("summary", "Confirmed evidence"),
            "sourceRevision": claim.get("sourceRevision"),
            "confirmed": True,
            "detail": "Accepted source evidence; planning changes still require explicit approval.",
        }
        for claim in knowledge_context["facts"]
    ]
    assumptions = [
        {
            "id": f"effort:{task['id']}",
            "label": f"{task.get('title', task['id'])}: remaining effort",
            "value": task.get("remainingPersonHours"),
            "confirmed": not any(a["entityId"] == task["id"] for a in assumed_inputs),
            "source": _assumption_source(task, assumed_inputs, planning.version),
        }
        for task in planning.tasks
    ]
    analysis = {
        **result,
        "organizationId": org_id,
        "projects": planning.projects,
        "projectOutcomes": result.get("baselineByProject", {}),
        "selectedScenarioId": recommended.get("id"),
        "recommendation": {
            "strategyId": recommended.get("id"),
            "summary": recommended.get("summary"),
            "scope": "Best ranked among evaluated bounded scenarios, not a global optimum.",
        },
        "runKey": run_key,
        "sourceMode": snapshot.source_mode.value,
        "snapshotId": snapshot.snapshot_id,
        "sourceRevision": snapshot.source_revision,
        "assumptionsVersion": planning.version,
        "executionRevision": decisions_revision,
        "calibrationVersion": str(calibration["version"]) if calibration else "initial",
        "calibration": calibration or {"version": "initial", "status": "assumed"},
        "decisionContext": {
            "retrievedKnowledge": knowledge_context,
            "learningUpdates": learning_updates,
            "recentExecutions": [
                {
                    "id": execution["id"],
                    "status": execution["status"],
                    "confirmedActions": [
                        step["action"]
                        for step in execution.get("steps", [])
                        if step.get("status") in {"confirmed", "applied"}
                    ],
                }
                for execution in executions[:5]
                if execution.get("sourceMode") == snapshot.source_mode.value
            ],
            "recentOutcomes": [
                {
                    key: observation.get(key)
                    for key in ("id", "setupHours", "eventAt", "executionId")
                }
                for observation in outcomes[:5]
                if observation.get("sourceMode") == snapshot.source_mode.value
            ],
            "calibration": calibration or {"version": "initial", "status": "assumed"},
        },
        "weatherRevision": context["revision"],
        "providerStatus": context["status"],
        "evidenceStatus": evidence_status,
        "contextWarnings": context["warnings"],
        "estimatedInputs": assumed_inputs,
        "assumptions": assumptions,
        "sourceEvidence": evidence,
        "knowledgeContext": knowledge_context,
        "candidateProposals": proposals,
        "learningUpdates": learning_updates,
        "asOf": snapshot.as_of.isoformat(),
        "mode": body.mode.value,
        "modelVersion": result.get("modelVersion", "portfolio-v4"),
        "projectId": body.project_id,
        "status": "completed",
        "completedAt": completed,
        "readinessIssues": [],
    }
    from src.app.services.explanations import build_explanation, explain_options

    if progress:
        progress("explaining", 85)
    analysis["explanation"] = build_explanation(
        analysis,
        payload,
        state.settings,
        external_text_consent=state.settings.allow_external_text_processing,
    )
    explain_options(analysis, payload, state.settings)
    if progress:
        progress("publishing", 95)
    analysis_id = state.store.create_analysis(
        org_id,
        session.user.id,
        analysis,
        "completed",
        frozen_input={
            "planning": payload,
            "contextInput": context_input,
            "graph": build_graph(payload),
        },
    )
    from src.app.services.knowledge_context import publish_analysis_context

    publish_analysis_context(state.store, org_id, {**analysis, "id": analysis_id})
    from src.app.services.knowledge_embeddings import refresh_embeddings

    refresh_embeddings(state.store, org_id, state.settings)
    schedule = state.store.get_record(org_id, "schedule", "daily") or {
        "enabled": True,
        "timezone": planning.projects[0].get("timezone", "Europe/Warsaw"),
        "analysisTime": "06:00",
    }
    state.store.save_record(
        org_id, "schedule", "daily", {**schedule, "sessionId": session.record.session_id}
    )
    return state.store.get_analysis(org_id, analysis_id) or {**analysis, "id": analysis_id}
