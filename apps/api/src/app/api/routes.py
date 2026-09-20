"""HTTP API for the authenticated IntelliQ vertical slice."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime
from math import isclose
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.exc import IntegrityError

from src.app.api.auth_service import AuthenticatedSession, AuthenticationFailure
from src.app.api.dependencies import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    current_session,
    require_org_permissions,
    validate_csrf,
    validate_origin,
)
from src.app.decisions.calibration import update_setup_hours
from src.app.decisions.checkpoints import Comparator, evaluate_checkpoint
from src.app.domain.contracts import (
    AnalysisMode,
    AnalysisRequest,
    CheckpointCheckRequest,
    DecisionRequest,
    ImplementationObservationRequest,
    PlanningInputs,
    PortfolioSnapshot,
    ReplayRequest,
    SetupDurationObservationRequest,
    SignalReviewRequest,
    SignalReviewStatus,
    SourceMode,
)
from src.app.domain.engine_bridge import (
    SimulationEngineUnavailable,
    SimulationInputError,
    evaluate_portfolio,
)
from src.app.domain.readiness import readiness_issues
from src.app.integrations.evidence_bridge import EvidenceIntegrationError, extract_evidence
from src.app.integrations.timecue import UpstreamIntegrationError
from src.app.services.analysis_payload import _engine_payload, _frontend_engine_result

router = APIRouter(prefix="/api")

FIXTURE_REPLAY_LABEL = "Synthetic historical replay — fixture data only"
MANAGER_ATTESTED_EVIDENCE = "manager_attested"
VERIFIED_DIRECT_OBSERVATION = "verified_direct_observation"


@router.get("/runtime")
def runtime(request: Request) -> dict[str, str | bool]:
    """Expose only the configured data source so login can label demo mode."""

    source_mode = request.app.state.settings.data_mode
    return {"sourceMode": source_mode, "synthetic": source_mode == SourceMode.FIXTURE.value}


@router.post("/auth/login")
def login(payload: dict[str, str], request: Request, response: Response) -> dict[str, Any]:
    """Proxy credentials to Timecue or create an explicitly synthetic fixture session."""

    validate_origin(request)
    email = payload.get("email", "")
    password = payload.get("password", "")
    if not email or not password:
        raise HTTPException(status_code=422, detail="email_and_password_required")
    try:
        session = request.app.state.auth.login(email, password)
    except AuthenticationFailure as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc
    _set_local_cookies(response, request, session)
    return {
        "user": _user_response(session),
        "sourceMode": session.user.source_mode.value,
        "synthetic": session.user.synthetic,
    }


@router.post("/auth/refresh")
def refresh(request: Request, response: Response) -> dict[str, Any]:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="authentication_required")
    record = request.app.state.store.get_session(token)
    if record is None:
        raise HTTPException(status_code=401, detail="session_not_found")
    validate_csrf(request, record.csrf_token)
    try:
        session = request.app.state.auth.refresh(record)
    except AuthenticationFailure as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc
    _set_local_cookies(response, request, session)
    return {
        "user": _user_response(session),
        "sourceMode": session.user.source_mode.value,
        "synthetic": session.user.synthetic,
    }


@router.get("/auth/me")
def me(session: AuthenticatedSession = Depends(current_session)) -> dict[str, Any]:
    return {
        "user": _user_response(session),
        "sourceMode": session.user.source_mode.value,
        "synthetic": session.user.synthetic,
    }


@router.get("/auth/csrf")
def csrf(
    request: Request, response: Response, session: AuthenticatedSession = Depends(current_session)
) -> dict[str, str]:
    """Expose the current non-secret double-submit token to the browser client."""

    settings = request.app.state.settings
    response.set_cookie(
        CSRF_COOKIE,
        session.record.csrf_token,
        max_age=60 * 60 * 8,
        httponly=False,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        domain=settings.cookie_domain,
        path="/",
    )
    return {"token": session.record.csrf_token, "csrfToken": session.record.csrf_token}


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        record = request.app.state.store.get_session(token)
        if record is not None:
            validate_csrf(request, record.csrf_token)
            request.app.state.auth.logout(record)
    _clear_local_cookies(response, request)


@router.get("/organizations")
def organizations(
    request: Request, session: AuthenticatedSession = Depends(current_session)
) -> list[dict[str, Any]]:
    try:
        if request.app.state.settings.data_mode == "fixture":
            return request.app.state.fixture.organizations(session.record)
        return request.app.state.adapter.organizations(session.record)
    except UpstreamIntegrationError as exc:
        raise _upstream_http_error(exc) from exc


@router.get("/organizations/{organization_id}/portfolio")
def portfolio(
    organization_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(
        require_org_permissions("projects.read", "tasks.read", "workers.read", "worklogs.read")
    ),
) -> dict[str, Any]:
    snapshot = _load_snapshot(request, session, organization_id)
    planning = _planning_for(request, snapshot)
    issues = readiness_issues(snapshot, planning)
    return {
        "sourceMode": snapshot.source_mode.value,
        "asOf": snapshot.as_of.isoformat(),
        "snapshotId": snapshot.snapshot_id,
        "sourceRevision": snapshot.source_revision,
        "projects": snapshot.projects,
        "readinessIssues": issues,
        "warnings": snapshot.warnings,
        "readinessStatus": "ready" if not issues else "needs_inputs",
        "synthetic": snapshot.source_mode.value == "fixture",
    }


@router.get("/organizations/{organization_id}/planning-inputs")
def get_planning_inputs(
    organization_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(
        require_org_permissions("projects.read", "tasks.read", "workers.read", "worklogs.read")
    ),
) -> dict[str, Any]:
    snapshot = _load_snapshot(request, session, organization_id)
    return _planning_for(request, snapshot).model_dump(mode="json", by_alias=True)


@router.put("/organizations/{organization_id}/planning-inputs")
def put_planning_inputs(
    organization_id: str,
    body: dict[str, Any],
    request: Request,
    session: AuthenticatedSession = Depends(
        require_org_permissions(
            "projects.read",
            "tasks.read",
            "workers.read",
            "worklogs.read",
            "tasks.write",
            write=True,
        )
    ),
) -> dict[str, Any]:
    try:
        _validate_planning_payload_shape(body)
        expected = body.get("expectedVersion")
        inputs = PlanningInputs.model_validate(
            {key: value for key, value in body.items() if key != "expectedVersion"}
        )
        _validate_planning_ids(request, session, organization_id, inputs)
        saved = request.app.state.store.save_planning(
            organization_id, inputs.model_dump(mode="json", by_alias=True), expected
        )
    except ValueError as exc:
        if str(exc).startswith("planning_version_conflict:"):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "planning_version_conflict",
                    "currentVersion": int(str(exc).split(":", 1)[1]),
                },
            ) from exc
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return saved


@router.post("/organizations/{organization_id}/analyses")
def create_analysis(
    organization_id: str,
    body: AnalysisRequest,
    request: Request,
    session: AuthenticatedSession = Depends(
        require_org_permissions(
            "projects.read", "tasks.read", "workers.read", "worklogs.read", write=True
        )
    ),
) -> dict[str, Any]:
    from src.app.services.portfolio import run_analysis

    if request.app.state.settings.jobs_enabled:
        from src.app.workers.service import enqueue_analysis

        return enqueue_analysis(
            request.app.state.store,
            organization_id,
            session.record.session_id,
            body.model_dump(by_alias=True, mode="json"),
        )
    try:
        return run_analysis(
            request.app.state, organization_id, session, body.model_dump(by_alias=True, mode="json")
        )
    except UpstreamIntegrationError as exc:
        raise _upstream_http_error(exc) from exc
    except SimulationEngineUnavailable as exc:
        raise HTTPException(
            status_code=503, detail={"code": "simulation_engine_unavailable", "message": str(exc)}
        ) from exc
    except SimulationInputError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "simulation_input_invalid", "message": str(exc)}
        ) from exc


@router.get("/organizations/{organization_id}/analyses")
def list_analyses(
    organization_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(
        require_org_permissions("projects.read", "tasks.read", "workers.read", "worklogs.read")
    ),
) -> list[dict[str, Any]]:
    return request.app.state.store.list_analyses(organization_id)


@router.get("/organizations/{organization_id}/analyses/{analysis_id}")
def get_analysis(
    organization_id: str,
    analysis_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(
        require_org_permissions("projects.read", "tasks.read", "workers.read", "worklogs.read")
    ),
) -> dict[str, Any]:
    analysis = request.app.state.store.get_analysis(organization_id, analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="analysis_not_found")
    return analysis


@router.get("/organizations/{organization_id}/projects/{project_id}/signals")
def signals(
    organization_id: str,
    project_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(
        require_org_permissions("projects.read", "tasks.read", "workers.read", "worklogs.read")
    ),
) -> dict[str, Any]:
    values = request.app.state.store.list_signals(organization_id, project_id)
    return {
        "projectId": project_id,
        "reviewed": [item for item in values if item.get("status") == "confirmed"],
        "pending": [
            item
            for item in values
            if item.get("status") not in {"confirmed", "rejected", "superseded"}
        ],
        "signals": values,
    }


@router.post("/organizations/{organization_id}/projects/{project_id}/signals/refresh")
def refresh_signals(
    organization_id: str,
    project_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(
        require_org_permissions("projects.read", "tasks.read", "worklogs.read", write=True)
    ),
) -> dict[str, Any]:
    snapshot = _load_snapshot(request, session, organization_id)
    task_ids = {
        str(task["id"]) for task in snapshot.tasks if str(task.get("projectId")) == project_id
    }
    _ingest_signals(request, snapshot, task_ids, project_id)
    return {"signals": request.app.state.store.list_signals(organization_id, project_id)}


@router.post("/organizations/{organization_id}/signals/{signal_id}/review")
def review_signal(
    organization_id: str,
    signal_id: str,
    body: SignalReviewRequest,
    request: Request,
    session: AuthenticatedSession = Depends(
        require_org_permissions(
            "projects.read",
            "tasks.read",
            "workers.read",
            "worklogs.read",
            "tasks.write",
            write=True,
        )
    ),
) -> dict[str, Any]:
    if body.planning_change is not None and body.status != SignalReviewStatus.CONFIRMED:
        raise HTTPException(status_code=422, detail="planning_change_requires_confirmed_signal")
    snapshot = _load_snapshot(request, session, organization_id)
    values = request.app.state.store.list_signals(organization_id)
    current = next((item for item in values if item.get("id") == signal_id), None)
    if current is None:
        raise HTTPException(status_code=404, detail="signal_not_found")
    if current.get("status") != SignalReviewStatus.PROPOSED:
        raise HTTPException(status_code=409, detail="signal_already_reviewed_or_superseded")
    source = next(
        (item for item in snapshot.worklogs if str(item.get("id")) == current.get("sourceId")),
        None,
    )
    if source is None or (
        str(source.get("updatedAt") or source.get("knownAt") or "unknown")
        != current.get("sourceRevision")
        or source.get("description") != current.get("sourceText")
    ):
        raise HTTPException(status_code=409, detail="signal_source_changed")
    if body.planning_change is not None and (
        body.planning_change.get("taskId") != current.get("taskId")
    ):
        raise HTTPException(status_code=422, detail="planning_change_task_mismatch")
    updated = {
        **current,
        "status": body.status.value,
        "reviewedBy": session.user.id,
        "reviewedAt": datetime.now(UTC).isoformat(),
    }
    if body.planning_change is not None:
        _apply_planning_change(request, session, organization_id, body.planning_change)
        updated["planningChange"] = body.planning_change
    request.app.state.store.review_signal(organization_id, signal_id, body.status.value, updated)
    return updated


@router.post("/organizations/{organization_id}/decisions", status_code=status.HTTP_201_CREATED)
def create_decision(
    organization_id: str,
    body: DecisionRequest,
    request: Request,
    session: AuthenticatedSession = Depends(
        require_org_permissions(
            "projects.read",
            "tasks.read",
            "workers.read",
            "worklogs.read",
            "tasks.assign",
            write=True,
        )
    ),
) -> dict[str, Any]:
    existing = request.app.state.store.get_decision_by_idempotency(
        organization_id, body.idempotency_key
    )
    if existing is not None:
        if (
            existing.get("analysisId") != body.analysis_id
            or existing.get("strategyId") != body.strategy_id
        ):
            raise HTTPException(status_code=409, detail="idempotency_key_conflict")
        return existing
    analysis = request.app.state.store.get_analysis(organization_id, body.analysis_id)
    if analysis is None or analysis.get("status") != "completed":
        raise HTTPException(status_code=404, detail="completed_analysis_not_found")
    snapshot = _load_snapshot(request, session, organization_id)
    planning = _planning_for(request, snapshot)
    _ensure_analysis_fresh(analysis, snapshot, planning, request, organization_id)
    strategy = next(
        (item for item in analysis.get("strategies", []) if item.get("id") == body.strategy_id),
        None,
    )
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy_not_found")
    if any(
        str(action.get("type", "")).lower() in {"priority", "reprioritize", "change_priority"}
        for action in strategy.get("actions", [])
        if isinstance(action, dict)
    ):
        permissions = set(session.user.organization_permissions.get(organization_id, []))
        if "tasks.write" not in permissions:
            raise HTTPException(
                status_code=403,
                detail={"code": "missing_permissions", "permissions": ["tasks.write"]},
            )
    payload = {
        "organizationId": organization_id,
        "approvedBy": session.user.id,
        "approvedAt": datetime.now(UTC).isoformat(),
        "sourceMode": session.user.source_mode.value,
        "synthetic": session.user.synthetic,
        "analysisId": body.analysis_id,
        "strategyId": body.strategy_id,
        "snapshotId": analysis.get("snapshotId"),
        "sourceRevision": analysis.get("sourceRevision"),
        "assumptionsVersion": analysis.get("assumptionsVersion"),
        "calibrationVersion": analysis.get("calibrationVersion"),
        "modelVersion": analysis.get("modelVersion"),
        "weatherRevision": analysis.get("weatherRevision"),
        "simulationSeeds": {
            "seed": analysis.get("seed"),
            "sampleCount": analysis.get("sampleCount"),
        },
        "baselineOutcomes": analysis.get("baselineByProject", []),
        "selectedOutcomes": strategy.get("outcomesByProject", []),
        "exactActions": strategy.get("actions", []),
        "affectedProjectIds": strategy.get("affectedProjectIds", []),
        "guardrails": list(body.guardrails),
        "guardrailChecks": strategy.get("guardrailChecks", {}),
        "checkpoints": [
            checkpoint.model_dump(mode="json", by_alias=True) for checkpoint in body.checkpoints
        ],
        "managerRationale": body.rationale,
        "decisionState": "approved",
        "implementationState": "not_applied",
        "message": "Decision recorded. Not applied to Timecue.",
        "idempotencyKey": body.idempotency_key,
    }
    try:
        return request.app.state.store.create_decision(organization_id, session.user.id, payload)
    except IntegrityError:
        existing = request.app.state.store.get_decision_by_idempotency(
            organization_id, body.idempotency_key
        )
        if existing is not None:
            return existing
        raise HTTPException(status_code=409, detail="decision_conflict") from None


@router.get("/organizations/{organization_id}/decisions")
def list_decisions(
    organization_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(
        require_org_permissions("projects.read", "tasks.read", "workers.read", "worklogs.read")
    ),
) -> list[dict[str, Any]]:
    return [
        _decision_response(request, organization_id, decision)
        for decision in request.app.state.store.list_decisions(organization_id)
    ]


@router.get("/organizations/{organization_id}/decisions/{decision_id}")
def get_decision(
    organization_id: str,
    decision_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(
        require_org_permissions("projects.read", "tasks.read", "workers.read", "worklogs.read")
    ),
) -> dict[str, Any]:
    decision = request.app.state.store.get_decision(organization_id, decision_id)
    if decision is None:
        raise HTTPException(status_code=404, detail="decision_not_found")
    return _decision_response(request, organization_id, decision)


@router.post("/organizations/{organization_id}/decisions/{decision_id}/replay")
def replay_decision(
    organization_id: str,
    decision_id: str,
    body: ReplayRequest,
    request: Request,
    session: AuthenticatedSession = Depends(
        require_org_permissions(
            "projects.read",
            "tasks.read",
            "workers.read",
            "worklogs.read",
            write=True,
        )
    ),
) -> dict[str, Any]:
    """Run a deterministic, fixture-only historical replay without writeback.

    The replay uses the approved action and prediction as an immutable reference.
    It may recompute a comparison with a setup-hours calibration version that was
    already known at the supplied test-clock time, but it never persists that
    comparison or calls the live Timecue adapter.
    """

    if (
        request.app.state.settings.data_mode != SourceMode.FIXTURE.value
        or session.user.source_mode.value != SourceMode.FIXTURE.value
    ):
        raise HTTPException(status_code=403, detail="fixture_replay_only")

    decision = _require_decision(request, organization_id, decision_id)
    if (
        decision.get("sourceMode") not in (None, SourceMode.FIXTURE.value)
        or decision.get("synthetic") is False
    ):
        raise HTTPException(status_code=403, detail="fixture_replay_only")

    analysis_id = decision.get("analysisId")
    if not isinstance(analysis_id, str) or not analysis_id:
        raise HTTPException(status_code=422, detail="replay_analysis_missing")
    analysis = request.app.state.store.get_analysis(organization_id, analysis_id)
    if analysis is None or analysis.get("status") != "completed":
        raise HTTPException(status_code=404, detail="completed_analysis_not_found")

    _require_aware(body.as_of, "asOf")
    approved_at = _optional_aware_datetime(decision.get("approvedAt"))
    if approved_at is not None and body.as_of < approved_at:
        raise HTTPException(status_code=422, detail="replay_before_decision")

    try:
        snapshot = request.app.state.fixture.snapshot(session.record, organization_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    planning = _planning_for(request, snapshot)
    replay_mismatches: dict[str, dict[str, Any]] = {}
    if decision.get("sourceRevision") != snapshot.source_revision:
        replay_mismatches["sourceRevision"] = {
            "decision": decision.get("sourceRevision"),
            "current": snapshot.source_revision,
        }
    if (
        decision.get("assumptionsVersion") is not None
        and int(decision.get("assumptionsVersion", -1)) != planning.version
    ):
        replay_mismatches["assumptionsVersion"] = {
            "decision": decision.get("assumptionsVersion"),
            "current": planning.version,
        }
    if replay_mismatches:
        raise HTTPException(
            status_code=409,
            detail={"code": "replay_inputs_changed", "mismatches": replay_mismatches},
        )

    all_observations = request.app.state.store.list_observations(organization_id, decision_id)
    visible_observations = [
        item for item in all_observations if _timeline_visible(item, body.as_of)
    ]
    visible_evidence = [
        item
        for item in (_fixture_evidence_item(worklog) for worklog in snapshot.worklogs)
        if item is not None and _timeline_visible(item, body.as_of)
    ]
    calibration = _calibration_as_of(request, organization_id, body.as_of)
    calibrated_setup_hours = (
        float(calibration["meanHours"]) if int(calibration.get("version", 0)) > 0 else None
    )

    strategy_id = decision.get("strategyId")
    exact_actions = decision.get("exactActions", [])
    if not isinstance(strategy_id, str) or not isinstance(exact_actions, list):
        raise HTTPException(status_code=422, detail="replay_decision_action_missing")
    target_project_id = analysis.get("projectId") or analysis.get("targetProjectId")
    if not isinstance(target_project_id, str) or not target_project_id:
        raise HTTPException(status_code=422, detail="replay_target_project_missing")

    simulation_seeds = decision.get("simulationSeeds")
    seed = body.seed
    sample_count = body.samples
    if isinstance(simulation_seeds, Mapping):
        if seed is None and isinstance(simulation_seeds.get("seed"), int):
            seed = int(simulation_seeds["seed"])
        if sample_count is None and isinstance(simulation_seeds.get("sampleCount"), int):
            sample_count = int(simulation_seeds["sampleCount"])
    if seed is None and isinstance(analysis.get("seed"), int):
        seed = int(analysis["seed"])
    if sample_count is None and isinstance(analysis.get("sampleCount"), int):
        sample_count = int(analysis["sampleCount"])

    replay_request = AnalysisRequest(
        projectId=target_project_id,
        mode=AnalysisMode.RECOVERY,
        seed=seed,
    )
    replay_actions = _replay_actions(exact_actions, calibrated_setup_hours)
    engine_payload = _engine_payload(
        snapshot,
        planning,
        replay_request,
        setup_hours=calibrated_setup_hours,
        as_of=body.as_of,
        samples=sample_count,
        candidate_actions=[{"id": strategy_id, "actions": replay_actions}],
    )
    try:
        engine_result = _frontend_engine_result(
            evaluate_portfolio(engine_payload), target_project_id
        )
    except SimulationEngineUnavailable as exc:
        raise HTTPException(
            status_code=503, detail={"code": "simulation_engine_unavailable", "message": str(exc)}
        ) from exc
    except SimulationInputError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "simulation_input_invalid", "message": str(exc)}
        ) from exc

    replay_candidate = _find_replay_candidate(engine_result, strategy_id)
    if replay_candidate is None:
        raise HTTPException(status_code=422, detail="replay_strategy_unavailable")
    before_model_version = str(
        decision.get("modelVersion") or analysis.get("modelVersion") or "simulation-engine"
    )
    after_model_version = str(engine_result.get("modelVersion") or before_model_version)
    original_prediction = decision.get("selectedOutcomes", {})
    after_outcomes = replay_candidate.get("outcomesByProject", {})
    before_calibration_version = _calibration_version_label(decision.get("calibrationVersion"))
    after_calibration_version = _calibration_version_label(calibration.get("version"))
    return {
        "decisionId": decision_id,
        "organizationId": organization_id,
        "sourceMode": SourceMode.FIXTURE.value,
        "synthetic": True,
        "syntheticLabel": FIXTURE_REPLAY_LABEL,
        "replayMode": "historical_synthetic_fixture",
        "asOf": body.as_of.isoformat(),
        "appliedToTimecue": False,
        "message": "Synthetic historical replay only. No changes were applied to Timecue.",
        "frozenDecision": {
            "analysisId": analysis_id,
            "strategyId": strategy_id,
            "exactActions": exact_actions,
            "guardrails": decision.get("guardrails", []),
            "guardrailChecks": decision.get("guardrailChecks", {}),
        },
        "originalPrediction": original_prediction,
        "before": {
            "modelVersion": before_model_version,
            "calibrationVersion": before_calibration_version,
            "outcomes": original_prediction,
            "immutable": True,
        },
        "after": {
            "modelVersion": after_model_version,
            "calibrationVersion": after_calibration_version,
            "outcomes": after_outcomes,
            "feasible": replay_candidate.get("feasible"),
            "rejectionReasons": replay_candidate.get("rejectionReasons", []),
            "actions": replay_candidate.get("actions", []),
            "guardrailChecks": replay_candidate.get("guardrailChecks", {}),
        },
        "modelVersions": {"before": before_model_version, "after": after_model_version},
        "calibration": calibration,
        "observations": visible_observations,
        "visibleObservations": visible_observations,
        "evidence": visible_evidence,
        "visibleEvidence": visible_evidence,
        "excludedFutureObservationCount": len(all_observations) - len(visible_observations),
        "excludedFutureEvidenceCount": len(snapshot.worklogs) - len(visible_evidence),
    }


@router.post("/organizations/{organization_id}/decisions/{decision_id}/implementation")
def record_implementation_observation(
    organization_id: str,
    decision_id: str,
    body: ImplementationObservationRequest,
    request: Request,
    session: AuthenticatedSession = Depends(
        require_org_permissions(
            "projects.read",
            "tasks.read",
            "workers.read",
            "worklogs.read",
            "tasks.assign",
            write=True,
        )
    ),
) -> dict[str, Any]:
    decision = _require_decision(request, organization_id, decision_id)
    event_at = body.event_at or datetime.now(UTC)
    known_at = body.known_at or datetime.now(UTC)
    _require_aware(event_at, "eventAt")
    _require_aware(known_at, "knownAt")
    observation = request.app.state.store.create_observation(
        organization_id,
        decision_id,
        "implementation",
        session.user.source_mode.value,
        {
            "type": "implementation",
            "state": body.state.value,
            "eventAt": event_at.isoformat(),
            "knownAt": known_at.isoformat(),
            "note": body.note,
            "sourceMode": session.user.source_mode.value,
            "synthetic": session.user.synthetic,
        },
    )
    return {
        "decisionId": decision["id"],
        "implementationObservation": observation,
        "implementationState": body.state.value,
        "message": "Decision recorded. Not applied to Timecue.",
    }


@router.post("/organizations/{organization_id}/decisions/{decision_id}/observations")
def record_setup_duration_observation(
    organization_id: str,
    decision_id: str,
    body: SetupDurationObservationRequest,
    request: Request,
    session: AuthenticatedSession = Depends(
        require_org_permissions(
            "projects.read",
            "tasks.read",
            "workers.read",
            "worklogs.read",
            "tasks.assign",
            write=True,
        )
    ),
) -> dict[str, Any]:
    decision = _require_decision(request, organization_id, decision_id)
    _require_aware(body.event_at, "eventAt")
    _require_aware(body.known_at, "knownAt")
    if not _matches_transfer_action(decision, body):
        raise HTTPException(status_code=422, detail="setup_observation_not_in_approved_action")
    source_mode = session.user.source_mode.value
    eligible = source_mode == SourceMode.FIXTURE.value and body.accepted_for_calibration
    observation = request.app.state.store.create_observation(
        organization_id,
        decision_id,
        body.type.value,
        source_mode,
        {
            "type": body.type.value,
            "eventAt": body.event_at.isoformat(),
            "knownAt": body.known_at.isoformat(),
            "workerId": body.worker_id,
            "fromProjectId": body.from_project_id,
            "toProjectId": body.to_project_id,
            "setupHours": body.setup_hours,
            "acceptedForCalibration": body.accepted_for_calibration,
            "calibrationEligible": eligible,
            "note": body.note,
            "sourceMode": source_mode,
            "synthetic": session.user.synthetic,
        },
    )
    calibration = None
    if eligible:
        calibration = _update_fixture_calibration(
            request,
            organization_id,
            body.setup_hours,
            observation["id"],
            event_at=body.event_at,
            known_at=body.known_at,
        )
    return {
        "observation": observation,
        "calibrationUpdated": calibration is not None,
        "calibration": calibration,
        "message": (
            "Setup observation accepted for fixture calibration."
            if calibration is not None
            else "Setup observation recorded; live calibration is disabled."
        ),
    }


@router.get("/organizations/{organization_id}/calibration/setup-hours")
def get_setup_calibration(
    organization_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(
        require_org_permissions("projects.read", "tasks.read", "workers.read", "worklogs.read")
    ),
) -> dict[str, Any]:
    source_mode = session.user.source_mode.value
    if source_mode != SourceMode.FIXTURE.value:
        return {
            "sourceMode": source_mode,
            "calibrationEligible": False,
            "current": None,
            "history": [],
            "reason": "fixture_calibration_only",
        }
    current = request.app.state.store.get_calibration(organization_id, source_mode)
    return {
        "sourceMode": source_mode,
        "calibrationEligible": True,
        "current": current or _initial_calibration(),
        "history": request.app.state.store.list_calibration_history(organization_id, source_mode),
    }


@router.post("/organizations/{organization_id}/decisions/{decision_id}/checkpoints/check")
def check_decision_checkpoint(
    organization_id: str,
    decision_id: str,
    body: CheckpointCheckRequest,
    request: Request,
    session: AuthenticatedSession = Depends(
        require_org_permissions(
            "projects.read",
            "tasks.read",
            "workers.read",
            "worklogs.read",
            "tasks.assign",
            write=True,
        )
    ),
) -> dict[str, Any]:
    decision = _require_decision(request, organization_id, decision_id)
    definition = next(
        (
            item
            for item in decision.get("checkpoints", [])
            if isinstance(item, Mapping) and item.get("id") == body.checkpoint_id
        ),
        None,
    )
    if definition is None:
        raise HTTPException(status_code=404, detail="checkpoint_not_found")
    as_of = body.as_of or datetime.now(UTC)
    _require_aware(as_of, "asOf")
    due_at = _parse_aware_datetime(definition.get("dueAt"), "dueAt")
    evidence_provided = body.evidence is not None
    evidence = None
    observed_value = None
    if body.evidence is not None:
        evidence, observed_value = _normalize_checkpoint_evidence(
            body.evidence,
            request.app.state.store.list_observations(organization_id, decision_id),
            session.user.id,
            str(definition["metric"]),
            body.observed_value,
        )
    try:
        state = evaluate_checkpoint(
            due_at=due_at,
            as_of=as_of,
            comparator=Comparator(str(definition["comparator"])),
            threshold=float(definition["threshold"]),
            observed_value=observed_value,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    evaluation = request.app.state.store.create_checkpoint_evaluation(
        organization_id,
        decision_id,
        body.checkpoint_id,
        state.value,
        {
            "asOf": as_of.isoformat(),
            "evaluatedAt": datetime.now(UTC).isoformat(),
            "observedValue": observed_value,
            "evidenceProvided": evidence_provided,
            "metric": definition["metric"],
            "comparator": definition["comparator"],
            "threshold": definition["threshold"],
            "evidence": evidence,
        },
    )
    return {"evaluation": evaluation}


def _decision_response(
    request: Request, organization_id: str, decision: dict[str, Any]
) -> dict[str, Any]:
    """Expose append-only observations without mutating the approved contract."""

    return {
        **decision,
        "observations": request.app.state.store.list_observations(
            organization_id, str(decision["id"])
        ),
        "checkpointEvaluations": request.app.state.store.list_checkpoint_evaluations(
            organization_id, str(decision["id"])
        ),
    }


def _require_decision(request: Request, organization_id: str, decision_id: str) -> dict[str, Any]:
    decision = request.app.state.store.get_decision(organization_id, decision_id)
    if decision is None:
        raise HTTPException(status_code=404, detail="decision_not_found")
    return decision


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(status_code=422, detail=f"{field}_must_be_timezone_aware")


def _parse_aware_datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise HTTPException(status_code=422, detail=f"{field}_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{field}_invalid") from exc
    _require_aware(parsed, field)
    return parsed


def _optional_aware_datetime(value: Any) -> datetime | None:
    """Parse a timeline value conservatively; malformed or naive values stay hidden."""

    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _has_evidence_provenance(evidence: Mapping[str, Any], actor_id: str) -> bool:
    """Require traceability without trusting a client-supplied attester identity."""

    supplied_actor = evidence.get("actorId")
    if supplied_actor is not None and (
        not isinstance(supplied_actor, str) or supplied_actor != actor_id
    ):
        return False

    source_backed = all(
        isinstance(evidence.get(key), str) and bool(evidence[key].strip())
        for key in ("sourceType", "sourceId")
    )
    manager_note = evidence.get("managerNote", evidence.get("note"))
    manager_attested = all(
        isinstance(value, str) and bool(value.strip()) for value in (manager_note,)
    )
    return source_backed or manager_attested


def _normalize_checkpoint_evidence(
    evidence: Mapping[str, Any],
    decision_observations: list[dict[str, Any]],
    actor_id: str,
    metric: str,
    observed_value: float | None,
) -> tuple[dict[str, Any], float | None]:
    """Validate checkpoint provenance and separate direct evidence from attestation.

    A `decision_observation` source must belong to this exact decision in this
    organization. For setup-hours checkpoints its recorded value is authoritative;
    a conflicting client value is rejected instead of being treated as evidence.
    Manager notes are explicitly attested by the current session and never marked
    as verified upstream evidence.
    """

    supplied_actor = evidence.get("actorId")
    if supplied_actor is not None and (
        not isinstance(supplied_actor, str) or supplied_actor != actor_id
    ):
        raise HTTPException(status_code=422, detail="checkpoint_evidence_actor_mismatch")
    if not _has_evidence_provenance(evidence, actor_id):
        raise HTTPException(status_code=422, detail="checkpoint_evidence_provenance_required")

    normalized = dict(evidence)
    source_type = evidence.get("sourceType")
    source_id = evidence.get("sourceId")
    if source_type == "decision_observation":
        source = next(
            (item for item in decision_observations if item.get("id") == source_id),
            None,
        )
        if source is None:
            raise HTTPException(status_code=422, detail="checkpoint_source_observation_not_found")
        normalized["evidenceKind"] = VERIFIED_DIRECT_OBSERVATION
        normalized["sourceValidated"] = True
        normalized["upstreamVerified"] = False
        if source.get("sourceMode") is not None:
            normalized["sourceMode"] = source["sourceMode"]
        if metric == "setup_hours":
            recorded_value = source.get("setupHours")
            if isinstance(recorded_value, bool) or not isinstance(recorded_value, (int, float)):
                raise HTTPException(
                    status_code=422, detail="checkpoint_source_setup_hours_unavailable"
                )
            recorded = float(recorded_value)
            if observed_value is not None and not isclose(
                float(observed_value), recorded, rel_tol=1e-9, abs_tol=1e-9
            ):
                raise HTTPException(status_code=422, detail="checkpoint_observed_value_mismatch")
            return normalized, recorded
        return normalized, observed_value

    manager_note = evidence.get("managerNote", evidence.get("note"))
    if isinstance(manager_note, str) and manager_note.strip():
        normalized["managerNote"] = manager_note.strip()
        normalized["actorId"] = actor_id
        normalized["evidenceKind"] = MANAGER_ATTESTED_EVIDENCE
        normalized["attestation"] = MANAGER_ATTESTED_EVIDENCE
        normalized["upstreamVerified"] = False
    else:
        normalized["evidenceKind"] = "source_linked"
        normalized["upstreamVerified"] = False
    return normalized, observed_value


def _matches_transfer_action(
    decision: Mapping[str, Any], body: SetupDurationObservationRequest
) -> bool:
    for action in decision.get("exactActions", []):
        if not isinstance(action, Mapping) or action.get("type") != "transfer":
            continue
        if (
            str(action.get("workerId")) == body.worker_id
            and str(action.get("fromProjectId")) == body.from_project_id
            and str(action.get("toProjectId")) == body.to_project_id
        ):
            return True
    return False


def _initial_calibration() -> dict[str, Any]:
    return {
        "version": 0,
        "sourceMode": SourceMode.FIXTURE.value,
        "meanHours": 1.0,
        "priorStrength": 2.0,
        "previousSampleCount": 0,
        "totalSampleCount": 0,
        "status": "assumed",
        "assumption": "Assumed fixture setup duration; awaiting direct observations.",
    }


def _update_fixture_calibration(
    request: Request,
    organization_id: str,
    setup_hours: float,
    observation_id: str,
    *,
    event_at: datetime | None = None,
    known_at: datetime | None = None,
) -> dict[str, Any]:
    source_mode = SourceMode.FIXTURE.value
    current = request.app.state.store.get_calibration(organization_id, source_mode)
    prior = current or _initial_calibration()
    update = update_setup_hours(
        prior_mean_hours=float(prior["meanHours"]),
        prior_strength=float(prior["priorStrength"]),
        previous_sample_count=int(prior.get("totalSampleCount", 0)),
        accepted_observation_hours=(setup_hours,),
    )
    version = int(prior.get("version", 0)) + 1
    return request.app.state.store.create_calibration(
        organization_id,
        source_mode,
        version,
        {
            "sourceMode": source_mode,
            "meanHours": update.updated_mean_hours,
            "priorMeanHours": update.prior_mean_hours,
            "priorStrength": update.prior_strength,
            "previousSampleCount": update.previous_sample_count,
            "totalSampleCount": update.total_sample_count,
            "acceptedObservationHours": list(update.accepted_observation_hours),
            "observationIds": [observation_id],
            "status": "calibrated",
            "observationEventAt": (event_at or datetime.now(UTC)).isoformat(),
            "observationKnownAt": (known_at or datetime.now(UTC)).isoformat(),
            "updatedAt": (known_at or datetime.now(UTC)).isoformat(),
        },
    )


def _timeline_visible(item: Mapping[str, Any], as_of: datetime) -> bool:
    """Return whether an event is observable at the replay clock.

    Both event time and knowledge time are gates. Missing or malformed time is
    excluded rather than guessed into the historical record.
    """

    known_at = _optional_aware_datetime(item.get("knownAt", item.get("known_at")))
    event_at = _optional_aware_datetime(item.get("eventAt", item.get("event_at")))
    if known_at is None and event_at is None:
        return False
    if known_at is not None and known_at > as_of:
        return False
    if event_at is not None and event_at > as_of:
        return False
    return True


def _fixture_evidence_item(worklog: Mapping[str, Any]) -> dict[str, Any] | None:
    """Project a fixture worklog into a minimal, timestamped replay evidence row."""

    known_at = worklog.get("knownAt", worklog.get("updatedAt"))
    event_at = worklog.get("eventAt")
    if not isinstance(known_at, str) and not isinstance(event_at, str):
        return None
    return {
        "sourceType": "worklog",
        "sourceId": worklog.get("id"),
        "sourceRevision": worklog.get("updatedAt") or worklog.get("knownAt"),
        "projectId": worklog.get("projectId"),
        "taskId": worklog.get("taskId"),
        "workDate": worklog.get("workDate"),
        "eventAt": event_at,
        "knownAt": known_at,
        "description": worklog.get("description"),
        "sourceMode": SourceMode.FIXTURE.value,
        "synthetic": True,
    }


def _calibration_as_of(request: Request, organization_id: str, as_of: datetime) -> dict[str, Any]:
    """Return the latest fixture calibration version known at the replay clock."""

    history = request.app.state.store.list_calibration_history(
        organization_id, SourceMode.FIXTURE.value
    )
    visible = [
        item
        for item in history
        if item.get("sourceMode") in (None, SourceMode.FIXTURE.value)
        and _timeline_visible(
            {
                "eventAt": item.get("observationEventAt", item.get("updatedAt")),
                "knownAt": item.get("observationKnownAt", item.get("updatedAt")),
            },
            as_of,
        )
    ]
    if not visible:
        return _initial_calibration()
    return max(visible, key=lambda item: int(item.get("version", 0)))


def _calibration_version_label(value: Any) -> str:
    if value is None or value == "initial" or value == 0 or value == "0":
        return "initial"
    return str(value)


def _replay_actions(exact_actions: list[Any], calibrated_setup_hours: float | None) -> list[Any]:
    """Copy the frozen actions, changing only the replay calibration input."""

    copied: list[Any] = []
    for raw_action in exact_actions:
        if not isinstance(raw_action, Mapping):
            copied.append(raw_action)
            continue
        action = dict(raw_action)
        if calibrated_setup_hours is not None and action.get("type") == "transfer":
            action["setupHours"] = calibrated_setup_hours
        copied.append(action)
    return copied


def _find_replay_candidate(
    engine_result: Mapping[str, Any], strategy_id: str
) -> dict[str, Any] | None:
    for key in ("strategies", "candidateEvaluations"):
        candidates = engine_result.get(key, [])
        if not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if isinstance(candidate, Mapping) and str(candidate.get("id")) == strategy_id:
                return dict(candidate)
    return None


def _ensure_analysis_fresh(
    analysis: Mapping[str, Any],
    snapshot: PortfolioSnapshot,
    planning: PlanningInputs,
    request: Request,
    organization_id: str,
) -> None:
    """Prevent approval of metrics computed from an older source or overlay."""

    current_calibration = request.app.state.store.get_calibration(
        organization_id, snapshot.source_mode.value
    )
    current_calibration_version = (
        str(current_calibration.get("version")) if current_calibration else "initial"
    )
    mismatches: dict[str, dict[str, Any]] = {}
    if analysis.get("sourceRevision") != snapshot.source_revision:
        mismatches["sourceRevision"] = {
            "analysis": analysis.get("sourceRevision"),
            "current": snapshot.source_revision,
        }
    if int(analysis.get("assumptionsVersion", -1)) != planning.version:
        mismatches["assumptionsVersion"] = {
            "analysis": analysis.get("assumptionsVersion"),
            "current": planning.version,
        }
    if str(analysis.get("calibrationVersion", "initial")) != current_calibration_version:
        mismatches["calibrationVersion"] = {
            "analysis": analysis.get("calibrationVersion"),
            "current": current_calibration_version,
        }
    if mismatches:
        raise HTTPException(
            status_code=409,
            detail={"code": "analysis_stale", "mismatches": mismatches},
        )


def _load_snapshot(
    request: Request, session: AuthenticatedSession, organization_id: str
) -> PortfolioSnapshot:
    try:
        snapshot = request.app.state.adapter.snapshot(session.record, organization_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except UpstreamIntegrationError as exc:
        raise _upstream_http_error(exc) from exc
    except EvidenceIntegrationError as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": str(exc), "source": "evidence_provider"},
        ) from exc
    request.app.state.store.save_snapshot(snapshot.model_dump(mode="json", by_alias=True))
    return snapshot


def _planning_for(request: Request, snapshot: PortfolioSnapshot) -> PlanningInputs:
    from src.app.services.portfolio import planning_for

    return planning_for(request.app.state.store, snapshot)


_PLANNING_TOP_LEVEL_FIELDS = {
    "version",
    "projects",
    "tasks",
    "workers",
    "transfers",
    "reservations",
}
_PROJECT_OVERLAY_FIELDS = {
    "id",
    "name",
    "targetFinishAt",
    "priority",
    "timezone",
    "planningNotes",
    "location",
}
_TASK_OVERLAY_FIELDS = {
    "id",
    "projectId",
    "title",
    "status",
    "predecessorIds",
    "requiredSpecialtyId",
    "remainingPersonHours",
    "minCrew",
    "maxCrew",
    "earliestStartAt",
    "assignedWorkerIds",
    "priority",
    "materialReadyAt",
    "workabilityMode",
    "crew",
    "specialty",
    "planningNotes",
    "weatherRules",
    "workType",
    "workability",
}
_WORKER_OVERLAY_FIELDS = {
    "id",
    "name",
    "specialtyIds",
    "homeProjectId",
    "projectIds",
    "workingWeekdays",
    "shiftStart",
    "shiftEnd",
    "timezone",
    "canTransfer",
    "active",
    "permittedOvertimeSlots",
    "maxOvertimeHours",
    "availability",
    "planningNotes",
}
_TRANSFER_OVERLAY_FIELDS = {
    "fromProjectId",
    "toProjectId",
    "workerId",
    "outboundTravelHours",
    "returnTravelHours",
    "setupHours",
    "startsAt",
    "endsAt",
    "eligibleTargetTaskIds",
    "availableAt",
    "earliestStartAt",
    "latestEndAt",
}
_RESERVATION_OVERLAY_FIELDS = {
    "id",
    "projectId",
    "taskId",
    "workerId",
    "workerProfileId",
    "startAt",
    "endAt",
    "recordType",
    "sourceType",
    "sourceId",
    "assignments",
    "timezone",
    "description",
}


def _validate_planning_payload_shape(payload: Mapping[str, Any]) -> None:
    unknown = set(payload) - _PLANNING_TOP_LEVEL_FIELDS - {"expectedVersion"}
    if unknown:
        raise ValueError(f"planning fields are not allowed: {sorted(unknown)}")


def _validate_planning_ids(
    request: Request, session: AuthenticatedSession, organization_id: str, inputs: PlanningInputs
) -> None:
    snapshot = _load_snapshot(request, session, organization_id)
    projects = _indexed(snapshot.projects)
    tasks = _indexed(snapshot.tasks)
    workers = _indexed(snapshot.workers)
    specialties = _indexed(snapshot.specialties)
    project_ids = set(projects)
    task_ids = set(tasks)
    worker_ids = set(workers)
    specialty_ids = set(specialties)
    specialty_ids.update(
        str(task["requiredSpecialtyId"])
        for task in snapshot.tasks
        if task.get("requiredSpecialtyId")
    )
    specialty_ids.update(
        str(specialty_id)
        for worker in snapshot.workers
        for specialty_id in worker.get("specialtyIds", [])
    )
    for project in inputs.projects:
        _validate_overlay_keys(project, _PROJECT_OVERLAY_FIELDS, "project")
        _require_known(projects, project.get("id"), "project")
    for task in inputs.tasks:
        _validate_task_overlay(task, tasks, project_ids, task_ids, worker_ids, specialty_ids)
    for worker in inputs.workers:
        _validate_overlay_keys(worker, _WORKER_OVERLAY_FIELDS, "worker")
        _require_known(workers, worker.get("id"), "worker")
        _validate_optional_reference(worker, "homeProjectId", project_ids, "project")
        _validate_id_list(worker.get("projectIds"), project_ids, "project")
        _validate_id_list(worker.get("specialtyIds"), specialty_ids, "specialty")
    for transfer in inputs.transfers:
        _validate_overlay_keys(transfer, _TRANSFER_OVERLAY_FIELDS, "transfer")
        _validate_optional_reference(transfer, "fromProjectId", project_ids, "project")
        _validate_optional_reference(transfer, "toProjectId", project_ids, "project")
        _validate_optional_reference(transfer, "workerId", worker_ids, "worker")
    for reservation in inputs.reservations:
        _validate_overlay_keys(reservation, _RESERVATION_OVERLAY_FIELDS, "reservation")
        _validate_optional_reference(reservation, "projectId", project_ids, "project")
        _validate_optional_reference(reservation, "taskId", task_ids, "task")
        worker_key = "workerProfileId" if reservation.get("workerProfileId") else "workerId"
        _validate_optional_reference(reservation, worker_key, worker_ids, "worker")


def _indexed(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item["id"]): item for item in items if item.get("id")}


def _validate_overlay_keys(item: Mapping[str, Any], allowed: set[str], resource: str) -> None:
    unknown = set(item) - allowed
    if unknown:
        raise ValueError(f"planning {resource} fields are not allowed: {sorted(unknown)}")


def _require_known(source: Mapping[str, Mapping[str, Any]], value: Any, resource: str) -> str:
    identifier = str(value) if value is not None else ""
    if not identifier or identifier not in source:
        raise ValueError(f"planning {resource} is outside the authorized snapshot")
    return identifier


def _validate_optional_reference(
    item: Mapping[str, Any], key: str, valid_ids: set[str], resource: str
) -> None:
    if item.get(key) is not None:
        identifier = str(item[key])
        if identifier not in valid_ids:
            raise ValueError(f"planning {key} references an unauthorized {resource}")


def _validate_id_list(value: Any, valid_ids: set[str], resource: str) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        raise ValueError(f"planning {resource} references must be a list")
    for identifier in value:
        if str(identifier) not in valid_ids:
            raise ValueError(f"planning reference is outside the authorized {resource} set")


def _validate_task_overlay(
    task: Mapping[str, Any],
    source_tasks: Mapping[str, Mapping[str, Any]],
    project_ids: set[str],
    task_ids: set[str],
    worker_ids: set[str],
    specialty_ids: set[str],
    *,
    partial: bool = False,
) -> None:
    _validate_overlay_keys(task, _TASK_OVERLAY_FIELDS, "task")
    task_id = _require_known(source_tasks, task.get("id"), "task")
    source = source_tasks[task_id]
    if not partial:
        for immutable in ("projectId", "status"):
            if immutable in task and str(task[immutable]) != str(source.get(immutable)):
                raise ValueError(f"planning task {immutable} cannot change upstream identity")
    _validate_optional_reference(task, "projectId", project_ids, "project")
    _validate_id_list(task.get("predecessorIds"), task_ids, "task")
    _validate_id_list(task.get("assignedWorkerIds"), worker_ids, "worker")
    _validate_optional_reference(task, "requiredSpecialtyId", specialty_ids, "specialty")
    _validate_id_list(task.get("crew"), worker_ids, "worker")
    remaining = task.get("remainingPersonHours")
    if remaining is not None:
        if not isinstance(remaining, Mapping) or set(remaining) - {
            "optimistic",
            "mostLikely",
            "pessimistic",
        }:
            raise ValueError("planning remainingPersonHours has an unsupported shape")
        if any(not isinstance(value, (int, float)) or value < 0 for value in remaining.values()):
            raise ValueError("planning remainingPersonHours must contain non-negative numbers")


def _apply_planning_change(
    request: Request, session: AuthenticatedSession, organization_id: str, change: dict[str, Any]
) -> None:
    planning = _planning_for(request, _load_snapshot(request, session, organization_id))
    task_id = change.get("taskId")
    if not isinstance(task_id, str):
        raise HTTPException(status_code=422, detail="planningChange.taskId_required")
    snapshot = _load_snapshot(request, session, organization_id)
    source_tasks = _indexed(snapshot.tasks)
    try:
        _validate_task_overlay(
            {"id": task_id, **{key: value for key, value in change.items() if key != "taskId"}},
            source_tasks,
            set(_indexed(snapshot.projects)),
            set(source_tasks),
            set(_indexed(snapshot.workers)),
            {
                *set(_indexed(snapshot.specialties)),
                *{
                    str(task["requiredSpecialtyId"])
                    for task in snapshot.tasks
                    if task.get("requiredSpecialtyId")
                },
            },
            partial=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    for task in planning.tasks:
        if str(task.get("id")) == task_id:
            if {key for key in change if key != "taskId"} - (
                _TASK_OVERLAY_FIELDS - {"id", "projectId", "status"}
            ):
                raise HTTPException(status_code=422, detail="planningChange_field_not_allowed")
            for key, value in change.items():
                if key != "taskId":
                    task[key] = value
            request.app.state.store.save_planning(
                organization_id, planning.model_dump(mode="json", by_alias=True), planning.version
            )
            return
    raise HTTPException(status_code=422, detail="planningChange.task_not_in_snapshot")


def _ingest_signals(
    request: Request, snapshot: PortfolioSnapshot, task_ids: set[str], project_id: str | None
) -> None:
    for worklog in snapshot.worklogs:
        worklog_id = str(worklog.get("id", ""))
        if not worklog_id or (
            project_id is not None and str(worklog.get("projectId")) != project_id
        ):
            continue
        linked_task = str(worklog.get("taskId")) if worklog.get("taskId") else None
        source_revision = str(worklog.get("updatedAt") or worklog.get("knownAt") or "unknown")
        source = {
            "sourceType": "worklog",
            "sourceId": worklog_id,
            "sourceRevision": source_revision,
            "knownAt": worklog.get("updatedAt") or worklog.get("knownAt"),
            "projectId": worklog.get("projectId"),
            "taskId": linked_task,
            "workDate": worklog.get("workDate"),
            "timezone": worklog.get("projectTimezone", "UTC"),
        }
        observations = extract_evidence(
            str(worklog.get("description", "")),
            {linked_task} if linked_task in task_ids else task_ids,
            source,
            settings=request.app.state.settings,
        )
        for index, observation in enumerate(observations):
            signal_id = hashlib.sha256(
                f"{worklog_id}:{source_revision}:{index}".encode()
            ).hexdigest()[:32]
            payload = {
                "id": signal_id,
                "organizationId": snapshot.organization_id,
                "projectId": worklog.get("projectId"),
                "taskId": observation.get("linkedTaskId", linked_task),
                "status": observation.get("status", "proposed"),
                "observation": observation,
                "sourceText": worklog.get("description"),
                "sourceRevision": source_revision,
                "sourceId": worklog_id,
            }
            request.app.state.store.save_signal(
                snapshot.organization_id,
                signal_id,
                str(worklog.get("projectId")) if worklog.get("projectId") else None,
                str(payload["status"]),
                payload,
            )


def _user_response(session: AuthenticatedSession) -> dict[str, Any]:
    return session.user.model_dump(mode="json", by_alias=True)


def _set_local_cookies(response: Response, request: Request, session: AuthenticatedSession) -> None:
    settings = request.app.state.settings
    response.set_cookie(
        SESSION_COOKIE,
        session.record.session_id,
        max_age=60 * 60 * 8,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        domain=settings.cookie_domain,
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        session.record.csrf_token,
        max_age=60 * 60 * 8,
        httponly=False,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        domain=settings.cookie_domain,
        path="/",
    )


def _clear_local_cookies(response: Response, request: Request) -> None:
    settings = request.app.state.settings
    response.delete_cookie(SESSION_COOKIE, domain=settings.cookie_domain, path="/")
    response.delete_cookie(CSRF_COOKIE, domain=settings.cookie_domain, path="/")


def _upstream_http_error(exc: UpstreamIntegrationError) -> HTTPException:
    code = 502 if exc.status_code is None or exc.status_code >= 500 else exc.status_code
    return HTTPException(status_code=code, detail={"code": str(exc), "source": "timecue"})


__all__ = ["router"]
