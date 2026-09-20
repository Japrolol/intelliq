"""Deterministic portfolio scheduling and probabilistic comparison engine.

The engine is deliberately self-contained.  The adapter that calls it is
responsible for authentication, tenant scoping, provenance, and readiness
gates; this module only accepts an already-authorized planning payload and
returns reproducible model output.

The simulation uses UTC-aware one-hour slots while interpreting each worker's
calendar in its declared local timezone.  Remaining effort is sampled once per
task and sample, then the same draws are used for the baseline and every
candidate.  Unfinished trajectories stay censored: the engine never replaces a
missing finish with the horizon end.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from hashlib import sha256
from functools import lru_cache
from itertools import combinations, zip_longest
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

HOUR = timedelta(hours=1)
EPSILON = 1e-9
MAX_SAMPLES = 5_000
# Keep the legacy non-baseline candidate response cap for compatibility.  The
# v4 total budget is still enforced by the baseline-inclusive limit below.
MAX_CANDIDATES = 60
MAX_TOTAL_CANDIDATE_BUNDLES = 64
MAX_ELEMENTARY_ACTIONS = 24
MAX_ACTIONS_PER_BUNDLE = 3
MAX_TRANSFER_WINDOWS_PER_DONOR = 3
MAX_REPRESENTATIVE_TRACES = 3

type JsonObject = Mapping[str, object]


class SimulationInputError(ValueError):
    """Raised when the engine cannot safely interpret a simulation payload."""


@dataclass(frozen=True)
class _Project:
    id: str
    name: str
    target_finish_at: datetime
    priority: float
    timezone: ZoneInfo
    workability: object = None


@dataclass(frozen=True)
class _Task:
    id: str
    project_id: str
    status: str
    predecessor_ids: tuple[str, ...]
    required_specialty_id: str | None
    effort_optimistic: float
    effort_most_likely: float
    effort_pessimistic: float
    min_crew: int
    max_crew: int
    earliest_start_at: datetime
    planned_start_at: datetime | None
    planned_end_at: datetime | None
    material_ready_at: datetime | None
    assigned_worker_ids: frozenset[str]
    priority: float
    workability: object = None
    workability_mode: str | None = None
    completed_at: datetime | None = None


@dataclass(frozen=True)
class _Interval:
    start: datetime
    end: datetime

    def duration_hours(self) -> float:
        return max(0.0, (self.end - self.start).total_seconds() / 3600.0)


@dataclass(frozen=True)
class _Worker:
    id: str
    specialties: frozenset[str]
    home_project_id: str | None
    project_ids: frozenset[str]
    working_weekdays: frozenset[int]
    shift_start: time
    shift_end: time
    timezone: ZoneInfo
    can_transfer: bool
    permitted_overtime_slots: tuple[_Interval, ...]
    max_overtime_hours: float | None
    active: bool


@dataclass(frozen=True)
class _Reservation:
    worker_id: str | None
    project_id: str | None
    task_id: str | None
    interval: _Interval


@dataclass(frozen=True)
class _TransferSpec:
    worker_id: str
    from_project_id: str
    to_project_id: str
    starts_at: datetime
    ends_at: datetime
    eligible_target_task_ids: tuple[str, ...]
    outbound_travel_hours: float | None
    return_travel_hours: float | None
    setup_hours: float | None


@dataclass(frozen=True)
class _OvertimeSpec:
    worker_id: str
    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True)
class _DateShiftSpec:
    task_id: str
    starts_at: datetime
    ends_at: datetime
    action_type: str = "date_shift"


@dataclass(frozen=True)
class _MaterialSpec:
    task_id: str
    available_at: datetime | None
    material_id: str | None
    confirmed: bool
    action_type: str = "material"


@dataclass(frozen=True)
class _WeatherSpec:
    task_id: str | None
    project_id: str | None
    starts_at: datetime
    ends_at: datetime
    capacity: float | None
    rule_id: str | None
    confirmed: bool
    action_type: str = "weather"


@dataclass(frozen=True)
class _CandidateAction:
    id: str
    transfers: tuple[_TransferSpec, ...] = ()
    overtime: tuple[_OvertimeSpec, ...] = ()
    date_shifts: tuple[_DateShiftSpec, ...] = ()
    materials: tuple[_MaterialSpec, ...] = ()
    weather: tuple[_WeatherSpec, ...] = ()
    project_order: tuple[str, ...] = ()
    task_order: tuple[str, ...] = ()
    priority_action_type: str = "priority"
    source: str = "generated"
    validation_errors: tuple[str, ...] = ()
    implemented_signatures: frozenset[tuple[str, object]] = frozenset()


@dataclass(frozen=True)
class _PreparedTransfer:
    spec: _TransferSpec
    productive_start: datetime
    productive_end: datetime
    non_productive_hours: float


@dataclass(frozen=True)
class _PreparedAction:
    action: _CandidateAction
    transfers: tuple[_PreparedTransfer, ...]
    overtime: tuple[_OvertimeSpec, ...]
    date_shifts: tuple[_DateShiftSpec, ...] = ()
    materials: tuple[_MaterialSpec, ...] = ()
    weather: tuple[_WeatherSpec, ...] = ()


@dataclass(frozen=True)
class _Input:
    as_of: datetime
    horizon_end: datetime
    seed: int
    samples: int
    target_project_id: str
    projects: tuple[_Project, ...]
    tasks: tuple[_Task, ...]
    workers: tuple[_Worker, ...]
    reservations: tuple[_Reservation, ...]
    transfer_assumptions: tuple[_TransferSpec, ...]
    guardrails: JsonObject
    input_warnings: tuple[str, ...]
    decision_scope: str = "project"


@dataclass
class _ScheduleResult:
    project_finishes: dict[str, datetime | None]
    task_finishes: dict[str, datetime | None]
    unfinished_tasks: frozenset[str]
    diagnostics: set[tuple[str, str | None, str | None, str | None]]
    changed_assignments: set[tuple[str, str, str]]
    transfer_productive_hours: float
    overtime_hours: float
    transfer_used: set[str]
    samples: tuple[_ScheduleResult, ...] = ()
    capacity_by_project_day: dict[str, dict[str, dict[str, float]]] = field(default_factory=dict)
    available_capacity_by_project_day: dict[str, dict[str, dict[str, float]]] = field(
        default_factory=dict
    )


def evaluate_portfolio(payload: dict, progress: Callable[[dict], None] | None = None) -> dict:
    """Evaluate a planning snapshot and its bounded recovery candidates.

    ``payload`` must contain the planning snapshot fields described in
    ``docs/implementation-plan-v4.md``.  The function returns only metrics
    calculated from simulated trajectories; it does not estimate cost, causal
    attribution, or provider confidence.  Structural input errors raise
    :class:`SimulationInputError` so an adapter cannot accidentally turn an
    invalid graph or incomplete planning input into a forecast.  Equivalent
    action parameter sets are evaluated once even when they arrive under
    different candidate IDs.
    """

    data = _parse_input(payload)
    draws = _sample_effort(data)
    implemented_action = _parse_implemented_action(payload, data)
    baseline_action = replace(
        implemented_action,
        id="baseline",
        source="baseline",
        implemented_signatures=frozenset(_action_item_signatures(implemented_action)),
    )

    def emit(
        action: _CandidateAction, completed_samples: int, status: str = "running", **extra: object
    ) -> None:
        if progress is not None:
            progress(
                {
                    "id": action.id,
                    "status": status,
                    "completedSamples": completed_samples,
                    "totalSamples": data.samples,
                    "actions": _action_outputs(data, action),
                    **extra,
                }
            )

    emit(baseline_action, 0)
    baseline_schedule = _run_scenario(
        data, draws, baseline_action, lambda count: emit(baseline_action, count)
    )
    baseline_outcomes = _summarize_outcomes(data, baseline_schedule)
    emit(baseline_action, data.samples, "completed", outcomesByProject=baseline_outcomes)

    diagnostics = _collect_diagnostics(data, baseline_schedule, scenario_id="baseline")
    explicit_candidates = _extract_explicit_candidates(payload, data)
    if explicit_candidates is None:
        candidates = _generate_candidates(data, implemented_action)
    else:
        candidates = explicit_candidates
    proposed = payload.get("proposedCandidates")
    if isinstance(proposed, list) and proposed:
        extra = _extract_explicit_candidates({"candidateActions": proposed[:3]}, data) or []
        candidates = [*extra, *candidates]

    candidate_evaluations: list[dict[str, object]] = []
    feasible_candidates: list[dict[str, object]] = []
    warnings = list(data.input_warnings)

    unique_candidates = _unique_evaluation_candidates(candidates)
    evaluated_candidates = unique_candidates[:MAX_CANDIDATES]
    for candidate_index, action in enumerate(evaluated_candidates):
        emit(
            action,
            0,
            totalCandidates=len(evaluated_candidates),
            completedCandidates=candidate_index,
        )
        evaluation, schedule = _evaluate_candidate(
            data,
            draws,
            action,
            baseline_action,
            baseline_outcomes,
            lambda count: emit(
                action,
                count,
                totalCandidates=len(evaluated_candidates),
                completedCandidates=candidate_index,
            ),
        )
        emit(
            action,
            data.samples if schedule is not None else 0,
            "completed" if evaluation.get("feasible") else "rejected",
            feasible=evaluation.get("feasible"),
            outcomesByProject=evaluation.get("outcomesByProject", {}),
            totalCandidates=len(evaluated_candidates),
            completedCandidates=candidate_index + 1,
        )
        candidate_evaluations.append(evaluation)
        if evaluation.get("feasible") is True and schedule is not None:
            feasible_candidates.append(evaluation)
            diagnostics.extend(_collect_diagnostics(data, schedule, scenario_id=action.id))

    selected = _select_strategies(data, baseline_outcomes, feasible_candidates)
    if not selected:
        warnings.append("no_feasible_beneficial_recovery_candidate")

    baseline_strategy = _strategy_result(
        data=data,
        action=baseline_action,
        outcomes=baseline_outcomes,
        baseline_outcomes=baseline_outcomes,
        feasible=True,
        rejection_reasons=(),
        schedule=baseline_schedule,
        labels=("unchangedBaseline",),
    )
    baseline_strategy["strategy"] = "baseline"
    baseline_strategy["name"] = "Current plan"
    baseline_strategy["implemented"] = bool(_action_count(baseline_action))

    selected_results: list[dict[str, object]] = [baseline_strategy]
    selected_ids = {str(candidate["id"]) for candidate in selected}
    for candidate in feasible_candidates:
        if str(candidate["id"]) in selected_ids:
            selected_results.append(candidate)
    if data.decision_scope == "portfolio":
        selected_results = [
            baseline_strategy,
            *sorted(selected, key=lambda item: int(item.get("rank", 999))),
        ]

    # Dedupe diagnostics without relying on set iteration order.  A diagnostic
    # is an engine observation, not a probability or an explanation percentage.
    diagnostics = _dedupe_diagnostics(diagnostics)
    total_evaluated = len(candidate_evaluations) + 1
    warnings = list(warnings)
    if len(unique_candidates) > len(evaluated_candidates):
        warnings.append("candidate_bundle_search_truncated")
    return {
        "baselineByProject": baseline_outcomes,
        "strategies": selected_results,
        "candidateEvaluations": candidate_evaluations,
        "decisionScope": data.decision_scope,
        "rankingPolicy": "portfolio_compromise_v1"
        if data.decision_scope == "portfolio"
        else "target_recovery_v4",
        "candidatesEvaluated": len(candidate_evaluations),
        "totalBundlesEvaluated": total_evaluated,
        "candidateLimits": {
            "maxElementaryActions": MAX_ELEMENTARY_ACTIONS,
            "maxActionsPerBundle": MAX_ACTIONS_PER_BUNDLE,
            "maxTotalBundles": MAX_TOTAL_CANDIDATE_BUNDLES,
            "baselineIncluded": True,
        },
        "implementedActions": _action_outputs(data, baseline_action),
        "diagnostics": diagnostics,
        "seed": data.seed,
        "sampleCount": data.samples,
        "horizonEnd": _format_datetime(data.horizon_end),
        "targetProjectId": data.target_project_id,
        "warnings": _dedupe_strings(warnings),
        "sampling": {
            "distribution": "triangular",
            "pairedAcrossStrategies": True,
            "unit": "remaining_reference_person_hours",
            "quantization": "one_hour_slots",
        },
    }


def _parse_input(payload: dict) -> _Input:
    root = _object(payload, "payload")
    as_of = _datetime(_required(root, "asOf", "as_of"), "asOf")
    horizon_days = _number(_required(root, "horizonDays", "horizon_days"), "horizonDays")
    if not horizon_days.is_integer() or horizon_days <= 0 or horizon_days > 365:
        raise SimulationInputError("horizonDays must be an integer between 1 and 365")
    seed_value = _number(root.get("seed", 0), "seed")
    if not seed_value.is_integer():
        raise SimulationInputError("seed must be an integer")
    samples_value = _number(root.get("samples", 100), "samples")
    if not samples_value.is_integer() or samples_value < 1 or samples_value > MAX_SAMPLES:
        raise SimulationInputError(f"samples must be an integer between 1 and {MAX_SAMPLES}")

    project_objects = _objects(root.get("projects"), "projects")
    if not project_objects:
        raise SimulationInputError("projects must contain at least one project")
    target_project_id = _string(
        _required(root, "targetProjectId", "target_project_id"), "targetProjectId"
    )
    project_ids = {
        _string(_required(project, "id"), "projects[].id") for project in project_objects
    }
    if target_project_id not in project_ids:
        raise SimulationInputError(f"targetProjectId {target_project_id!r} is not in projects")

    projects = tuple(
        _parse_project(project, as_of, index) for index, project in enumerate(project_objects)
    )
    project_by_id = {project.id: project for project in projects}

    task_objects = _objects(root.get("tasks"), "tasks")
    assignment_map = _assignment_map(
        root.get("assignments", root.get("effectiveAssignments", [])),
        "assignments",
    )
    normalized_task_objects: list[JsonObject] = []
    for task in task_objects:
        task_id = task.get("id")
        assignment_workers = assignment_map.get(task_id) if isinstance(task_id, str) else None
        if (
            assignment_workers is not None
            and "assignedWorkerIds" not in task
            and "assigned_worker_ids" not in task
            and "workerIds" not in task
            and "worker_ids" not in task
        ):
            normalized = dict(task)
            normalized["assignedWorkerIds"] = assignment_workers
            normalized_task_objects.append(normalized)
        else:
            normalized_task_objects.append(task)
    tasks = tuple(
        _parse_task(task, as_of, project_by_id, index)
        for index, task in enumerate(normalized_task_objects)
    )
    task_ids = [task.id for task in tasks]
    if len(set(task_ids)) != len(task_ids):
        raise SimulationInputError("tasks contain duplicate IDs")
    task_by_id = {task.id: task for task in tasks}
    _validate_task_graph(tasks, task_by_id)

    worker_objects = _objects(root.get("workers"), "workers")
    workers = tuple(
        _parse_worker(worker, project_by_id, as_of, horizon_days, index)
        for index, worker in enumerate(worker_objects)
    )
    worker_ids = [worker.id for worker in workers]
    if len(set(worker_ids)) != len(worker_ids):
        raise SimulationInputError("workers contain duplicate IDs")
    task_worker_ids = {worker_id for task in tasks for worker_id in task.assigned_worker_ids}
    unknown_task_workers = task_worker_ids.difference(worker_ids)
    if unknown_task_workers:
        raise SimulationInputError(
            f"tasks reference unknown assigned workers {sorted(unknown_task_workers)!r}"
        )

    reservations = tuple(
        _parse_reservation(reservation, project_ids, task_ids, worker_ids, index)
        for index, reservation in enumerate(
            _objects(
                root.get("reservations", root.get("externalReservations", [])),
                "reservations",
            )
        )
    )
    transfer_assumptions = tuple(
        _parse_transfer_assumption(
            transfer,
            project_ids,
            worker_ids,
            as_of,
            as_of + timedelta(days=horizon_days),
            index,
        )
        for index, transfer in enumerate(
            _objects(root.get("transferAssumptions", []), "transferAssumptions")
        )
    )

    input_warnings: list[str] = []
    if not root.get("workability") and not any(
        task.workability is not None or project.workability is not None
        for task in tasks
        for project in (project_by_id[task.project_id],)
    ):
        input_warnings.append("workability_not_supplied_ordinary_capacity_assumed")
    if any(task.completed_at is None and task.status not in {"done", "canceled"} for task in tasks):
        # This is intentionally not a readiness error: the parser requires
        # confirmed remaining effort below, and the scheduler handles future work.
        pass

    horizon_end = as_of + timedelta(days=horizon_days)
    guardrails_value = root.get("guardrails", {})
    guardrails = _object(guardrails_value, "guardrails") if guardrails_value else {}
    decision_scope = root.get("decisionScope", "project")
    if decision_scope not in {"project", "portfolio"}:
        raise SimulationInputError("decisionScope must be project or portfolio")
    return _Input(
        as_of=as_of,
        horizon_end=horizon_end,
        seed=int(seed_value),
        samples=int(samples_value),
        target_project_id=target_project_id,
        projects=projects,
        tasks=tasks,
        workers=workers,
        reservations=reservations,
        transfer_assumptions=transfer_assumptions,
        guardrails=guardrails,
        input_warnings=tuple(input_warnings),
        decision_scope=decision_scope,
    )


def _parse_project(project: JsonObject, as_of: datetime, index: int) -> _Project:
    project_id = _string(_required(project, "id"), f"projects[{index}].id")
    target = _datetime(
        _required(project, "targetFinishAt", "target_finish_at"),
        f"projects[{index}].targetFinishAt",
    )
    timezone_name = project.get("timezone", "UTC")
    project_timezone = _zone(timezone_name, f"projects[{index}].timezone")
    name = _string(project.get("name", project_id), f"projects[{index}].name")
    priority_value = _number(project.get("priority", 0), f"projects[{index}].priority")
    if priority_value < 0:
        raise SimulationInputError(f"projects[{index}].priority cannot be negative")
    return _Project(
        id=project_id,
        name=name,
        target_finish_at=target,
        priority=priority_value,
        timezone=project_timezone,
        workability=project.get("workability", project.get("weatherProfile")),
    )


def _assignment_map(value: object, label: str) -> dict[str, list[str]]:
    """Normalize optional effective-assignment rows before task parsing."""

    rows = _objects(value, label)
    result: dict[str, list[str]] = {}
    for index, row in enumerate(rows):
        task_value = row.get("taskId", row.get("task_id"))
        if task_value in (None, ""):
            raise SimulationInputError(f"{label}[{index}].taskId is required")
        task_id = _string(task_value, f"{label}[{index}].taskId")
        workers_value = row.get(
            "workerIds",
            row.get("worker_ids", row.get("workers", [])),
        )
        if workers_value in (None, ""):
            workers_value = []
        result[task_id] = list(_string_tuple(workers_value, f"{label}[{index}].workerIds"))
    return result


def _parse_task(
    task: JsonObject,
    as_of: datetime,
    projects: Mapping[str, _Project],
    index: int,
) -> _Task:
    task_id = _string(_required(task, "id"), f"tasks[{index}].id")
    project_id = _string(_required(task, "projectId", "project_id"), f"tasks[{index}].projectId")
    if project_id not in projects:
        raise SimulationInputError(f"tasks[{index}] references unknown project {project_id!r}")
    status = _string(task.get("status", "planned"), f"tasks[{index}].status").lower()
    predecessor_ids = _string_tuple(
        task.get("predecessorIds", task.get("predecessor_ids", [])),
        f"tasks[{index}].predecessorIds",
    )
    specialty_value = task.get("requiredSpecialtyId", task.get("required_specialty_id"))
    specialty = (
        None
        if specialty_value in (None, "")
        else _string(specialty_value, f"tasks[{index}].requiredSpecialtyId")
    )

    effort_value = task.get("remainingPersonHours", task.get("remaining_person_hours"))
    active = status not in {"done", "canceled"}
    effort = _parse_effort(effort_value, f"tasks[{index}].remainingPersonHours", required=active)
    if effort is None:
        effort = (0.0, 0.0, 0.0)
    min_crew = _bounded_int(task.get("minCrew", task.get("min_crew", 1)), "minCrew", 1, 100)
    max_crew = _bounded_int(
        task.get("maxCrew", task.get("max_crew", min_crew)), "maxCrew", min_crew, 100
    )
    earliest = _datetime(
        task.get("earliestStartAt", task.get("earliest_start_at", as_of)),
        f"tasks[{index}].earliestStartAt",
    )
    planned_start_value = task.get(
        "plannedStartAt",
        task.get("planned_start_at", task.get("startsAt", task.get("starts_at"))),
    )
    planned_end_value = task.get(
        "plannedEndAt",
        task.get("planned_end_at", task.get("endsAt", task.get("ends_at"))),
    )
    planned_start = (
        None
        if planned_start_value in (None, "")
        else _datetime(planned_start_value, f"tasks[{index}].plannedStartAt")
    )
    planned_end = (
        None
        if planned_end_value in (None, "")
        else _datetime(planned_end_value, f"tasks[{index}].plannedEndAt")
    )
    if (planned_start is None) != (planned_end is None):
        raise SimulationInputError(
            f"tasks[{index}] plannedStartAt and plannedEndAt must be supplied together"
        )
    if planned_start is not None and planned_end is not None and planned_end <= planned_start:
        raise SimulationInputError(f"tasks[{index}] planned date window is invalid")
    material_value = task.get("materialReadyAt", task.get("material_ready_at"))
    material_ready = (
        None
        if material_value in (None, "")
        else _datetime(material_value, f"tasks[{index}].materialReadyAt")
    )
    assigned_value = task.get(
        "assignedWorkerIds",
        task.get("assigned_worker_ids", task.get("workerIds", task.get("worker_ids", []))),
    )
    assigned_workers = frozenset(_string_tuple(assigned_value, f"tasks[{index}].assignedWorkerIds"))
    priority = _number(task.get("priority", 0), f"tasks[{index}].priority")
    if priority < 0:
        raise SimulationInputError(f"tasks[{index}].priority cannot be negative")
    completed_value = task.get("completedAt", task.get("completed_at"))
    completed_at = (
        None
        if completed_value in (None, "")
        else _datetime(completed_value, f"tasks[{index}].completedAt")
    )
    workability = task.get(
        "workability",
        task.get("workabilityProfile", task.get("weatherProfile")),
    )
    return _Task(
        id=task_id,
        project_id=project_id,
        status=status,
        predecessor_ids=predecessor_ids,
        required_specialty_id=specialty,
        effort_optimistic=effort[0],
        effort_most_likely=effort[1],
        effort_pessimistic=effort[2],
        min_crew=min_crew,
        max_crew=max_crew,
        earliest_start_at=earliest,
        planned_start_at=planned_start,
        planned_end_at=planned_end,
        material_ready_at=material_ready,
        assigned_worker_ids=assigned_workers,
        priority=priority,
        workability=workability,
        workability_mode=_workability_mode(task, workability),
        completed_at=completed_at,
    )


def _parse_worker(
    worker: JsonObject,
    projects: Mapping[str, _Project],
    as_of: datetime,
    horizon_days: int,
    index: int,
) -> _Worker:
    worker_id = _string(_required(worker, "id"), f"workers[{index}].id")
    specialty_value = worker.get("specialtyIds", worker.get("specialty_ids", []))
    specialties = frozenset(_string_tuple(specialty_value, f"workers[{index}].specialtyIds"))
    home_value = worker.get("homeProjectId", worker.get("home_project_id"))
    home_project_id = (
        None if home_value in (None, "") else _string(home_value, f"workers[{index}].homeProjectId")
    )
    if home_project_id is not None and home_project_id not in projects:
        raise SimulationInputError(
            f"workers[{index}] references unknown home project {home_project_id!r}"
        )
    project_value = worker.get(
        "projectIds", worker.get("project_ids", worker.get("assignedProjectIds", []))
    )
    project_ids = frozenset(_string_tuple(project_value, f"workers[{index}].projectIds"))
    unknown_projects = project_ids.difference(projects)
    if unknown_projects:
        raise SimulationInputError(
            f"workers[{index}] references unknown projects {sorted(unknown_projects)!r}"
        )
    weekdays_value = worker.get("workingWeekdays", worker.get("working_weekdays", [0, 1, 2, 3, 4]))
    weekdays = frozenset(
        _weekday(value, f"workers[{index}].workingWeekdays")
        for value in _list(weekdays_value, f"workers[{index}].workingWeekdays")
    )
    shift_start = _clock_time(
        worker.get("shiftStart", worker.get("shift_start", "08:00")),
        f"workers[{index}].shiftStart",
    )
    shift_end = _clock_time(
        worker.get("shiftEnd", worker.get("shift_end", "16:00")),
        f"workers[{index}].shiftEnd",
    )
    if shift_start == shift_end:
        raise SimulationInputError(f"workers[{index}] shift must have positive duration")
    timezone_value = worker.get("timezone", worker.get("timeZone"))
    if timezone_value in (None, ""):
        # Worker calendars are local to the worker's home site when the
        # upstream snapshot does not carry a separate worker timezone.  This
        # keeps the section-16 fixture's 08:00--16:00 Warsaw shift intact.
        default_project_id = home_project_id
        if default_project_id is None:
            candidate_project_ids = sorted(project_ids or projects.keys())
            default_project_id = candidate_project_ids[0] if candidate_project_ids else None
        timezone_value = (
            projects[default_project_id].timezone if default_project_id is not None else "UTC"
        )
    worker_timezone = _zone(timezone_value, f"workers[{index}].timezone")
    overtime_slots = _parse_overtime_slots(
        worker.get("permittedOvertimeSlots", worker.get("permitted_overtime_slots", [])),
        as_of,
        as_of + timedelta(days=horizon_days),
        worker_timezone,
        f"workers[{index}].permittedOvertimeSlots",
    )
    max_overtime_value = worker.get("maxOvertimeHours", worker.get("max_overtime_hours"))
    max_overtime = (
        None
        if max_overtime_value in (None, "")
        else _number(max_overtime_value, f"workers[{index}].maxOvertimeHours")
    )
    if max_overtime is not None and max_overtime < 0:
        raise SimulationInputError(f"workers[{index}].maxOvertimeHours cannot be negative")
    return _Worker(
        id=worker_id,
        specialties=specialties,
        home_project_id=home_project_id,
        project_ids=project_ids,
        working_weekdays=weekdays,
        shift_start=shift_start,
        shift_end=shift_end,
        timezone=worker_timezone,
        can_transfer=bool(worker.get("canTransfer", worker.get("can_transfer", False))),
        permitted_overtime_slots=overtime_slots,
        max_overtime_hours=max_overtime,
        active=bool(worker.get("active", True)),
    )


def _parse_reservation(
    reservation: JsonObject,
    project_ids: set[str],
    task_ids: Sequence[str],
    worker_ids: Sequence[str],
    index: int,
) -> _Reservation:
    worker_value = reservation.get("workerId", reservation.get("worker_id"))
    project_value = reservation.get("projectId", reservation.get("project_id"))
    task_value = reservation.get("taskId", reservation.get("task_id"))
    worker_id = (
        None
        if worker_value in (None, "")
        else _string(worker_value, f"reservations[{index}].workerId")
    )
    project_id = (
        None
        if project_value in (None, "")
        else _string(project_value, f"reservations[{index}].projectId")
    )
    task_id = (
        None if task_value in (None, "") else _string(task_value, f"reservations[{index}].taskId")
    )
    if worker_id is None and project_id is None and task_id is None:
        raise SimulationInputError(
            f"reservations[{index}] must identify a worker, project, or task"
        )
    if worker_id is not None and worker_id not in worker_ids:
        raise SimulationInputError(f"reservations[{index}] references unknown worker {worker_id!r}")
    if project_id is not None and project_id not in project_ids:
        raise SimulationInputError(
            f"reservations[{index}] references unknown project {project_id!r}"
        )
    if task_id is not None and task_id not in task_ids:
        raise SimulationInputError(f"reservations[{index}] references unknown task {task_id!r}")
    interval = _interval_from_object(reservation, f"reservations[{index}]")
    return _Reservation(worker_id, project_id, task_id, interval)


def _parse_transfer_assumption(
    transfer: JsonObject,
    project_ids: set[str],
    worker_ids: Sequence[str],
    as_of: datetime,
    horizon_end: datetime,
    index: int,
) -> _TransferSpec:
    from_project_id = _string(
        _required(transfer, "fromProjectId", "from_project_id"),
        f"transferAssumptions[{index}].fromProjectId",
    )
    to_project_id = _string(
        _required(transfer, "toProjectId", "to_project_id"),
        f"transferAssumptions[{index}].toProjectId",
    )
    if from_project_id not in project_ids or to_project_id not in project_ids:
        raise SimulationInputError(f"transferAssumptions[{index}] references an unknown project")
    if from_project_id == to_project_id:
        raise SimulationInputError(f"transferAssumptions[{index}] must connect two projects")
    worker_value = transfer.get("workerId", transfer.get("worker_id"))
    worker_id = (
        "*"
        if worker_value in (None, "")
        else _string(worker_value, f"transferAssumptions[{index}].workerId")
    )
    if worker_id != "*" and worker_id not in worker_ids:
        raise SimulationInputError(
            f"transferAssumptions[{index}] references unknown worker {worker_id!r}"
        )
    starts_value = transfer.get("startsAt", transfer.get("starts_at"))
    ends_value = transfer.get("endsAt", transfer.get("ends_at"))
    starts_at = (
        as_of
        if starts_value in (None, "")
        else _datetime(starts_value, f"transferAssumptions[{index}].startsAt")
    )
    ends_at = (
        horizon_end
        if ends_value in (None, "")
        else _datetime(ends_value, f"transferAssumptions[{index}].endsAt")
    )
    eligible = _string_tuple(
        transfer.get("eligibleTargetTaskIds", transfer.get("eligible_target_task_ids", [])),
        f"transferAssumptions[{index}].eligibleTargetTaskIds",
    )
    return _TransferSpec(
        worker_id=worker_id,
        from_project_id=from_project_id,
        to_project_id=to_project_id,
        starts_at=starts_at,
        ends_at=ends_at,
        eligible_target_task_ids=eligible,
        outbound_travel_hours=_optional_number(
            transfer.get("outboundTravelHours", transfer.get("outbound_travel_hours")),
            f"transferAssumptions[{index}].outboundTravelHours",
        ),
        return_travel_hours=_optional_number(
            transfer.get("returnTravelHours", transfer.get("return_travel_hours")),
            f"transferAssumptions[{index}].returnTravelHours",
        ),
        setup_hours=_optional_number(
            transfer.get("setupHours", transfer.get("setup_hours")),
            f"transferAssumptions[{index}].setupHours",
        ),
    )


def _parse_overtime_slots(
    value: object,
    as_of: datetime,
    horizon_end: datetime,
    local_timezone: ZoneInfo,
    label: str,
) -> tuple[_Interval, ...]:
    slots = _objects(value, label)
    result: list[_Interval] = []
    for index, slot in enumerate(slots):
        interval = _interval_from_object(slot, f"{label}[{index}]", local_timezone)
        if interval.end <= as_of or interval.start >= horizon_end:
            continue
        result.append(interval)
    return tuple(sorted(result, key=lambda item: (item.start, item.end)))


def _parse_effort(value: object, label: str, required: bool) -> tuple[float, float, float] | None:
    if value is None:
        if required:
            raise SimulationInputError(f"{label} is required for unfinished tasks")
        return None
    if isinstance(value, Mapping):
        optimistic = _number(value.get("optimistic"), f"{label}.optimistic")
        most_likely = _number(
            value.get("mostLikely", value.get("most_likely")), f"{label}.mostLikely"
        )
        pessimistic = _number(value.get("pessimistic"), f"{label}.pessimistic")
    else:
        fixed = _number(value, label)
        optimistic = most_likely = pessimistic = fixed
    if optimistic < 0 or most_likely < 0 or pessimistic < 0:
        raise SimulationInputError(f"{label} cannot contain negative hours")
    if not optimistic <= most_likely <= pessimistic:
        raise SimulationInputError(f"{label} must satisfy optimistic <= mostLikely <= pessimistic")
    return optimistic, most_likely, pessimistic


def _validate_task_graph(tasks: Sequence[_Task], task_by_id: Mapping[str, _Task]) -> None:
    for task in tasks:
        for predecessor_id in task.predecessor_ids:
            if predecessor_id not in task_by_id:
                raise SimulationInputError(
                    f"task {task.id!r} references unknown predecessor {predecessor_id!r}"
                )
            if predecessor_id == task.id:
                raise SimulationInputError(
                    f"task graph contains a predecessor cycle (self-loop at {task.id!r})"
                )
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise SimulationInputError("task graph contains a predecessor cycle")
        if task_id in visited:
            return
        visiting.add(task_id)
        for predecessor_id in task_by_id[task_id].predecessor_ids:
            visit(predecessor_id)
        visiting.remove(task_id)
        visited.add(task_id)

    for task in sorted(tasks, key=lambda item: item.id):
        visit(task.id)


def _sample_effort(data: _Input) -> dict[str, tuple[float, ...]]:
    draws: dict[str, tuple[float, ...]] = {}
    for task in sorted(data.tasks, key=lambda item: item.id):
        if task.status in {"done", "canceled"}:
            draws[task.id] = tuple(0.0 for _ in range(data.samples))
            continue
        rng = random.Random(_stable_seed(data.seed, "effort", task.id))
        if (
            task.effort_optimistic == task.effort_most_likely
            and task.effort_most_likely == task.effort_pessimistic
        ):
            values = [task.effort_most_likely] * data.samples
        else:
            values = [
                rng.triangular(
                    task.effort_optimistic,
                    task.effort_pessimistic,
                    task.effort_most_likely,
                )
                for _ in range(data.samples)
            ]
        draws[task.id] = tuple(values)
    return draws


def _run_scenario(
    data: _Input,
    draws: Mapping[str, Sequence[float]],
    action: _CandidateAction,
    progress: Callable[[int], None] | None = None,
) -> _ScheduleResult:
    _, rejection_reasons = _prepare_action(data, action)
    if rejection_reasons:
        # The baseline is an empty action; this guard also keeps the helper
        # honest if an internal caller tries to run a malformed action.
        raise SimulationInputError(
            f"cannot simulate action {action.id!r}: {', '.join(rejection_reasons)}"
        )
    schedules = []
    for sample_index in range(data.samples):
        schedules.append(_run_sample(data, draws, sample_index, action))
        if progress and ((sample_index + 1) % 25 == 0 or sample_index + 1 == data.samples):
            progress(sample_index + 1)
    aggregate = _aggregate_schedules(schedules)
    prepared, _ = _prepare_action(data, action)
    _attach_capacity_context(data, aggregate, action, prepared)
    return aggregate


def _run_sample(
    data: _Input,
    draws: Mapping[str, Sequence[float]],
    sample_index: int,
    action: _CandidateAction,
) -> _ScheduleResult:
    prepared, rejection_reasons = _prepare_action(data, action)
    if rejection_reasons:
        raise SimulationInputError(
            f"cannot simulate action {action.id!r}: {', '.join(rejection_reasons)}"
        )
    task_by_id = {task.id: task for task in data.tasks}
    task_finishes: dict[str, datetime | None] = {}
    remaining: dict[str, float] = {}
    for task in data.tasks:
        if task.status == "done":
            task_finishes[task.id] = task.completed_at or data.as_of
        elif task.status == "canceled":
            task_finishes[task.id] = None
        else:
            task_finishes[task.id] = None
            remaining[task.id] = float(draws[task.id][sample_index])
            if remaining[task.id] <= EPSILON:
                task_finishes[task.id] = max(_effective_earliest_start(task, prepared), data.as_of)

    diagnostics: set[tuple[str, str | None, str | None, str | None]] = set()
    changed_assignments: set[tuple[str, str, str]] = set()
    transfer_productive_hours = 0.0
    overtime_hours = 0.0
    transfer_used: set[str] = set()
    capacity_by_project_day: dict[str, dict[str, dict[str, float]]] = defaultdict(
        lambda: defaultdict(_capacity_row)
    )
    slots = _slot_starts(data.as_of, data.horizon_end)

    for slot_start in slots:
        slot_end = slot_start + HOUR
        unfinished_active = [
            task
            for task in data.tasks
            if task.status not in {"done", "canceled"}
            and task_finishes[task.id] is None
            and remaining.get(task.id, 0.0) > EPSILON
        ]
        if not unfinished_active:
            break

        ready_tasks: list[_Task] = []
        for task in unfinished_active:
            if _task_reserved(data.reservations, task, _Interval(slot_start, slot_end)):
                diagnostics.add(
                    ("task_blocked_by_hard_reservation", task.project_id, task.id, None)
                )
                continue
            readiness = _task_readiness(
                task,
                task_finishes,
                task_by_id,
                slot_start,
                _effective_earliest_start(task, prepared),
                _effective_material_ready_at(task, prepared),
            )
            if readiness == "ready":
                ready_tasks.append(task)
            elif readiness == "predecessor":
                diagnostics.add(
                    ("waiting_on_confirmed_predecessor", task.project_id, task.id, None)
                )
            elif readiness == "canceled_predecessor":
                diagnostics.add(
                    ("canceled_predecessor_requires_waiver", task.project_id, task.id, None)
                )
            elif readiness == "material":
                diagnostics.add(
                    ("waiting_on_confirmed_material_gate", task.project_id, task.id, None)
                )

        task_worker_options: dict[str, list[tuple[_Worker, float, bool]]] = {}
        worker_ready_task_count: dict[str, int] = defaultdict(int)
        for task in ready_tasks:
            options: list[tuple[_Worker, float, bool]] = []
            for worker in sorted(data.workers, key=lambda item: item.id):
                capacity, is_overtime, transfer_id = _worker_slot_capacity(
                    data,
                    worker,
                    task,
                    slot_start,
                    slot_end,
                    prepared,
                )
                if capacity <= EPSILON:
                    continue
                if not _worker_can_do_task(worker, task, data, prepared, slot_start, slot_end):
                    continue
                options.append((worker, capacity, is_overtime))
                worker_ready_task_count[worker.id] += 1
                if transfer_id is not None:
                    transfer_used.add(transfer_id)
            task_worker_options[task.id] = options

            if task.required_specialty_id is not None and not options:
                any_skilled = any(
                    worker.active and task.required_specialty_id in worker.specialties
                    for worker in data.workers
                )
                if not any_skilled:
                    diagnostics.add(
                        (
                            "unavailable_specialty_capacity",
                            task.project_id,
                            task.id,
                            task.required_specialty_id,
                        )
                    )
            if _task_workability(data, task, slot_start, prepared) < 1.0 - EPSILON:
                diagnostics.add(
                    ("task_exposed_to_reduced_workability", task.project_id, task.id, None)
                )

        for worker_id, count in worker_ready_task_count.items():
            if count > 1:
                diagnostics.add(("shared_worker_contention", None, None, worker_id))

        assigned_workers: set[str] = set()
        sorted_ready = sorted(
            ready_tasks,
            key=lambda task: _task_sort_key(data, task, action),
        )
        for task in sorted_ready:
            options = [
                option
                for option in task_worker_options.get(task.id, [])
                if option[0].id not in assigned_workers
            ]
            if len(options) < task.min_crew:
                if options:
                    diagnostics.add(("crew_below_minimum", task.project_id, task.id, None))
                continue
            crew = options[: task.max_crew]
            workability = _task_workability(data, task, slot_start, prepared)
            if workability <= EPSILON:
                diagnostics.add(("task_stop_work_gate", task.project_id, task.id, None))
                continue
            productive_hours = sum(option[1] for option in crew) * workability
            if productive_hours <= EPSILON:
                continue
            for worker, capacity, is_overtime in crew:
                assigned_workers.add(worker.id)
                capacity_row = capacity_by_project_day[task.project_id][
                    _project_day_key(data, task.project_id, slot_start)
                ]
                capacity_row["usedPersonHours"] += capacity
                capacity_row["effectiveWorkHours"] += capacity * workability
                if is_overtime:
                    overtime_hours += capacity
                    capacity_row["overtimeUsedHours"] += capacity
                transfer = _active_transfer_for_worker(prepared, worker.id, slot_start, slot_end)
                if transfer is not None and task.project_id == transfer.spec.to_project_id:
                    transfer_productive_hours += capacity
                    capacity_row["transferProductiveHours"] += capacity
                    changed_assignments.add((worker.id, task.id, task.project_id))
                elif transfer is not None:
                    diagnostics.add(
                        ("transfer_donor_capacity_reserved", task.project_id, task.id, worker.id)
                    )
            remaining[task.id] = max(0.0, remaining[task.id] - productive_hours)
            if remaining[task.id] <= EPSILON:
                task_finishes[task.id] = slot_end

    unfinished = frozenset(
        task.id
        for task in data.tasks
        if task.status not in {"done", "canceled"} and task_finishes[task.id] is None
    )
    project_finishes: dict[str, datetime | None] = {}
    for project in data.projects:
        project_tasks = [task for task in data.tasks if task.project_id == project.id]
        live_tasks = [task for task in project_tasks if task.status != "canceled"]
        if not live_tasks:
            project_finishes[project.id] = data.as_of
            continue
        finishes = [task_finishes[task.id] for task in live_tasks]
        project_finishes[project.id] = (
            max(finish for finish in finishes if finish is not None)
            if all(finish is not None for finish in finishes)
            else None
        )
    return _ScheduleResult(
        project_finishes=project_finishes,
        task_finishes=task_finishes,
        unfinished_tasks=unfinished,
        diagnostics=diagnostics,
        changed_assignments=changed_assignments,
        transfer_productive_hours=transfer_productive_hours,
        overtime_hours=overtime_hours,
        transfer_used=transfer_used,
        capacity_by_project_day={
            project_id: {day: dict(values) for day, values in days.items()}
            for project_id, days in capacity_by_project_day.items()
        },
    )


def _evaluate_candidate(
    data: _Input,
    draws: Mapping[str, Sequence[float]],
    action: _CandidateAction,
    baseline_action: _CandidateAction,
    baseline_outcomes: Mapping[str, dict[str, object]],
    progress: Callable[[int], None] | None = None,
) -> tuple[dict[str, object], _ScheduleResult | None]:
    effective_action, composition_reasons = _compose_actions(baseline_action, action)
    prepared, rejection_reasons = _prepare_action(data, effective_action)
    rejection_reasons = [*composition_reasons, *rejection_reasons]
    if rejection_reasons:
        return (
            _strategy_result(
                data=data,
                action=action,
                outcomes={},
                baseline_outcomes=baseline_outcomes,
                feasible=False,
                rejection_reasons=tuple(rejection_reasons),
                schedule=None,
                labels=(),
            ),
            None,
        )

    schedules: list[_ScheduleResult] = []
    try:
        for sample_index in range(data.samples):
            schedules.append(_run_sample(data, draws, sample_index, effective_action))
            if progress and ((sample_index + 1) % 25 == 0 or sample_index + 1 == data.samples):
                progress(sample_index + 1)
    except SimulationInputError as exc:
        return (
            _strategy_result(
                data=data,
                action=action,
                outcomes={},
                baseline_outcomes=baseline_outcomes,
                feasible=False,
                rejection_reasons=(str(exc),),
                schedule=None,
                labels=(),
            ),
            None,
        )
    aggregate = _aggregate_schedules(schedules)
    _attach_capacity_context(data, aggregate, effective_action, prepared)
    reasons = list(_guardrail_rejections(data, aggregate, baseline_outcomes, prepared))
    if action.transfers and aggregate.transfer_productive_hours <= EPSILON:
        reasons.append("transfer_has_no_useful_target_capacity")
    outcomes = _summarize_outcomes(data, aggregate)
    result = _strategy_result(
        data=data,
        action=action,
        outcomes=outcomes,
        baseline_outcomes=baseline_outcomes,
        feasible=not reasons,
        rejection_reasons=tuple(_dedupe_strings(reasons)),
        schedule=aggregate,
        labels=(),
    )
    return result, aggregate if not reasons else None


def _capacity_row() -> dict[str, float]:
    return {
        "availablePersonHours": 0.0,
        "regularAvailablePersonHours": 0.0,
        "overtimeAvailablePersonHours": 0.0,
        "transferAvailablePersonHours": 0.0,
        "usedPersonHours": 0.0,
        "effectiveWorkHours": 0.0,
        "overtimeUsedHours": 0.0,
        "transferProductiveHours": 0.0,
        "transferTravelHours": 0.0,
        "transferSetupHours": 0.0,
        "transferNonProductiveHours": 0.0,
    }


def _project_day_key(data: _Input, project_id: str, instant: datetime) -> str:
    project = next(project for project in data.projects if project.id == project_id)
    return instant.astimezone(project.timezone).date().isoformat()


def _capacity_worker_is_eligible(
    worker: _Worker,
    project_id: str,
    tasks: Sequence[_Task],
    prepared: _PreparedAction,
) -> bool:
    for task in tasks:
        if task.project_id != project_id or task.status in {"done", "canceled"}:
            continue
        if _baseline_worker_can_do_task(worker, task):
            return True
        for transfer in prepared.transfers:
            if (
                transfer.spec.worker_id == worker.id
                and transfer.spec.to_project_id == project_id
                and (
                    not transfer.spec.eligible_target_task_ids
                    or task.id in transfer.spec.eligible_target_task_ids
                )
                and (
                    task.required_specialty_id is None
                    or task.required_specialty_id in worker.specialties
                )
            ):
                return True
    return False


def _attach_capacity_context(
    data: _Input,
    schedule: _ScheduleResult,
    action: _CandidateAction,
    prepared: _PreparedAction,
) -> None:
    """Attach exact calendar capacity and transfer friction to an aggregate run."""

    available: dict[str, dict[str, dict[str, float]]] = defaultdict(
        lambda: defaultdict(_capacity_row)
    )
    active_tasks = [task for task in data.tasks if task.status not in {"done", "canceled"}]
    for slot_start in _slot_starts(data.as_of, data.horizon_end):
        slot_end = slot_start + HOUR
        for project in sorted(data.projects, key=lambda item: item.id):
            day = _project_day_key(data, project.id, slot_start)
            row = available[project.id][day]
            for worker in sorted(data.workers, key=lambda item: item.id):
                if not worker.active or not _capacity_worker_is_eligible(
                    worker, project.id, active_tasks, prepared
                ):
                    continue
                if _has_worker_reservation(
                    data.reservations, worker.id, _Interval(slot_start, slot_end)
                ):
                    continue
                transfer = _active_transfer_for_worker(prepared, worker.id, slot_start, slot_end)
                if transfer is not None:
                    if transfer.spec.to_project_id != project.id:
                        continue
                    transfer_capacity = _overlap_hours(
                        _Interval(slot_start, slot_end),
                        _Interval(transfer.productive_start, transfer.productive_end),
                    )
                    if transfer_capacity <= EPSILON:
                        continue
                    row["availablePersonHours"] += transfer_capacity
                    row["transferAvailablePersonHours"] += transfer_capacity
                    continue
                normal = _normal_calendar_capacity(worker, slot_start, slot_end)
                overtime = _overtime_calendar_capacity(
                    worker, prepared.overtime, slot_start, slot_end
                )
                if normal >= overtime and normal > EPSILON:
                    row["availablePersonHours"] += normal
                    row["regularAvailablePersonHours"] += normal
                elif overtime > EPSILON:
                    row["availablePersonHours"] += overtime
                    row["overtimeAvailablePersonHours"] += overtime

    for transfer in prepared.transfers:
        spec = transfer.spec
        target_day = _project_day_key(data, spec.to_project_id, spec.starts_at)
        row = available[spec.to_project_id][target_day]
        outbound = float(spec.outbound_travel_hours or 0.0)
        returning = float(spec.return_travel_hours or 0.0)
        setup = float(spec.setup_hours or 0.0)
        row["transferTravelHours"] += outbound + returning
        row["transferSetupHours"] += setup
        row["transferNonProductiveHours"] += outbound + returning + setup

    schedule.available_capacity_by_project_day = {
        project_id: {
            day: {key: round(value, 6) for key, value in values.items()}
            for day, values in days.items()
        }
        for project_id, days in available.items()
    }
    for project_id, days in schedule.available_capacity_by_project_day.items():
        for day, values in days.items():
            row = schedule.capacity_by_project_day.setdefault(project_id, {}).setdefault(
                day, _capacity_row()
            )
            for key in (
                "availablePersonHours",
                "regularAvailablePersonHours",
                "overtimeAvailablePersonHours",
                "transferAvailablePersonHours",
                "transferTravelHours",
                "transferSetupHours",
                "transferNonProductiveHours",
            ):
                row[key] = values.get(key, 0.0)


def _aggregate_schedules(schedules: Sequence[_ScheduleResult]) -> _ScheduleResult:
    if not schedules:
        raise SimulationInputError("cannot aggregate an empty simulation")
    project_ids = sorted(schedules[0].project_finishes)
    project_finishes: dict[str, datetime | None] = {}
    for project_id in project_ids:
        finishes = [schedule.project_finishes[project_id] for schedule in schedules]
        # A list of finishes cannot be represented as one datetime.  The
        # summarizer receives the individual schedules through the attached
        # private attribute below; this aggregate is only for operational facts
        # and diagnostics.
        project_finishes[project_id] = (
            None if any(finish is None for finish in finishes) else finishes[0]
        )
    task_finishes: dict[str, datetime | None] = {}
    diagnostics = set().union(*(schedule.diagnostics for schedule in schedules))
    changed_assignments = set().union(*(schedule.changed_assignments for schedule in schedules))
    unfinished = frozenset().union(*(schedule.unfinished_tasks for schedule in schedules))
    capacity_totals: dict[str, dict[str, dict[str, float]]] = defaultdict(
        lambda: defaultdict(_capacity_row)
    )
    for schedule in schedules:
        for project_id, days in schedule.capacity_by_project_day.items():
            for day, values in days.items():
                target = capacity_totals[project_id][day]
                for key, value in values.items():
                    target[key] += value
    capacity_by_project_day = {
        project_id: {
            day: {key: round(value / len(schedules), 6) for key, value in values.items()}
            for day, values in days.items()
        }
        for project_id, days in capacity_totals.items()
    }
    aggregate = _ScheduleResult(
        project_finishes=project_finishes,
        task_finishes=task_finishes,
        unfinished_tasks=unfinished,
        diagnostics=diagnostics,
        changed_assignments=changed_assignments,
        transfer_productive_hours=sum(schedule.transfer_productive_hours for schedule in schedules)
        / len(schedules),
        overtime_hours=sum(schedule.overtime_hours for schedule in schedules) / len(schedules),
        transfer_used=set().union(*(schedule.transfer_used for schedule in schedules)),
        capacity_by_project_day=capacity_by_project_day,
    )
    # Dataclasses are intentionally kept small, but the summarizer must retain
    # each trajectory to preserve censoring.  This private attachment avoids
    # leaking an internal representation in the public output.
    aggregate.samples = tuple(schedules)
    return aggregate


def _action_count(action: _CandidateAction) -> int:
    return (
        len(action.transfers)
        + len(action.overtime)
        + len(action.date_shifts)
        + len(action.materials)
        + len(action.weather)
        + int(bool(action.project_order or action.task_order))
    )


def _action_signature(action: _CandidateAction) -> tuple[object, ...]:
    return (
        tuple(action.transfers),
        tuple(action.overtime),
        tuple(action.date_shifts),
        tuple(action.materials),
        tuple(action.weather),
        action.project_order,
        action.task_order,
    )


def _evaluation_signature(action: _CandidateAction) -> tuple[object, ...]:
    """Identify equivalent Monte Carlo work, ignoring candidate IDs and source labels."""

    return (
        *_action_signature(action),
        action.priority_action_type,
        action.validation_errors,
    )


def _unique_evaluation_candidates(
    candidates: Sequence[_CandidateAction],
) -> list[_CandidateAction]:
    """Keep the first candidate for each distinct parameter set before sampling.

    Proposed extras are prepended to generated or explicit bundles, so the same
    transfers, overtime windows, or priority orders can appear twice under
    different IDs.  Re-running those trajectories does not change the forecast.
    """

    unique: list[_CandidateAction] = []
    seen: set[tuple[object, ...]] = set()
    for candidate in candidates:
        signature = _evaluation_signature(candidate)
        if signature in seen:
            continue
        seen.add(signature)
        unique.append(candidate)
    return unique


def _action_item_signatures(action: _CandidateAction) -> set[tuple[str, object]]:
    return {
        *{("transfer", item) for item in action.transfers},
        *{("overtime", item) for item in action.overtime},
        *{("date_shift", item) for item in action.date_shifts},
        *{("material", item) for item in action.materials},
        *{("weather", item) for item in action.weather},
    }


def _compose_actions(
    baseline: _CandidateAction,
    candidate: _CandidateAction,
) -> tuple[_CandidateAction, list[str]]:
    """Overlay a proposed bundle on confirmed baseline actions without duplication."""

    reasons: list[str] = list(candidate.validation_errors)
    project_order = baseline.project_order
    task_order = baseline.task_order
    priority_action_type = baseline.priority_action_type
    if candidate.project_order or candidate.task_order:
        if (baseline.project_order or baseline.task_order) and (
            baseline.project_order != candidate.project_order
            or baseline.task_order != candidate.task_order
        ):
            reasons.append("conflicting_priority_overlay_with_implemented_action")
        else:
            project_order = candidate.project_order
            task_order = candidate.task_order
            priority_action_type = candidate.priority_action_type

    def combine[T](left: Sequence[T], right: Sequence[T]) -> tuple[T, ...]:
        result: list[T] = []
        for item in (*left, *right):
            if item not in result:
                result.append(item)
        return tuple(result)

    return (
        _CandidateAction(
            id=candidate.id,
            transfers=combine(baseline.transfers, candidate.transfers),
            overtime=combine(baseline.overtime, candidate.overtime),
            date_shifts=combine(baseline.date_shifts, candidate.date_shifts),
            materials=combine(baseline.materials, candidate.materials),
            weather=combine(baseline.weather, candidate.weather),
            project_order=project_order,
            task_order=task_order,
            priority_action_type=priority_action_type,
            source=candidate.source,
            validation_errors=tuple(reasons),
            implemented_signatures=baseline.implemented_signatures,
        ),
        reasons,
    )


def _action_outputs(data: _Input, action: _CandidateAction) -> list[dict[str, object]]:
    prepared, _ = _prepare_action(data, action)
    return _action_outputs_from_prepared(action, prepared)


def _action_outputs_from_prepared(
    action: _CandidateAction,
    prepared: _PreparedAction,
) -> list[dict[str, object]]:
    output = [_transfer_output(item) for item in prepared.transfers]
    output.extend(_overtime_output(item) for item in prepared.overtime)
    output.extend(_date_shift_output(item) for item in prepared.date_shifts)
    output.extend(_material_output(item) for item in prepared.materials)
    output.extend(_weather_output(item) for item in prepared.weather)
    if action.project_order:
        item: dict[str, object] = {
            "type": action.priority_action_type,
            "projectOrder": list(action.project_order),
        }
        if action.priority_action_type == "resequence":
            item.update(
                {
                    "capability": "manual",
                    "modelledEffect": "dispatch_order_only",
                }
            )
        output.append(item)
    if action.task_order:
        item = {
            "type": action.priority_action_type,
            "taskOrder": list(action.task_order),
        }
        if action.priority_action_type == "resequence":
            item.update(
                {
                    "capability": "manual",
                    "modelledEffect": "dispatch_order_only",
                }
            )
        output.append(item)
    return output


def _strategy_result(
    data: _Input,
    action: _CandidateAction,
    outcomes: Mapping[str, dict[str, object]],
    baseline_outcomes: Mapping[str, dict[str, object]],
    feasible: bool,
    rejection_reasons: Sequence[str],
    schedule: _ScheduleResult | None,
    labels: Sequence[str],
) -> dict[str, object]:
    prepared, _ = _prepare_action(data, action)
    transfers = prepared.transfers if prepared is not None else ()
    transfer_non_productive = sum(transfer.non_productive_hours for transfer in transfers)
    if schedule is not None:
        changed_assignments = [
            {"workerId": worker_id, "taskId": task_id, "projectId": project_id}
            for worker_id, task_id, project_id in sorted(schedule.changed_assignments)
        ]
        measured_overtime = round(schedule.overtime_hours, 6)
    else:
        changed_assignments = []
        measured_overtime = 0.0
    deltas = {
        project_id: _outcome_delta(baseline_outcomes.get(project_id), outcome)
        for project_id, outcome in sorted(outcomes.items())
    }
    affected = sorted(
        {
            project_id
            for transfer in transfers
            for project_id in (transfer.spec.from_project_id, transfer.spec.to_project_id)
        }
        | {item["projectId"] for item in changed_assignments}
        | {
            task.project_id
            for task in data.tasks
            for date_shift in prepared.date_shifts
            if task.id == date_shift.task_id
        }
        | {
            task.project_id
            for task in data.tasks
            for material in prepared.materials
            if task.id == material.task_id
        }
        | {
            project_id
            for weather in prepared.weather
            for project_id in (
                (weather.project_id,)
                if weather.project_id is not None
                else tuple(task.project_id for task in data.tasks if task.id == weather.task_id)
            )
        }
    )
    action_output = _action_outputs_from_prepared(action, prepared)
    guardrail_checks = _guardrail_details(data, schedule, baseline_outcomes, outcomes, prepared)
    return {
        "id": action.id,
        "labels": list(labels),
        "actions": action_output,
        "feasible": feasible,
        "rejectionReasons": list(rejection_reasons),
        "outcomesByProject": dict(sorted(outcomes.items())),
        "deltasByProject": deltas,
        "overtimeHours": measured_overtime,
        "transferNonProductiveHours": round(transfer_non_productive, 6),
        "actionCount": _action_count(action),
        "changedAssignments": changed_assignments,
        "affectedProjectIds": affected,
        "guardrailChecks": guardrail_checks,
        "explanationReferences": [],
    }


def _summarize_outcomes(data: _Input, schedule: _ScheduleResult) -> dict[str, dict[str, object]]:
    schedules = schedule.samples or (schedule,)
    results: dict[str, dict[str, object]] = {}
    project_by_id = {project.id: project for project in data.projects}
    for project in data.projects:
        finishes = [item.project_finishes[project.id] for item in schedules]
        unfinished_count = sum(finish is None for finish in finishes)
        known_late_count = sum(
            finish is not None and finish > project.target_finish_at for finish in finishes
        )
        unknown_censored_count = 0
        for finish in finishes:
            if finish is None:
                if data.horizon_end >= project.target_finish_at:
                    known_late_count += 1
                else:
                    unknown_censored_count += 1
        delay_probability: float | None
        if unknown_censored_count:
            delay_probability = None
        else:
            delay_probability = round(known_late_count / data.samples, 6)

        complete = unfinished_count == 0
        positive_delays = [
            max(0.0, (finish - project.target_finish_at).total_seconds() / 86400.0)
            for finish in finishes
            if finish is not None
        ]
        expected_positive: float | None = (
            round(sum(positive_delays) / len(positive_delays), 6)
            if complete and positive_delays
            else None
        )
        finish_quantiles: dict[str, str | None]
        if complete:
            finish_quantiles = {
                "finishP10": _format_project_datetime(
                    _quantile_datetime(finishes, 0.10), project_by_id[project.id]
                ),
                "finishP50": _format_project_datetime(
                    _quantile_datetime(finishes, 0.50), project_by_id[project.id]
                ),
                "finishP90": _format_project_datetime(
                    _quantile_datetime(finishes, 0.90), project_by_id[project.id]
                ),
            }
        else:
            finish_quantiles = {"finishP10": None, "finishP50": None, "finishP90": None}
        missing_reason = None
        if unfinished_count:
            missing_reason = (
                "beyond_simulated_horizon_before_target"
                if data.horizon_end < project.target_finish_at
                else "beyond_simulated_horizon_after_target_known_late"
            )
        histogram = _finish_histogram(data, project, finishes)
        capacity = _capacity_output(schedule, project.id)
        traces = _representative_traces(data, project, schedules)
        results[project.id] = {
            "projectId": project.id,
            "projectName": project.name,
            "targetFinishAt": _format_project_datetime(project.target_finish_at, project),
            "delayProbability": delay_probability,
            "expectedPositiveDelayDays": expected_positive,
            **finish_quantiles,
            "unfinishedCount": unfinished_count,
            "sampleCount": data.samples,
            "censored": unfinished_count > 0,
            "knownLateCount": known_late_count,
            "unknownCensoredCount": unknown_censored_count,
            "missingReason": missing_reason,
            "censoring": {
                "censored": unfinished_count > 0,
                "unfinishedCount": unfinished_count,
                "unknownBeforeTargetCount": unknown_censored_count,
                "knownLateUnfinishedCount": (unfinished_count - unknown_censored_count),
                "horizonEnd": _format_project_datetime(data.horizon_end, project),
                "reason": missing_reason,
            },
            "finishHistogram": histogram,
            "histogramCoverage": {
                "from": _format_project_datetime(data.as_of, project),
                "to": _format_project_datetime(data.horizon_end, project),
                "unfinishedBucket": True,
            },
            "capacityByDay": capacity,
            "capacityBasis": (
                "eligible_worker_calendar; shared workers may appear in multiple project rows"
            ),
            "traces": traces,
            "representativeTraces": traces,
        }
    return dict(sorted(results.items()))


def _finish_histogram(
    data: _Input,
    project: _Project,
    finishes: Sequence[datetime | None],
) -> list[dict[str, object]]:
    counts: dict[str, int] = defaultdict(int)
    for finish in finishes:
        if finish is not None:
            counts[finish.astimezone(project.timezone).date().isoformat()] += 1
    result: list[dict[str, object]] = []
    for bucket in sorted(counts):
        result.append(
            {
                "bucket": bucket,
                "label": bucket,
                "count": counts[bucket],
                "probability": round(counts[bucket] / len(finishes), 6),
                "censored": False,
            }
        )
    unfinished_count = sum(finish is None for finish in finishes)
    result.append(
        {
            "bucket": "unfinished",
            "label": "Beyond simulated horizon",
            "count": unfinished_count,
            "probability": round(unfinished_count / len(finishes), 6),
            "censored": True,
            "missingReason": (
                "beyond_simulated_horizon_before_target"
                if data.horizon_end < project.target_finish_at
                else "beyond_simulated_horizon_after_target_known_late"
            ),
        }
    )
    return result


def _capacity_output(
    schedule: _ScheduleResult,
    project_id: str,
) -> list[dict[str, object]]:
    days = schedule.capacity_by_project_day.get(project_id, {})
    return [
        {
            "date": day,
            **{key: round(value, 6) for key, value in values.items()},
        }
        for day, values in sorted(days.items())
    ]


def _representative_traces(
    data: _Input,
    project: _Project,
    schedules: Sequence[_ScheduleResult],
) -> list[dict[str, object]]:
    if not schedules:
        return []
    indices = sorted(
        {
            0,
            len(schedules) // 2,
            len(schedules) - 1,
        }
    )[:MAX_REPRESENTATIVE_TRACES]
    project_tasks = sorted(
        (task for task in data.tasks if task.project_id == project.id),
        key=lambda task: task.id,
    )
    traces: list[dict[str, object]] = []
    for index in indices:
        sample = schedules[index]
        finish = sample.project_finishes[project.id]
        task_finishes = [
            {
                "taskId": task.id,
                "finishAt": (
                    _format_project_datetime(task_finish, project)
                    if (task_finish := sample.task_finishes.get(task.id)) is not None
                    else None
                ),
            }
            for task in project_tasks
        ]
        traces.append(
            {
                "sampleIndex": index,
                "finishAt": (
                    _format_project_datetime(finish, project) if finish is not None else None
                ),
                "status": "completed" if finish is not None else "censored",
                "unfinishedTaskIds": sorted(
                    task.id
                    for task in project_tasks
                    if sample.task_finishes.get(task.id) is None
                    and task.status not in {"done", "canceled"}
                ),
                "taskFinishes": task_finishes,
            }
        )
    return traces


def _select_strategies(
    data: _Input,
    baseline: Mapping[str, dict[str, object]],
    candidates: Sequence[dict[str, object]],
) -> list[dict[str, object]]:
    material: list[dict[str, object]] = []
    seen_signatures: set[tuple[object, ...]] = set()
    for candidate in candidates:
        candidate["rankingComponents"] = _ranking_components(data, candidate)
        if not _material_target_improvement(data, baseline, candidate):
            continue
        signature = _candidate_outcome_signature(candidate)
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        material.append(candidate)
    if not material:
        return []

    if data.decision_scope == "portfolio":
        # Publish the strongest distinct action approaches, not three fixed
        # personas. Similar overtime dates are one approach, not extra choices.
        ranked = sorted(
            material, key=lambda item: (*_balanced_ranking_key(data, item), str(item["id"]))
        )
        selected: list[dict[str, object]] = []
        approaches: set[tuple[tuple[str, ...], ...]] = set()
        names = {
            "transfer": "Move workers",
            "overtime": "Add overtime",
            "priority": "Change work order",
            "date_shift": "Reschedule work",
            "material": "Change delivery timing",
            "weather": "Reschedule weather-sensitive work",
        }
        for candidate in ranked:
            actions = candidate.get("actions", [])
            approach = tuple(
                sorted(
                    {
                        tuple(
                            str(action.get(key, ""))
                            for key in (
                                "type",
                                "workerId",
                                "fromProjectId",
                                "toProjectId",
                                "taskId",
                            )
                        )
                        for action in actions
                        if isinstance(action, Mapping)
                    }
                )
            )
            if approach in approaches:
                continue
            approaches.add(approach)
            title = " + ".join(
                dict.fromkeys(names.get(action[0], "Adjust the schedule") for action in approach)
            )
            rank = len(selected) + 1
            candidate.update(
                {
                    "rank": rank,
                    "strategy": "recommended" if rank == 1 else "alternative",
                    "strategyName": title,
                    "name": title,
                    "label": title,
                    "strategyRoles": ["recommended" if rank == 1 else "alternative"],
                    "labels": ["recommended"] if rank == 1 else [],
                }
            )
            selected.append(candidate)
            if len(selected) == 4:
                break
        return selected

    policies = (
        ("fast", "Fast", "fastestRecovery", _fast_ranking_key),
        ("balanced", "Balanced", "portfolioBalance", _balanced_ranking_key),
        ("safe", "Safe", "leastDisruption", _safe_ranking_key),
    )
    selected: list[dict[str, object]] = []
    for role, name, legacy_label, key_function in policies:
        candidate = min(
            material,
            key=lambda item: (*key_function(data, item), str(item["id"])),
        )
        if candidate in selected:
            roles = candidate.setdefault("strategyRoles", [])
            if isinstance(roles, list) and role not in roles:
                roles.append(role)
            labels = candidate.setdefault("labels", [])
            if isinstance(labels, list) and legacy_label not in labels:
                labels.append(legacy_label)
            continue
        candidate["strategy"] = role
        candidate["strategyName"] = name
        candidate["strategyRoles"] = [role]
        labels = candidate.get("labels", [])
        candidate["labels"] = [
            *(labels if isinstance(labels, list) else []),
            name,
            legacy_label,
        ]
        selected.append(candidate)
    return selected[:3]


def _numeric(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _rank_metric(value: object, unknown_count: object) -> tuple[object, ...]:
    number = _numeric(value)
    if number is not None:
        return (0, number)
    unknown = _numeric(unknown_count) or 0.0
    return (1, unknown)


def _outcomes(candidate: Mapping[str, object]) -> Mapping[str, object]:
    value = candidate.get("outcomesByProject", {})
    return value if isinstance(value, Mapping) else {}


def _weighted_outcome_metric(
    data: _Input,
    candidate: Mapping[str, object],
    field_name: str,
) -> tuple[float | None, int]:
    outcomes = _outcomes(candidate)
    total = 0.0
    unknown_count = 0
    for project in data.projects:
        outcome = outcomes.get(project.id)
        if not isinstance(outcome, Mapping):
            unknown_count += 1
            continue
        value = _numeric(outcome.get(field_name))
        if value is None:
            unknown_count += int(outcome.get("unknownCensoredCount", 1) or 1)
        else:
            total += value * _priority_weight(data, project)
    return (None if unknown_count else total, unknown_count)


def _priority_weight(data: _Input, project: _Project) -> float:
    priorities = [item.priority for item in data.projects]
    if not priorities or max(priorities) - min(priorities) <= EPSILON:
        return 1.0
    if project.priority <= EPSILON:
        return 1.0
    return float(max(1, min(3, round(project.priority))))


def _date_metric(outcome: Mapping[str, object], field_name: str, target: datetime) -> float | None:
    value = outcome.get(field_name)
    if not isinstance(value, str):
        return None
    try:
        return (_datetime(value, field_name) - target).total_seconds() / 86400.0
    except SimulationInputError:
        return None


def _negative_buffer_exposure(candidate: Mapping[str, object]) -> tuple[float | None, int]:
    deltas = candidate.get("deltasByProject", {})
    if not isinstance(deltas, Mapping):
        return None, 1
    exposure = 0.0
    unknown = 0
    for delta in deltas.values():
        if not isinstance(delta, Mapping):
            unknown += 1
            continue
        before = _numeric(delta.get("targetDateBufferDaysBefore"))
        after = _numeric(delta.get("targetDateBufferDaysAfter"))
        if before is None or after is None:
            unknown += int(bool(delta.get("knownMissingCommitments")))
            continue
        exposure += max(0.0, before - after)
    return (None if unknown else exposure, unknown)


def _disruption_components(
    data: _Input,
    candidate: Mapping[str, object],
) -> dict[str, float]:
    worker_by_id = {worker.id: worker for worker in data.workers}
    changed_worker_days = 0.0
    actions = candidate.get("actions", [])
    if isinstance(actions, Sequence) and not isinstance(actions, (str, bytes, bytearray)):
        for action in actions:
            if not isinstance(action, Mapping):
                continue
            worker_id = action.get("workerId")
            worker = worker_by_id.get(str(worker_id)) if worker_id is not None else None
            if worker is None:
                continue
            starts = action.get("startsAt")
            ends = action.get("endsAt")
            if not isinstance(starts, str) or not isinstance(ends, str):
                continue
            try:
                hours = (
                    _datetime(ends, "action endsAt") - _datetime(starts, "action startsAt")
                ).total_seconds() / 3600.0
            except SimulationInputError:
                continue
            shift_hours = (
                datetime.combine(date.min, worker.shift_end)
                - datetime.combine(date.min, worker.shift_start)
            ).total_seconds() / 3600.0
            if shift_hours <= EPSILON:
                shift_hours += 24.0
            changed_worker_days += max(0.0, hours) / shift_hours
    overtime_hours = _numeric(candidate.get("overtimeHours")) or 0.0
    return {
        "changedWorkerDays": round(changed_worker_days, 6),
        "overtimeHours": round(overtime_hours, 6),
        "transferOverheadHours": round(
            _numeric(candidate.get("transferNonProductiveHours")) or 0.0,
            6,
        ),
        "changedAssignmentCount": float(len(candidate.get("changedAssignments", [])))
        if isinstance(candidate.get("changedAssignments", []), Sequence)
        else 0.0,
        "actionCount": float(_numeric(candidate.get("actionCount")) or 0.0),
    }


def _ranking_components(data: _Input, candidate: Mapping[str, object]) -> dict[str, object]:
    outcomes = _outcomes(candidate)
    target = outcomes.get(data.target_project_id)
    target_outcome = target if isinstance(target, Mapping) else {}
    target_project = next(
        project for project in data.projects if project.id == data.target_project_id
    )
    fast_risk = target_outcome.get("delayProbability")
    fast_unknown = target_outcome.get("unknownCensoredCount", 0)
    fast_delay = target_outcome.get("expectedPositiveDelayDays")
    fast_p90 = _date_metric(target_outcome, "finishP90", target_project.target_finish_at)
    balanced_risk, balanced_unknown = _weighted_outcome_metric(data, candidate, "delayProbability")
    balanced_delay, balanced_delay_unknown = _weighted_outcome_metric(
        data, candidate, "expectedPositiveDelayDays"
    )
    negative_buffer, negative_buffer_unknown = _negative_buffer_exposure(candidate)
    p90_values: list[float] = []
    p90_unknown = 0
    for project in data.projects:
        outcome = outcomes.get(project.id)
        if not isinstance(outcome, Mapping):
            p90_unknown += 1
            continue
        value = _date_metric(outcome, "finishP90", project.target_finish_at)
        if value is None:
            p90_unknown += int(outcome.get("unknownCensoredCount", 1) or 1)
        else:
            p90_values.append(value)
    disruption = _disruption_components(data, candidate)
    return {
        "policyVersion": "v4",
        "fast": {
            "priorityProjectId": data.target_project_id,
            "lateProbability": fast_risk,
            "unknownCensoredCount": fast_unknown,
            "expectedPositiveDelayDays": fast_delay,
            "finishP90DelayDays": fast_p90,
        },
        "balanced": {
            "priorityWeightedLateProbability": balanced_risk,
            "unknownCensoredCount": balanced_unknown,
            "priorityWeightedExpectedPositiveDelayDays": balanced_delay,
            "expectedDelayUnknownCensoredCount": balanced_delay_unknown,
            "negativeBufferChangeDays": negative_buffer,
            "negativeBufferUnknownCensoredCount": negative_buffer_unknown,
        },
        "safe": {
            "worstProjectLateProbability": (
                max(
                    float(outcome["delayProbability"])
                    for outcome in outcomes.values()
                    if isinstance(outcome, Mapping)
                    and isinstance(outcome.get("delayProbability"), (int, float))
                )
                if p90_unknown == 0 and outcomes
                else None
            ),
            "unknownCensoredCount": p90_unknown,
            "worstP90DelayDays": max(p90_values) if p90_values and p90_unknown == 0 else None,
            "worstBufferExposureDays": negative_buffer,
            "bufferUnknownCensoredCount": negative_buffer_unknown,
        },
        "disruption": disruption,
    }


def _fast_ranking_key(data: _Input, candidate: Mapping[str, object]) -> tuple[object, ...]:
    components = candidate.get("rankingComponents", {})
    fast = components.get("fast", {}) if isinstance(components, Mapping) else {}
    disruption = components.get("disruption", {}) if isinstance(components, Mapping) else {}
    return (
        *_rank_metric(fast.get("lateProbability"), fast.get("unknownCensoredCount")),
        *_rank_metric(fast.get("expectedPositiveDelayDays"), fast.get("unknownCensoredCount")),
        *_rank_metric(fast.get("finishP90DelayDays"), fast.get("unknownCensoredCount")),
        disruption.get("changedWorkerDays", math.inf),
        disruption.get("overtimeHours", math.inf),
        disruption.get("transferOverheadHours", math.inf),
    )


def _balanced_ranking_key(data: _Input, candidate: Mapping[str, object]) -> tuple[object, ...]:
    components = candidate.get("rankingComponents", {})
    balanced = components.get("balanced", {}) if isinstance(components, Mapping) else {}
    disruption = components.get("disruption", {}) if isinstance(components, Mapping) else {}
    return (
        *(
            _portfolio_outcome_key(data, _outcomes(candidate))
            if data.decision_scope == "portfolio"
            else ()
        ),
        *_rank_metric(
            balanced.get("priorityWeightedLateProbability"),
            balanced.get("unknownCensoredCount"),
        ),
        *_rank_metric(
            balanced.get("priorityWeightedExpectedPositiveDelayDays"),
            balanced.get("expectedDelayUnknownCensoredCount"),
        ),
        *_rank_metric(
            balanced.get("negativeBufferChangeDays"),
            balanced.get("negativeBufferUnknownCensoredCount"),
        ),
        disruption.get("changedWorkerDays", math.inf),
        disruption.get("overtimeHours", math.inf),
        disruption.get("transferOverheadHours", math.inf),
    )


def _safe_ranking_key(data: _Input, candidate: Mapping[str, object]) -> tuple[object, ...]:
    components = candidate.get("rankingComponents", {})
    safe = components.get("safe", {}) if isinstance(components, Mapping) else {}
    disruption = components.get("disruption", {}) if isinstance(components, Mapping) else {}
    return (
        *_rank_metric(safe.get("worstProjectLateProbability"), safe.get("unknownCensoredCount")),
        *_rank_metric(safe.get("worstP90DelayDays"), safe.get("unknownCensoredCount")),
        *_rank_metric(safe.get("worstBufferExposureDays"), safe.get("bufferUnknownCensoredCount")),
        disruption.get("changedWorkerDays", math.inf),
        disruption.get("overtimeHours", math.inf),
        disruption.get("transferOverheadHours", math.inf),
    )


def _material_target_improvement(
    data: _Input,
    baseline: Mapping[str, dict[str, object]],
    candidate: Mapping[str, object],
) -> bool:
    if data.decision_scope == "portfolio":
        return _material_portfolio_improvement(data, baseline, candidate)
    target_baseline = baseline.get(data.target_project_id)
    outcomes = candidate.get("outcomesByProject")
    if not isinstance(target_baseline, Mapping) or not isinstance(outcomes, Mapping):
        return False
    target_candidate = outcomes.get(data.target_project_id)
    if not isinstance(target_candidate, Mapping):
        return False
    # A candidate may retain censoring, but it must not make another project's
    # known lower bound or completion coverage worse.  Unknown outcomes are
    # never imputed as zero delay.
    for project in data.projects:
        before = baseline.get(project.id)
        after = outcomes.get(project.id)
        if not isinstance(before, Mapping) or not isinstance(after, Mapping):
            return False
        if _outcome_is_worse_under_censoring(before, after):
            return False

    return _outcome_is_materially_better(target_baseline, target_candidate)


def _portfolio_outcome_key(data: _Input, outcomes: Mapping[str, object]) -> tuple[float | int, ...]:
    """Compare coverage before risk and delay; censored finishes never become zero delay."""
    unknown = 0.0
    unfinished = 0.0
    for project in data.projects:
        outcome = outcomes.get(project.id)
        if not isinstance(outcome, Mapping):
            return (math.inf,)
        weight = _priority_weight(data, project)
        unknown += weight * float(outcome.get("unknownCensoredCount", 0) or 0)
        unfinished += weight * float(outcome.get("unfinishedCount", 0) or 0)
    candidate = {"outcomesByProject": outcomes}
    risk, risk_unknown = _weighted_outcome_metric(data, candidate, "delayProbability")
    delay, delay_unknown = _weighted_outcome_metric(data, candidate, "expectedPositiveDelayDays")
    return (
        unknown,
        unfinished,
        *_rank_metric(risk, risk_unknown),
        *_rank_metric(delay, delay_unknown),
    )


def _material_portfolio_improvement(
    data: _Input,
    baseline: Mapping[str, dict[str, object]],
    candidate: Mapping[str, object],
) -> bool:
    """Allow measured cross-project compromises without hiding missing evidence or hard limits."""
    outcomes = _outcomes(candidate)
    protected = set(
        data.guardrails.get("protectedProjectIds", data.guardrails.get("protected_project_ids", []))
    )
    for project in data.projects:
        before, after = baseline.get(project.id), outcomes.get(project.id)
        if not isinstance(before, Mapping) or not isinstance(after, Mapping):
            return False
        if int(after.get("unknownCensoredCount", 0) or 0) > int(
            before.get("unknownCensoredCount", 0) or 0
        ):
            return False
        if project.id in protected and _outcome_is_worse_under_censoring(before, after):
            return False
    before_key = _portfolio_outcome_key(data, baseline)
    after_key = _portfolio_outcome_key(data, outcomes)
    for before, after in zip(before_key, after_key, strict=True):
        if abs(after - before) > EPSILON:
            return after < before
    return False


def _outcome_is_worse_under_censoring(
    baseline: Mapping[str, object],
    candidate: Mapping[str, object],
) -> bool:
    baseline_unknown = int(_numeric(baseline.get("unknownCensoredCount")) or 0)
    candidate_unknown = int(_numeric(candidate.get("unknownCensoredCount")) or 0)
    if candidate_unknown > baseline_unknown:
        return True
    baseline_unfinished = int(_numeric(baseline.get("unfinishedCount")) or 0)
    candidate_unfinished = int(_numeric(candidate.get("unfinishedCount")) or 0)
    if candidate_unfinished > baseline_unfinished:
        return True
    baseline_risk = _numeric(baseline.get("delayProbability"))
    candidate_risk = _numeric(candidate.get("delayProbability"))
    if (
        baseline_risk is not None
        and candidate_risk is not None
        and candidate_risk > baseline_risk + EPSILON
    ):
        return True
    if candidate_unknown == baseline_unknown:
        baseline_late = int(_numeric(baseline.get("knownLateCount")) or 0)
        candidate_late = int(_numeric(candidate.get("knownLateCount")) or 0)
        if candidate_late > baseline_late:
            return True
    return False


def _outcome_is_materially_better(
    baseline: Mapping[str, object],
    candidate: Mapping[str, object],
) -> bool:
    baseline_unknown = int(_numeric(baseline.get("unknownCensoredCount")) or 0)
    candidate_unknown = int(_numeric(candidate.get("unknownCensoredCount")) or 0)
    if candidate_unknown < baseline_unknown:
        return True
    baseline_late = int(_numeric(baseline.get("knownLateCount")) or 0)
    candidate_late = int(_numeric(candidate.get("knownLateCount")) or 0)
    if candidate_unknown == baseline_unknown and candidate_late < baseline_late:
        return True
    baseline_delay = _numeric(baseline.get("expectedPositiveDelayDays"))
    candidate_delay = _numeric(candidate.get("expectedPositiveDelayDays"))
    if (
        baseline_delay is not None
        and candidate_delay is not None
        and candidate_delay < baseline_delay - EPSILON
    ):
        return True
    baseline_risk = _numeric(baseline.get("delayProbability"))
    candidate_risk = _numeric(candidate.get("delayProbability"))
    if (
        baseline_risk is not None
        and candidate_risk is not None
        and candidate_risk < baseline_risk - EPSILON
    ):
        return True
    baseline_p50 = baseline.get("finishP50")
    candidate_p50 = candidate.get("finishP50")
    if isinstance(baseline_p50, str) and isinstance(candidate_p50, str):
        return _datetime(candidate_p50, "finishP50") < _datetime(baseline_p50, "finishP50")
    return False


def _candidate_target_outcome(
    data: _Input, candidate: Mapping[str, object]
) -> Mapping[str, object]:
    outcomes = candidate.get("outcomesByProject", {})
    if not isinstance(outcomes, Mapping):
        return {}
    outcome = outcomes.get(data.target_project_id, {})
    return outcome if isinstance(outcome, Mapping) else {}


def _candidate_outcome_signature(candidate: Mapping[str, object]) -> tuple[object, ...]:
    outcomes = candidate.get("outcomesByProject", {})
    if not isinstance(outcomes, Mapping):
        return (candidate.get("id"),)
    signature: list[object] = []
    for project_id in sorted(str(key) for key in outcomes):
        outcome = outcomes.get(project_id)
        if isinstance(outcome, Mapping):
            signature.extend(
                (
                    project_id,
                    outcome.get("finishP50"),
                    outcome.get("delayProbability"),
                    outcome.get("expectedPositiveDelayDays"),
                    outcome.get("unfinishedCount"),
                )
            )
    return tuple(signature)


def _prepare_action(
    data: _Input,
    action: _CandidateAction,
) -> tuple[_PreparedAction, list[str]]:
    worker_by_id = {worker.id: worker for worker in data.workers}
    project_ids = {project.id for project in data.projects}
    task_by_id = {task.id: task for task in data.tasks}
    reasons: list[str] = list(action.validation_errors)
    prepared_transfers: list[_PreparedTransfer] = []
    intervals_by_worker: dict[str, list[_Interval]] = defaultdict(list)
    for transfer in action.transfers:
        historical = ("transfer", transfer) in action.implemented_signatures
        if transfer.worker_id not in worker_by_id:
            reasons.append(f"unknown_transfer_worker:{transfer.worker_id}")
            continue
        worker = worker_by_id[transfer.worker_id]
        if not worker.active:
            reasons.append(f"inactive_transfer_worker:{transfer.worker_id}")
        if not worker.can_transfer:
            reasons.append(f"worker_cannot_transfer:{transfer.worker_id}")
        if transfer.from_project_id not in project_ids or transfer.to_project_id not in project_ids:
            reasons.append(f"unknown_transfer_project:{transfer.worker_id}")
        if transfer.from_project_id == transfer.to_project_id:
            reasons.append(f"transfer_projects_match:{transfer.worker_id}")
        if not _worker_has_project(worker, transfer.from_project_id):
            reasons.append(f"worker_not_in_donor_project:{transfer.worker_id}")
        if transfer.ends_at <= transfer.starts_at:
            reasons.append(f"transfer_window_invalid:{transfer.worker_id}")
            continue
        if not historical and (
            transfer.starts_at < data.as_of or transfer.ends_at > data.horizon_end
        ):
            reasons.append(f"transfer_outside_simulation_horizon:{transfer.worker_id}")
            continue
        effective_start = max(transfer.starts_at, data.as_of) if historical else transfer.starts_at
        effective_end = min(transfer.ends_at, data.horizon_end) if historical else transfer.ends_at
        if effective_end <= effective_start:
            if historical:
                continue
            reasons.append(f"transfer_outside_simulation_horizon:{transfer.worker_id}")
            continue
        friction = (
            transfer.outbound_travel_hours,
            transfer.return_travel_hours,
            transfer.setup_hours,
        )
        if any(value is None for value in friction):
            reasons.append(f"transfer_friction_unknown:{transfer.worker_id}")
            continue
        outbound, returning, setup = (float(value) for value in friction if value is not None)
        if outbound < 0 or returning < 0 or setup < 0:
            reasons.append(f"transfer_friction_negative:{transfer.worker_id}")
            continue
        lead_hours = outbound + setup
        productive_start = transfer.starts_at + timedelta(hours=lead_hours)
        productive_end = transfer.ends_at - timedelta(hours=returning)
        if historical:
            productive_start = max(productive_start, data.as_of)
            productive_end = min(productive_end, data.horizon_end)
        if productive_start >= productive_end:
            if not historical:
                reasons.append(f"transfer_has_no_useful_window:{transfer.worker_id}")
            continue
        validation_interval = _Interval(effective_start, effective_end)
        if not _calendar_covers(
            worker,
            validation_interval.start,
            validation_interval.end,
            action.overtime,
        ):
            reasons.append(f"transfer_outside_worker_calendar:{transfer.worker_id}")
        transfer_interval = validation_interval
        if _has_worker_reservation(data.reservations, worker.id, transfer_interval):
            reasons.append(f"transfer_overlaps_reservation:{worker.id}")
        for task_id in transfer.eligible_target_task_ids:
            target_task = task_by_id.get(task_id)
            if target_task is None:
                reasons.append(f"unknown_transfer_target_task:{task_id}")
            elif target_task.project_id != transfer.to_project_id:
                reasons.append(f"transfer_target_task_wrong_project:{task_id}")
        intervals_by_worker[worker.id].append(transfer_interval)
        prepared_transfers.append(
            _PreparedTransfer(
                spec=transfer,
                productive_start=productive_start,
                productive_end=productive_end,
                non_productive_hours=(
                    _remaining_transfer_friction(transfer, data.as_of, data.horizon_end)
                    if historical
                    else lead_hours + returning
                ),
            )
        )
    for worker_id, intervals in intervals_by_worker.items():
        for left_index, left in enumerate(intervals):
            for right in intervals[left_index + 1 :]:
                if _overlaps(left, right):
                    reasons.append(f"overlapping_transfers:{worker_id}")

    overtime_by_worker: dict[str, float] = defaultdict(float)
    for overtime in action.overtime:
        historical = ("overtime", overtime) in action.implemented_signatures
        worker = worker_by_id.get(overtime.worker_id)
        if worker is None:
            reasons.append(f"unknown_overtime_worker:{overtime.worker_id}")
            continue
        if overtime.ends_at <= overtime.starts_at:
            reasons.append(f"overtime_window_invalid:{overtime.worker_id}")
            continue
        effective_start = max(overtime.starts_at, data.as_of) if historical else overtime.starts_at
        effective_end = min(overtime.ends_at, data.horizon_end) if historical else overtime.ends_at
        if effective_end <= effective_start:
            if historical:
                continue
            reasons.append(f"overtime_outside_simulation_horizon:{overtime.worker_id}")
            continue
        hours = (effective_end - effective_start).total_seconds() / 3600.0
        if not _interval_contained_by_any(
            _Interval(effective_start, effective_end), worker.permitted_overtime_slots
        ):
            reasons.append(f"overtime_not_permitted:{overtime.worker_id}")
        overtime_by_worker[worker.id] += hours
        if (
            worker.max_overtime_hours is not None
            and overtime_by_worker[worker.id] > worker.max_overtime_hours + EPSILON
        ):
            reasons.append(f"overtime_limit_exceeded:{worker.id}")
        if _has_worker_reservation(
            data.reservations, worker.id, _Interval(effective_start, effective_end)
        ):
            reasons.append(f"overtime_overlaps_reservation:{worker.id}")

    for overtime in action.overtime:
        historical = ("overtime", overtime) in action.implemented_signatures
        overtime_interval = _Interval(
            max(overtime.starts_at, data.as_of) if historical else overtime.starts_at,
            min(overtime.ends_at, data.horizon_end) if historical else overtime.ends_at,
        )
        if overtime_interval.end <= overtime_interval.start:
            continue
        if any(
            transfer.spec.worker_id == overtime.worker_id
            and _overlaps(
                overtime_interval,
                _Interval(transfer.spec.starts_at, transfer.spec.ends_at),
            )
            for transfer in prepared_transfers
        ):
            reasons.append(f"overtime_overlaps_transfer:{overtime.worker_id}")

    date_shift_by_task: dict[str, _DateShiftSpec] = {}
    for date_shift in action.date_shifts:
        historical = ("date_shift", date_shift) in action.implemented_signatures
        if date_shift.task_id not in task_by_id:
            reasons.append(f"unknown_date_shift_task:{date_shift.task_id}")
            continue
        if not historical and (
            date_shift.starts_at < data.as_of or date_shift.ends_at > data.horizon_end
        ):
            reasons.append(f"date_shift_outside_simulation_horizon:{date_shift.task_id}")
        if date_shift.ends_at <= date_shift.starts_at:
            reasons.append(f"date_shift_window_invalid:{date_shift.task_id}")
        previous = date_shift_by_task.get(date_shift.task_id)
        if previous is not None and previous != date_shift:
            reasons.append(f"multiple_date_shifts:{date_shift.task_id}")
        date_shift_by_task[date_shift.task_id] = date_shift

    material_by_task: dict[str, _MaterialSpec] = {}
    for material in action.materials:
        if material.task_id not in task_by_id:
            reasons.append(f"unknown_material_task:{material.task_id}")
            continue
        previous = material_by_task.get(material.task_id)
        if previous is not None and previous != material:
            reasons.append(f"multiple_material_responses:{material.task_id}")
        material_by_task[material.task_id] = material
        if (
            ("material", material) not in action.implemented_signatures
            and material.available_at is not None
            and (material.available_at < data.as_of or material.available_at > data.horizon_end)
        ):
            reasons.append(f"material_outside_simulation_horizon:{material.task_id}")

    weather_keys: set[tuple[str | None, str | None]] = set()
    for weather in action.weather:
        historical = ("weather", weather) in action.implemented_signatures
        if weather.task_id is not None and weather.task_id not in task_by_id:
            reasons.append(f"unknown_weather_task:{weather.task_id}")
        if weather.project_id is not None and weather.project_id not in project_ids:
            reasons.append(f"unknown_weather_project:{weather.project_id}")
        if weather.ends_at <= weather.starts_at:
            reasons.append("weather_window_invalid")
        key = (weather.task_id, weather.project_id)
        if key in weather_keys:
            reasons.append(f"multiple_weather_responses:{weather.task_id or weather.project_id}")
        weather_keys.add(key)
        if not historical and (
            weather.starts_at < data.as_of or weather.ends_at > data.horizon_end
        ):
            reasons.append(
                f"weather_outside_simulation_horizon:{weather.task_id or weather.project_id}"
            )

    for project_id in action.project_order:
        if project_id not in project_ids:
            reasons.append(f"unknown_priority_project:{project_id}")
    for task_id in action.task_order:
        if task_id not in task_by_id:
            reasons.append(f"unknown_priority_task:{task_id}")
    prepared = _PreparedAction(
        action=action,
        transfers=tuple(prepared_transfers),
        overtime=action.overtime,
        date_shifts=action.date_shifts,
        materials=action.materials,
        weather=action.weather,
    )
    return prepared, _dedupe_strings(reasons)


def _worker_slot_capacity(
    data: _Input,
    worker: _Worker,
    task: _Task,
    slot_start: datetime,
    slot_end: datetime,
    prepared: _PreparedAction,
) -> tuple[float, bool, str | None]:
    if not worker.active:
        return 0.0, False, None
    if _has_worker_reservation(data.reservations, worker.id, _Interval(slot_start, slot_end)):
        return 0.0, False, None
    transfer = _active_transfer_for_worker(prepared, worker.id, slot_start, slot_end)
    if transfer is not None:
        overlap = _overlap_hours(
            _Interval(slot_start, slot_end),
            _Interval(transfer.productive_start, transfer.productive_end),
        )
        if overlap <= EPSILON:
            return 0.0, False, transfer.spec.worker_id
        if task.project_id != transfer.spec.to_project_id:
            return 0.0, False, transfer.spec.worker_id
        if (
            transfer.spec.eligible_target_task_ids
            and task.id not in transfer.spec.eligible_target_task_ids
        ):
            return 0.0, False, transfer.spec.worker_id
        return min(1.0, overlap), False, transfer.spec.worker_id

    normal = _normal_calendar_capacity(worker, slot_start, slot_end)
    overtime = _overtime_calendar_capacity(worker, prepared.overtime, slot_start, slot_end)
    if normal <= EPSILON and overtime <= EPSILON:
        return 0.0, False, None
    if normal >= overtime:
        return normal, False, None
    return overtime, True, None


def _worker_can_do_task(
    worker: _Worker,
    task: _Task,
    data: _Input,
    prepared: _PreparedAction,
    slot_start: datetime,
    slot_end: datetime,
) -> bool:
    if (
        task.required_specialty_id is not None
        and task.required_specialty_id not in worker.specialties
    ):
        return False
    transfer = _active_transfer_for_worker(prepared, worker.id, slot_start, slot_end)
    if transfer is not None:
        if task.project_id != transfer.spec.to_project_id:
            return False
        if (
            transfer.spec.eligible_target_task_ids
            and task.id not in transfer.spec.eligible_target_task_ids
        ):
            return False
        return True
    if task.assigned_worker_ids:
        # An effective assignment is the confirmed cross-project relation.  It
        # takes precedence over the worker's home site; absent that relation,
        # home/project membership remains the baseline eligibility rule.
        return worker.id in task.assigned_worker_ids
    return _worker_has_project(worker, task.project_id)


def _worker_has_project(worker: _Worker, project_id: str) -> bool:
    if worker.home_project_id is None and not worker.project_ids:
        return True
    return project_id == worker.home_project_id or project_id in worker.project_ids


def _effective_earliest_start(task: _Task, prepared: _PreparedAction) -> datetime:
    shifts = [item for item in prepared.date_shifts if item.task_id == task.id]
    if shifts:
        # An explicit date change is a planned-date overlay.  It may bring a
        # task forward, but never before its confirmed earliest readiness.
        return max(task.earliest_start_at, max(item.starts_at for item in shifts))
    if task.planned_start_at is not None:
        return max(task.earliest_start_at, task.planned_start_at)
    return task.earliest_start_at


def _effective_material_ready_at(
    task: _Task,
    prepared: _PreparedAction,
) -> datetime | None:
    for item in prepared.materials:
        if item.task_id == task.id and item.confirmed and item.available_at is not None:
            return item.available_at
    return task.material_ready_at


def _task_readiness(
    task: _Task,
    task_finishes: Mapping[str, datetime | None],
    task_by_id: Mapping[str, _Task],
    slot_start: datetime,
    earliest_start_at: datetime | None = None,
    material_ready_at: datetime | None = None,
) -> str:
    if slot_start < (earliest_start_at or task.earliest_start_at):
        return "earliest"
    effective_material_ready_at = (
        task.material_ready_at if material_ready_at is None else material_ready_at
    )
    if effective_material_ready_at is not None and slot_start < effective_material_ready_at:
        return "material"
    for predecessor_id in task.predecessor_ids:
        predecessor = task_by_id[predecessor_id]
        if predecessor.status == "canceled":
            return "canceled_predecessor"
        predecessor_finish = task_finishes.get(predecessor_id)
        if predecessor_finish is None or predecessor_finish > slot_start:
            return "predecessor"
    return "ready"


def _task_sort_key(data: _Input, task: _Task, action: _CandidateAction) -> tuple[object, ...]:
    explicit_task_index = (
        action.task_order.index(task.id) if task.id in action.task_order else len(action.task_order)
    )
    explicit_project_index = (
        action.project_order.index(task.project_id)
        if task.project_id in action.project_order
        else len(action.project_order)
    )
    project = next(project for project in data.projects if project.id == task.project_id)
    # Larger declared priority wins; IDs provide the stable final tie-breaker.
    return (
        explicit_task_index,
        explicit_project_index,
        -task.priority,
        -project.priority,
        task.project_id,
        task.id,
    )


def _workability_mode(task: JsonObject, profile: object) -> str | None:
    """Resolve the optional task weather class without inferring one."""

    mode_value = task.get(
        "workabilityMode",
        task.get("workability_mode", task.get("weatherMode", task.get("weather_mode"))),
    )
    if isinstance(mode_value, str) and mode_value.strip():
        return mode_value.strip().lower()
    if task.get("indoor") is True:
        return "indoor"
    if task.get("outdoor") is True:
        return "outdoor"
    weather_sensitive = task.get("weatherSensitive", task.get("weather_sensitive"))
    if weather_sensitive is False:
        return "indoor"
    if weather_sensitive is True:
        return "outdoor"
    if isinstance(profile, Mapping):
        profile_mode = profile.get("mode", profile.get("weatherMode"))
        if isinstance(profile_mode, str) and profile_mode.strip():
            return profile_mode.strip().lower()
    return None


def _task_workability(
    data: _Input,
    task: _Task,
    slot_start: datetime,
    prepared: _PreparedAction | None = None,
) -> float:
    project = next(project for project in data.projects if project.id == task.project_id)
    if task.workability is not None:
        value = _workability_value(
            task.workability,
            slot_start,
            project.timezone,
            task.workability_mode,
        )
        # An explicitly supplied task profile with no value for this slot is
        # an out-of-coverage limitation, not permission to assume sunshine.
        result = 1.0 if value is None and task.workability_mode == "indoor" else (value or 0.0)
    elif task.workability_mode == "indoor":
        # Indoor work does not inherit an outdoor project/weather stop gate.
        result = 1.0
    else:
        value = _workability_value(
            project.workability,
            slot_start,
            project.timezone,
            task.workability_mode,
        )
        if value is None:
            result = 1.0 if project.workability is None else 0.0
        else:
            result = value

    if prepared is None:
        return result
    for weather in prepared.weather:
        if not weather.confirmed or weather.capacity is None:
            continue
        if weather.task_id not in (None, task.id):
            continue
        if weather.project_id not in (None, task.project_id):
            continue
        if _overlaps(
            _Interval(slot_start, slot_start + HOUR),
            _Interval(weather.starts_at, weather.ends_at),
        ):
            result = min(result, weather.capacity)
    return result


def _workability_value(
    profile: object,
    slot_start: datetime,
    local_timezone: ZoneInfo,
    mode: str | None = None,
) -> float | None:
    if profile is None:
        return None
    if isinstance(profile, (int, float)) and not isinstance(profile, bool):
        return _bounded_workability(float(profile))
    if not isinstance(profile, Mapping):
        return None
    if mode is not None and mode in profile:
        return _workability_value(profile[mode], slot_start, local_timezone)
    for nested_key in (
        "capacityBySlot",
        "capacity_by_slot",
        "workabilityBySlot",
        "workability_by_slot",
        "capacityByDate",
        "capacity_by_date",
        "workabilityByDate",
        "workability_by_date",
        "values",
    ):
        nested = profile.get(nested_key)
        if isinstance(nested, Mapping):
            value = _workability_value(nested, slot_start, local_timezone, mode)
            if value is not None:
                return value
    local_slot = slot_start.astimezone(local_timezone)
    candidates = (
        local_slot.isoformat(),
        slot_start.isoformat(),
        _format_datetime(slot_start),
        local_slot.date().isoformat(),
        slot_start.astimezone(UTC).date().isoformat(),
        slot_start.strftime("%Y-%m-%dT%H:00:00Z"),
    )
    for key in candidates:
        if key in profile:
            return _bounded_workability(_number(profile[key], "workability"))
    for key in ("capacity", "workability", "value"):
        if key in profile:
            return _bounded_workability(_number(profile[key], f"workability.{key}"))
    stop_dates = profile.get("stopWorkDates", profile.get("stop_work_dates"))
    if isinstance(stop_dates, Sequence) and not isinstance(stop_dates, (str, bytes, bytearray)):
        if local_slot.date().isoformat() in {str(item) for item in stop_dates}:
            return 0.0
    if bool(profile.get("stopWork", profile.get("stop_work", False))):
        return 0.0
    return None


def _bounded_workability(value: float) -> float:
    if not 0.0 <= value <= 1.0:
        raise SimulationInputError("workability must be between 0 and 1")
    return value


@lru_cache(maxsize=65_536)
def _normal_calendar_capacity(worker: _Worker, slot_start: datetime, slot_end: datetime) -> float:
    local_slot = slot_start.astimezone(worker.timezone)
    local_date = local_slot.date()
    shift_date = local_date
    local_time = local_slot.time().replace(tzinfo=None)
    if worker.shift_start > worker.shift_end and local_time < worker.shift_end:
        shift_date -= timedelta(days=1)
    if shift_date.weekday() not in worker.working_weekdays:
        return 0.0
    shift = _worker_shift_interval(worker, shift_date)
    return min(1.0, _overlap_hours(_Interval(slot_start, slot_end), shift))


@lru_cache(maxsize=8_192)
def _worker_shift_interval(worker: _Worker, local_date: date) -> _Interval:
    """Return one worker shift in UTC, including a shift crossing midnight."""

    shift_start = datetime.combine(local_date, worker.shift_start, worker.timezone).astimezone(UTC)
    shift_end = datetime.combine(local_date, worker.shift_end, worker.timezone).astimezone(UTC)
    if shift_end <= shift_start:
        shift_end += timedelta(days=1)
    return _Interval(shift_start, shift_end)


def _overtime_calendar_capacity(
    worker: _Worker,
    overtime: Sequence[_OvertimeSpec],
    slot_start: datetime,
    slot_end: datetime,
) -> float:
    capacity = 0.0
    for item in overtime:
        if item.worker_id == worker.id:
            capacity = max(
                capacity,
                _overlap_hours(
                    _Interval(slot_start, slot_end), _Interval(item.starts_at, item.ends_at)
                ),
            )
    return min(1.0, capacity)


def _calendar_covers(
    worker: _Worker,
    starts_at: datetime,
    ends_at: datetime,
    action_overtime: Sequence[_OvertimeSpec],
) -> bool:
    if ends_at <= starts_at:
        return False
    cursor = starts_at
    while cursor < ends_at:
        next_cursor = min(ends_at, cursor + HOUR)
        normal = _normal_calendar_capacity(worker, cursor, next_cursor)
        overtime = _overtime_calendar_capacity(worker, action_overtime, cursor, next_cursor)
        required_hours = (next_cursor - cursor).total_seconds() / 3600.0
        if max(normal, overtime) + EPSILON < required_hours:
            return False
        cursor = next_cursor
    return True


def _active_transfer_for_worker(
    prepared: _PreparedAction,
    worker_id: str,
    slot_start: datetime,
    slot_end: datetime,
) -> _PreparedTransfer | None:
    for transfer in prepared.transfers:
        if transfer.spec.worker_id != worker_id:
            continue
        if _overlaps(
            _Interval(slot_start, slot_end),
            _Interval(transfer.spec.starts_at, transfer.spec.ends_at),
        ):
            return transfer
    return None


def _remaining_transfer_friction(
    transfer: _TransferSpec,
    as_of: datetime,
    horizon_end: datetime,
) -> float:
    outbound = float(transfer.outbound_travel_hours or 0.0)
    returning = float(transfer.return_travel_hours or 0.0)
    setup = float(transfer.setup_hours or 0.0)
    lead_end = transfer.starts_at + timedelta(hours=outbound + setup)
    return_start = transfer.ends_at - timedelta(hours=returning)
    future = _Interval(as_of, horizon_end)
    return _overlap_hours(future, _Interval(transfer.starts_at, lead_end)) + _overlap_hours(
        future, _Interval(return_start, transfer.ends_at)
    )


def _has_worker_reservation(
    reservations: Sequence[_Reservation],
    worker_id: str,
    interval: _Interval,
) -> bool:
    return any(
        reservation.worker_id == worker_id and _overlaps(reservation.interval, interval)
        for reservation in reservations
    )


def _task_reserved(
    reservations: Sequence[_Reservation],
    task: _Task,
    interval: _Interval,
) -> bool:
    return any(
        _overlaps(reservation.interval, interval)
        and (
            reservation.task_id == task.id
            or (reservation.task_id is None and reservation.project_id == task.project_id)
        )
        for reservation in reservations
    )


def _collect_diagnostics(
    data: _Input,
    schedule: _ScheduleResult,
    scenario_id: str = "baseline",
) -> list[dict[str, object]]:
    """Resolve scheduler observations into stable, entity-linked explanations."""

    samples = schedule.samples or (schedule,)
    sample_count = len(samples)
    frequencies: dict[tuple[str, str | None, str | None, str | None], int] = defaultdict(int)
    for sample in samples:
        for key in sample.diagnostics:
            frequencies[key] += 1
        for task_id in sample.unfinished_tasks:
            task = next(item for item in data.tasks if item.id == task_id)
            frequencies[("unfinished_at_horizon", task.project_id, task.id, None)] += 1

    task_by_id = {task.id: task for task in data.tasks}
    project_by_id = {project.id: project for project in data.projects}
    diagnostics: list[dict[str, object]] = []
    for code, project_id, task_id, subject_id in sorted(frequencies):
        task = task_by_id.get(task_id) if task_id is not None else None
        project = project_by_id.get(project_id) if project_id is not None else None
        label, detail, severity = _diagnostic_copy(
            code,
            project_id=project_id,
            task_id=task_id,
            subject_id=subject_id,
            task=task,
            project=project,
            data=data,
        )
        item: dict[str, object] = {
            "code": code,
            "label": label,
            "message": detail,
            "detail": detail,
            "severity": severity,
            "scenarioIds": [scenario_id],
            "observedInSamples": frequencies[(code, project_id, task_id, subject_id)],
            "sampleCount": sample_count,
        }
        entity_refs: list[dict[str, str]] = []
        if project_id is not None:
            item["projectId"] = project_id
            entity_refs.append({"type": "project", "id": project_id})
        if task_id is not None:
            item["taskId"] = task_id
            entity_refs.append({"type": "task", "id": task_id})
        if subject_id is not None:
            if code == "unavailable_specialty_capacity":
                item["specialtyId"] = subject_id
                entity_refs.append({"type": "specialty", "id": subject_id})
            else:
                item["workerId"] = subject_id
                entity_refs.append({"type": "worker", "id": subject_id})
        item["entityRefs"] = entity_refs
        if task is not None:
            item["affectedInterval"] = {
                "startsAt": _format_datetime(task.earliest_start_at),
                "endsAt": _format_datetime(data.horizon_end),
            }
            blockers: list[dict[str, str]] = []
            if code in {"waiting_on_confirmed_predecessor", "canceled_predecessor_requires_waiver"}:
                blockers = [
                    {"type": "task", "id": predecessor_id}
                    for predecessor_id in task.predecessor_ids
                ]
            elif code == "waiting_on_confirmed_material_gate":
                blockers = [{"type": "material_gate", "id": task.id}]
            if blockers:
                item["blockerRefs"] = blockers
        if code == "unfinished_at_horizon":
            item["horizon"] = _format_datetime(data.horizon_end)
            item["missingReason"] = (
                "beyond_simulated_horizon_before_target"
                if project is not None and data.horizon_end < project.target_finish_at
                else "beyond_simulated_horizon_after_target_known_late"
            )
        diagnostics.append(item)
    return diagnostics


def _diagnostic_copy(
    code: str,
    *,
    project_id: str | None,
    task_id: str | None,
    subject_id: str | None,
    task: _Task | None,
    project: _Project | None,
    data: _Input,
) -> tuple[str, str, str]:
    subject = subject_id or "the required resource"
    task_label = task_id or "the task"
    project_label = project_id or "the project"
    if code == "task_blocked_by_hard_reservation":
        return (
            "Hard reservation blocks task",
            f"{task_label} in {project_label} overlaps a confirmed reservation.",
            "blocking",
        )
    if code == "waiting_on_confirmed_predecessor":
        predecessor_text = ", ".join(task.predecessor_ids) if task is not None else "a predecessor"
        return (
            "Waiting on confirmed prerequisite",
            f"{task_label} cannot start until {predecessor_text} finishes.",
            "info",
        )
    if code == "canceled_predecessor_requires_waiver":
        return (
            "Canceled prerequisite needs review",
            f"{task_label} has a canceled prerequisite and was not released by the model.",
            "blocking",
        )
    if code == "waiting_on_confirmed_material_gate":
        ready_at = (
            _format_datetime(task.material_ready_at)
            if task is not None and task.material_ready_at is not None
            else "an unconfirmed time"
        )
        return (
            "Waiting on confirmed material gate",
            f"{task_label} cannot start before the confirmed material-ready time ({ready_at}).",
            "warning",
        )
    if code == "unavailable_specialty_capacity":
        return (
            "Required specialty unavailable",
            f"{task_label} has no active worker with specialty {subject} and usable capacity.",
            "blocking",
        )
    if code == "shared_worker_contention":
        return (
            "Shared worker contention",
            (
                f"Worker {subject} is eligible for multiple ready tasks in the same slot; "
                "stable task order selected one."
            ),
            "warning",
        )
    if code == "crew_below_minimum":
        return (
            "Minimum crew unavailable",
            f"{task_label} could not assemble its required minimum crew in a ready slot.",
            "blocking",
        )
    if code == "task_stop_work_gate":
        return (
            "Workability stop gate",
            f"{task_label} was not scheduled because its confirmed workability was zero.",
            "blocking",
        )
    if code == "task_exposed_to_reduced_workability":
        return (
            "Reduced workability exposure",
            (
                f"{task_label} was scheduled under a reduced workability value from the "
                "planning input."
            ),
            "warning",
        )
    if code == "transfer_donor_capacity_reserved":
        return (
            "Transfer reserves donor capacity",
            f"Worker {subject} was unavailable to donor work during the transfer window.",
            "warning",
        )
    if code == "unfinished_at_horizon":
        return (
            "Beyond simulated horizon",
            f"{task_label} did not finish by the simulated horizon; its final finish is censored.",
            "blocking",
        )
    return (code.replace("_", " ").capitalize(), f"{code} was observed for {task_label}.", "info")


def _dedupe_diagnostics(items: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for item in sorted(
        items, key=lambda value: tuple((str(k), str(v)) for k, v in sorted(value.items()))
    ):
        key = tuple(sorted((str(k), str(v)) for k, v in item.items()))
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _dedupe_strings(values: Iterable[str]) -> list[str]:
    """Return stable, first-seen strings for warnings and rejection reasons."""

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _guardrail_rejections(
    data: _Input,
    schedule: _ScheduleResult,
    baseline_outcomes: Mapping[str, dict[str, object]],
    prepared: _PreparedAction,
) -> tuple[str, ...]:
    reasons: list[str] = []
    max_overtime = _optional_number(
        data.guardrails.get("maxOvertimeHours", data.guardrails.get("max_overtime_hours")),
        "guardrails.maxOvertimeHours",
    )
    if max_overtime is not None and schedule.overtime_hours > max_overtime + EPSILON:
        reasons.append("maximum_overtime_guardrail_exceeded")
    max_donor_delay = _optional_number(
        data.guardrails.get(
            "maxAddedDonorDelayDays", data.guardrails.get("max_added_donor_delay_days")
        ),
        "guardrails.maxAddedDonorDelayDays",
    )
    if max_donor_delay is not None:
        for transfer in prepared.transfers:
            donor = transfer.spec.from_project_id
            delta = _delay_delta(baseline_outcomes.get(donor), schedule, data, donor)
            if delta is not None and delta > max_donor_delay + EPSILON:
                reasons.append(f"maximum_donor_delay_guardrail_exceeded:{donor}")
    protected_value = data.guardrails.get(
        "protectedProjectIds", data.guardrails.get("protected_project_ids", [])
    )
    protected = set(_string_tuple(protected_value, "guardrails.protectedProjectIds"))
    protected.update(
        project.id for project in data.projects if bool(project.id in protected or False)
    )
    for project_id in sorted(protected):
        baseline = baseline_outcomes.get(project_id)
        delta = _delay_delta(baseline, schedule, data, project_id)
        if delta is not None and delta > EPSILON:
            reasons.append(f"protected_project_delay:{project_id}")
    return tuple(_dedupe_strings(reasons))


def _guardrail_details(
    data: _Input,
    schedule: _ScheduleResult | None,
    baseline_outcomes: Mapping[str, dict[str, object]],
    outcomes: Mapping[str, dict[str, object]],
    prepared: _PreparedAction,
) -> dict[str, object]:
    checks: dict[str, object] = {"feasible": schedule is not None}
    if schedule is None:
        return checks
    checks["overtimeHours"] = round(schedule.overtime_hours, 6)
    checks["transferNonProductiveHours"] = round(
        sum(item.non_productive_hours for item in prepared.transfers), 6
    )
    checks["donorImpactsChecked"] = sorted(
        {transfer.spec.from_project_id for transfer in prepared.transfers}
    )
    return checks


def _delay_delta(
    baseline: Mapping[str, object] | None,
    schedule: _ScheduleResult,
    data: _Input,
    project_id: str,
) -> float | None:
    if baseline is None:
        return None
    baseline_delay = baseline.get("expectedPositiveDelayDays")
    if not isinstance(baseline_delay, (int, float)):
        return None
    outcome = _summarize_outcomes(data, schedule).get(project_id)
    if not isinstance(outcome, Mapping):
        return None
    candidate_delay = outcome.get("expectedPositiveDelayDays")
    if not isinstance(candidate_delay, (int, float)):
        return None
    return float(candidate_delay) - float(baseline_delay)


def _outcome_delta(
    baseline: Mapping[str, object] | None,
    candidate: Mapping[str, object],
) -> dict[str, object]:
    if baseline is None:
        return {}
    delta: dict[str, object] = {}
    for metric_field, output_name in (
        ("delayProbability", "delayProbabilityChange"),
        ("expectedPositiveDelayDays", "expectedPositiveDelayChangeDays"),
    ):
        left = baseline.get(metric_field)
        right = candidate.get(metric_field)
        delta[output_name] = (
            round(float(right) - float(left), 6)
            if isinstance(left, (int, float)) and isinstance(right, (int, float))
            else None
        )
    baseline_finish = baseline.get("finishP50")
    candidate_finish = candidate.get("finishP50")
    if isinstance(baseline_finish, str) and isinstance(candidate_finish, str):
        before = _datetime(baseline_finish, "baseline finishP50")
        after = _datetime(candidate_finish, "candidate finishP50")
        target_value = baseline.get("targetFinishAt")
        if isinstance(target_value, str):
            target = _datetime(target_value, "targetFinishAt")
            delta["targetDateBufferDaysBefore"] = round(
                (target - before).total_seconds() / 86400.0, 6
            )
            delta["targetDateBufferDaysAfter"] = round(
                (target - after).total_seconds() / 86400.0, 6
            )
        else:
            delta["targetDateBufferDaysBefore"] = None
            delta["targetDateBufferDaysAfter"] = None
        delta["finishP50Before"] = baseline_finish
        delta["finishP50After"] = candidate_finish
        delta["finishP50ShiftDays"] = round((after - before).total_seconds() / 86400.0, 6)
    else:
        delta["targetDateBufferDaysBefore"] = None
        delta["targetDateBufferDaysAfter"] = None
        delta["finishP50Before"] = None
        delta["finishP50After"] = None
        delta["finishP50ShiftDays"] = None
    limitations: list[str] = []
    if baseline.get("unknownCensoredCount", 0) or candidate.get("unknownCensoredCount", 0):
        limitations.append("finish_censored_before_target")
    delta["knownMissingCommitments"] = limitations
    delta["unknownCensoredCountBefore"] = baseline.get("unknownCensoredCount", 0)
    delta["unknownCensoredCountAfter"] = candidate.get("unknownCensoredCount", 0)
    delta["knownLateCountBefore"] = baseline.get("knownLateCount", 0)
    delta["knownLateCountAfter"] = candidate.get("knownLateCount", 0)
    delta["completionCoverageChange"] = (
        round(
            float(baseline.get("unfinishedCount", 0)) - float(candidate.get("unfinishedCount", 0)),
            6,
        )
        if isinstance(baseline.get("unfinishedCount"), (int, float))
        and isinstance(candidate.get("unfinishedCount"), (int, float))
        else None
    )
    return delta


def _extract_explicit_candidates(payload: dict, data: _Input) -> list[_CandidateAction] | None:
    root = _object(payload, "payload")
    value: object = None
    for key in (
        "candidateActions",
        "candidate_actions",
        "strategies",
        "recoveryCandidates",
        "recovery_candidates",
    ):
        if key in root:
            value = root[key]
            break
    if value is None:
        return None
    objects = _objects(value, "candidate actions")
    candidates: list[_CandidateAction] = []
    used_candidate_ids: set[str] = set()
    for index, item in enumerate(objects):
        group = item
        action_values = group.get("actions")
        if action_values is None and group.get("action") is not None:
            action_values = [group.get("action")]
        if action_values is None:
            if "projectOrder" in group or "project_order" in group or "taskOrder" in group:
                action_values = [
                    {
                        "type": "priority",
                        "projectOrder": group.get("projectOrder", group.get("project_order", [])),
                        "taskOrder": group.get("taskOrder", group.get("task_order", [])),
                    }
                ]
            else:
                action_values = [group]
        action_objects = _objects(action_values, f"candidate[{index}].actions")
        project_order = _string_tuple(
            group.get("projectOrder", group.get("project_order", [])),
            f"candidate[{index}].projectOrder",
        )
        task_order = _string_tuple(
            group.get("taskOrder", group.get("task_order", [])),
            f"candidate[{index}].taskOrder",
        )
        action, local_reasons = _parse_action_objects(
            action_objects[:MAX_ACTIONS_PER_BUNDLE],
            data,
            candidate_index=index,
            initial_project_order=project_order,
            initial_task_order=task_order,
        )
        if len(action_objects) > MAX_ACTIONS_PER_BUNDLE:
            local_reasons.append(f"action_bundle_exceeds_max_actions:{len(action_objects)}")
        candidate_id = _string(
            group.get("id", group.get("strategyId", f"candidate-{index + 1}")),
            f"candidate[{index}].id",
        )
        original_candidate_id = candidate_id
        duplicate_index = 2
        while candidate_id in used_candidate_ids:
            candidate_id = f"{original_candidate_id}__duplicate-{duplicate_index}"
            duplicate_index += 1
        used_candidate_ids.add(candidate_id)
        if local_reasons:
            # Preserve the candidate as an invalid action so the output is
            # explicit instead of silently dropping a manager-supplied option.
            candidate = _CandidateAction(
                id=f"{candidate_id}__invalid__{abs(_stable_seed(data.seed, candidate_id))}",
                transfers=action.transfers,
                overtime=action.overtime,
                date_shifts=action.date_shifts,
                materials=action.materials,
                weather=action.weather,
                project_order=action.project_order,
                task_order=action.task_order,
                priority_action_type=action.priority_action_type,
                source="explicit",
                validation_errors=tuple(local_reasons),
            )
            candidates.append(candidate)
        else:
            candidates.append(
                _CandidateAction(
                    id=candidate_id,
                    transfers=action.transfers,
                    overtime=action.overtime,
                    date_shifts=action.date_shifts,
                    materials=action.materials,
                    weather=action.weather,
                    project_order=action.project_order,
                    task_order=action.task_order,
                    priority_action_type=action.priority_action_type,
                    source="explicit",
                )
            )
    return candidates


def _parse_implemented_action(payload: dict, data: _Input) -> _CandidateAction:
    """Parse confirmed execution steps that are now part of the baseline.

    Implemented actions use the same discriminated action objects as candidate
    bundles.  They are intentionally not generated or inferred here: the
    caller must pass only confirmed execution steps.  The action list may be
    longer than the three-action proposal bound because it represents already
    applied history, not a new recommendation.
    """

    root = _object(payload, "payload")
    value = root.get("implementedActions", root.get("implemented_actions", []))
    if value in (None, ""):
        return _CandidateAction(id="implemented", source="implemented")
    action_objects = _objects(value, "implementedActions")
    action, reasons = _parse_action_objects(
        action_objects,
        data,
        candidate_index="implemented",
        initial_project_order=(),
        initial_task_order=(),
    )
    if reasons:
        raise SimulationInputError(
            "implementedActions contains unsupported or invalid actions: " + "; ".join(reasons)
        )
    return replace(action, id="implemented", source="implemented")


def _parse_action_objects(
    action_objects: Sequence[JsonObject],
    data: _Input,
    *,
    candidate_index: int | str,
    initial_project_order: tuple[str, ...],
    initial_task_order: tuple[str, ...],
) -> tuple[_CandidateAction, list[str]]:
    """Parse a bounded or confirmed list of typed action objects."""

    transfers: list[_TransferSpec] = []
    overtime: list[_OvertimeSpec] = []
    date_shifts: list[_DateShiftSpec] = []
    materials: list[_MaterialSpec] = []
    weather: list[_WeatherSpec] = []
    project_order = initial_project_order
    task_order = initial_task_order
    priority_action_type = "priority"
    local_reasons: list[str] = []
    for action_index, raw_action in enumerate(action_objects):
        kind = str(raw_action.get("type", raw_action.get("kind", "transfer"))).lower()
        try:
            if kind in {"transfer", "worker_transfer", "move_worker"}:
                transfers.append(
                    _parse_action_transfer(raw_action, data, candidate_index, action_index)
                )
            elif kind in {"overtime", "extra_time"}:
                overtime.append(
                    _parse_action_overtime(raw_action, data, candidate_index, action_index)
                )
            elif kind in {"priority", "reprioritize", "resequence"}:
                project_order = _string_tuple(
                    raw_action.get("projectOrder", raw_action.get("project_order", project_order)),
                    f"candidate[{candidate_index}].projectOrder",
                )
                task_order = _string_tuple(
                    raw_action.get("taskOrder", raw_action.get("task_order", task_order)),
                    f"candidate[{candidate_index}].taskOrder",
                )
                if kind == "resequence":
                    priority_action_type = "resequence"
            elif kind in {
                "date_shift",
                "task_date_shift",
                "planned_date_shift",
                "planned_dates",
                "schedule_change",
            }:
                date_shifts.append(
                    _parse_action_date_shift(raw_action, data, candidate_index, action_index)
                )
            elif kind in {"material", "material_ready", "material_response"}:
                materials.append(
                    _parse_action_material(raw_action, data, candidate_index, action_index)
                )
            elif kind in {"weather", "weather_response", "weather_reschedule"}:
                weather.append(
                    _parse_action_weather(raw_action, data, candidate_index, action_index)
                )
            else:
                local_reasons.append(f"unsupported_action_type:{kind}")
        except SimulationInputError as exc:
            local_reasons.append(str(exc))
    return (
        _CandidateAction(
            id="parsed",
            transfers=tuple(dict.fromkeys(transfers)),
            overtime=tuple(dict.fromkeys(overtime)),
            date_shifts=tuple(dict.fromkeys(date_shifts)),
            materials=tuple(dict.fromkeys(materials)),
            weather=tuple(dict.fromkeys(weather)),
            project_order=project_order,
            task_order=task_order,
            priority_action_type=priority_action_type,
        ),
        local_reasons,
    )


def _parse_action_transfer(
    action: JsonObject,
    data: _Input,
    candidate_index: int,
    action_index: int,
) -> _TransferSpec:
    nested = action.get("transfer")
    source = (
        _object(nested, f"candidate[{candidate_index}].actions[{action_index}].transfer")
        if nested is not None
        else action
    )
    worker_id = _string(
        _required(source, "workerId", "worker_id"),
        f"candidate[{candidate_index}].actions[{action_index}].workerId",
    )
    from_project = _string(
        _required(source, "fromProjectId", "from_project_id"),
        f"candidate[{candidate_index}].actions[{action_index}].fromProjectId",
    )
    to_project = _string(
        _required(source, "toProjectId", "to_project_id"),
        f"candidate[{candidate_index}].actions[{action_index}].toProjectId",
    )
    starts_value = source.get("startsAt", source.get("starts_at"))
    ends_value = source.get("endsAt", source.get("ends_at"))
    if starts_value in (None, "") or ends_value in (None, ""):
        raise SimulationInputError(
            f"candidate[{candidate_index}].actions[{action_index}] transfer window is required"
        )
    assumption = _find_transfer_assumption(data, from_project, to_project, worker_id)
    return _TransferSpec(
        worker_id=worker_id,
        from_project_id=from_project,
        to_project_id=to_project,
        starts_at=_datetime(starts_value, "candidate transfer startsAt"),
        ends_at=_datetime(ends_value, "candidate transfer endsAt"),
        eligible_target_task_ids=_string_tuple(
            source.get("eligibleTargetTaskIds", source.get("eligible_target_task_ids", [])),
            "candidate transfer eligibleTargetTaskIds",
        ),
        outbound_travel_hours=_action_number_or_assumption(
            source, "outboundTravelHours", "outbound_travel_hours", assumption
        ),
        return_travel_hours=_action_number_or_assumption(
            source, "returnTravelHours", "return_travel_hours", assumption
        ),
        setup_hours=_action_number_or_assumption(source, "setupHours", "setup_hours", assumption),
    )


def _parse_action_overtime(
    action: JsonObject,
    data: _Input,
    candidate_index: int,
    action_index: int,
) -> _OvertimeSpec:
    worker_id = _string(
        _required(action, "workerId", "worker_id"),
        f"candidate[{candidate_index}].actions[{action_index}].workerId",
    )
    starts_value = action.get("startsAt", action.get("starts_at"))
    ends_value = action.get("endsAt", action.get("ends_at"))
    if starts_value in (None, ""):
        date_value = action.get("date")
        start_time = action.get("startTime", action.get("start_time"))
        if date_value is None or start_time is None:
            raise SimulationInputError(f"candidate[{candidate_index}] overtime start is required")
        worker = next(
            (item for item in data.workers if item.id == worker_id),
            None,
        )
        try:
            starts_value = datetime.combine(
                date.fromisoformat(str(date_value)),
                _clock_time(start_time, "candidate overtime startTime"),
                worker.timezone if worker is not None else UTC,
            )
        except ValueError as exc:
            raise SimulationInputError("candidate overtime date must be YYYY-MM-DD") from exc
    starts_at = _datetime(starts_value, "candidate overtime startsAt")
    if ends_value in (None, ""):
        hours = _number(action.get("hours"), "candidate overtime hours")
        if hours <= 0:
            raise SimulationInputError("candidate overtime hours must be positive")
        ends_at = starts_at + timedelta(hours=hours)
    else:
        ends_at = _datetime(ends_value, "candidate overtime endsAt")
    return _OvertimeSpec(worker_id, starts_at, ends_at)


def _parse_action_date_shift(
    action: JsonObject,
    data: _Input,
    candidate_index: int | str,
    action_index: int,
) -> _DateShiftSpec:
    nested = action.get("dateShift", action.get("plannedDates"))
    source = (
        _object(nested, f"candidate[{candidate_index}].actions[{action_index}].dateShift")
        if nested is not None
        else action
    )
    task_id = _string(
        _required(source, "taskId", "task_id"),
        f"candidate[{candidate_index}].actions[{action_index}].taskId",
    )
    starts_value = source.get("startsAt", source.get("starts_at"))
    ends_value = source.get("endsAt", source.get("ends_at"))
    if starts_value in (None, "") or ends_value in (None, ""):
        raise SimulationInputError(
            f"candidate[{candidate_index}].actions[{action_index}] date shift requires "
            "startsAt and endsAt"
        )
    starts_at = _datetime(starts_value, "candidate date shift startsAt")
    ends_at = _datetime(ends_value, "candidate date shift endsAt")
    if ends_at <= starts_at:
        raise SimulationInputError(
            f"candidate[{candidate_index}].actions[{action_index}] date shift window is invalid"
        )
    return _DateShiftSpec(task_id, starts_at, ends_at, "date_shift")


def _parse_action_material(
    action: JsonObject,
    data: _Input,
    candidate_index: int | str,
    action_index: int,
) -> _MaterialSpec:
    nested = action.get("material", action.get("materialResponse"))
    source = (
        _object(nested, f"candidate[{candidate_index}].actions[{action_index}].material")
        if nested is not None
        else action
    )
    task_id = _string(
        _required(source, "taskId", "task_id"),
        f"candidate[{candidate_index}].actions[{action_index}].taskId",
    )
    available_value = source.get(
        "availableAt",
        source.get("available_at", source.get("materialReadyAt", source.get("material_ready_at"))),
    )
    available_at = (
        None
        if available_value in (None, "")
        else _datetime(available_value, "candidate material availableAt")
    )
    material_value = source.get("materialId", source.get("material_id"))
    material_id = (
        None if material_value in (None, "") else _string(material_value, "candidate materialId")
    )
    confirmed = bool(source.get("confirmed", source.get("isConfirmed", False)))
    return _MaterialSpec(task_id, available_at, material_id, confirmed, "material")


def _parse_action_weather(
    action: JsonObject,
    data: _Input,
    candidate_index: int | str,
    action_index: int,
) -> _WeatherSpec:
    nested = action.get("weather", action.get("weatherResponse"))
    source = (
        _object(nested, f"candidate[{candidate_index}].actions[{action_index}].weather")
        if nested is not None
        else action
    )
    task_value = source.get("taskId", source.get("task_id"))
    project_value = source.get("projectId", source.get("project_id"))
    task_id = None if task_value in (None, "") else _string(task_value, "candidate weather taskId")
    project_id = (
        None
        if project_value in (None, "")
        else _string(project_value, "candidate weather projectId")
    )
    if task_id is None and project_id is None:
        raise SimulationInputError(
            f"candidate[{candidate_index}].actions[{action_index}] weather requires "
            "taskId or projectId"
        )
    starts_value = source.get("startsAt", source.get("starts_at"))
    ends_value = source.get("endsAt", source.get("ends_at"))
    if starts_value in (None, "") or ends_value in (None, ""):
        raise SimulationInputError(
            f"candidate[{candidate_index}].actions[{action_index}] weather requires "
            "startsAt and endsAt"
        )
    starts_at = _datetime(starts_value, "candidate weather startsAt")
    ends_at = _datetime(ends_value, "candidate weather endsAt")
    if ends_at <= starts_at:
        raise SimulationInputError(
            f"candidate[{candidate_index}].actions[{action_index}] weather window is invalid"
        )
    capacity_value = source.get("capacity", source.get("workability"))
    capacity = (
        None
        if capacity_value in (None, "")
        else _number(capacity_value, "candidate weather capacity")
    )
    if capacity is not None and not 0.0 <= capacity <= 1.0:
        raise SimulationInputError("candidate weather capacity must be between 0 and 1")
    rule_value = source.get("ruleId", source.get("rule_id", source.get("contextId")))
    rule_id = None if rule_value in (None, "") else _string(rule_value, "candidate weather ruleId")
    confirmed = bool(source.get("confirmed", source.get("isConfirmed", False)))
    return _WeatherSpec(
        task_id, project_id, starts_at, ends_at, capacity, rule_id, confirmed, "weather"
    )


def _action_number_or_assumption(
    action: JsonObject,
    camel: str,
    snake: str,
    assumption: _TransferSpec | None,
) -> float | None:
    value = action.get(camel, action.get(snake))
    if value is not None:
        return _number(value, f"candidate transfer {camel}")
    if assumption is None:
        return None
    return {
        "outboundTravelHours": assumption.outbound_travel_hours,
        "returnTravelHours": assumption.return_travel_hours,
        "setupHours": assumption.setup_hours,
    }[camel]


def _find_transfer_assumption(
    data: _Input,
    from_project: str,
    to_project: str,
    worker_id: str,
) -> _TransferSpec | None:
    for assumption in data.transfer_assumptions:
        if (
            assumption.from_project_id == from_project
            and assumption.to_project_id == to_project
            and (assumption.worker_id in {"*", worker_id})
        ):
            return assumption
    return None


def _generate_candidates(
    data: _Input,
    implemented_action: _CandidateAction | None = None,
) -> list[_CandidateAction]:
    """Generate deterministic compatible bundles from a bounded action set."""

    if data.decision_scope == "portfolio":
        # Round-robin bounded searches keep one project's actions from consuming
        # the whole budget. Every candidate still simulates the entire portfolio.
        searches = [
            _generate_candidates(
                replace(data, target_project_id=project.id, decision_scope="project"),
                implemented_action,
            )
            for project in sorted(
                data.projects, key=lambda project: (-project.priority, project.id)
            )
        ]
        candidates: list[_CandidateAction] = []
        seen: set[tuple[object, ...]] = set()
        for index in range(max((len(search) for search in searches), default=0)):
            for search in searches:
                if index >= len(search):
                    continue
                candidate = search[index]
                signature = _action_signature(candidate)
                if signature in seen:
                    continue
                seen.add(signature)
                # Per-project generators reuse descriptive IDs for different
                # priority orders. Keep portfolio IDs unambiguous for Apply.
                candidates.append(replace(candidate, id=f"portfolio-{len(candidates) + 1}"))
                if len(candidates) >= MAX_CANDIDATES:
                    return candidates
        return candidates

    families = [
        _generate_date_shift_candidates(data, implemented_action),
        _generate_transfer_candidates(data),
        _generate_overtime_candidates(data),
        _generate_priority_candidates(data),
        _generate_material_candidates(data, implemented_action),
        _generate_weather_candidates(data, implemented_action),
    ]
    elementary = (
        candidate
        for group in zip_longest(*families)
        for candidate in group
        if candidate is not None
    )
    bounded_elementary: list[_CandidateAction] = []
    seen_elementary: set[tuple[object, ...]] = set()
    for candidate in elementary:
        signature = _action_signature(candidate)
        if signature in seen_elementary:
            continue
        seen_elementary.add(signature)
        bounded_elementary.append(candidate)
        if len(bounded_elementary) >= MAX_ELEMENTARY_ACTIONS:
            break

    result: list[_CandidateAction] = []
    seen_bundles: set[tuple[object, ...]] = set()
    # Give combined plans space before the bounded budget is exhausted by singles.
    searches = [
        (
            parts
            for parts in combinations(bounded_elementary, size)
            if _compatible_action_parts(parts)
        )
        for size in range(1, MAX_ACTIONS_PER_BUNDLE + 1)
    ]
    for group in zip_longest(*searches):
        for parts in group:
            if parts is None:
                continue
            bundle = _bundle_actions(parts)
            signature = _action_signature(bundle)
            if signature in seen_bundles:
                continue
            seen_bundles.add(signature)
            result.append(bundle)
            if len(result) >= MAX_CANDIDATES:
                return result
    return result


def _generate_date_shift_candidates(
    data: _Input,
    implemented_action: _CandidateAction | None,
) -> list[_CandidateAction]:
    """Suggest explicit planned-date moves for ready work and input constraints.

    A date-shift candidate is emitted only when the source task supplies an
    explicit planned start and end.  That keeps the action mappable to the
    verified Timecue task-date PATCH without inventing a duration.  Material
    readiness and explicit workability profiles remain scheduling constraints.
    Future tasks may also move earlier; feasibility and portfolio improvement
    are evaluated by the same simulator before publishing any recommendation.
    """

    represented_task_ids = {
        item.task_id for item in (implemented_action.date_shifts if implemented_action else ())
    }
    candidates: list[_CandidateAction] = []
    for task in sorted(data.tasks, key=lambda item: item.id):
        if task.status in {"done", "canceled"}:
            continue
        if task.planned_start_at is None or task.planned_end_at is None:
            continue
        if task.planned_end_at <= data.as_of or task.planned_start_at >= data.horizon_end:
            continue
        project = next(project for project in data.projects if project.id == task.project_id)
        has_workability_profile = task.workability is not None or project.workability is not None
        material_trigger = (
            task.material_ready_at is not None and task.material_ready_at > task.planned_start_at
        )
        planned_workability = _task_workability(data, task, task.planned_start_at)
        weather_trigger = has_workability_profile and planned_workability < 1.0 - EPSILON
        if not material_trigger and not weather_trigger and task.planned_start_at <= data.as_of:
            continue

        earliest_candidate = max(data.as_of, task.material_ready_at or data.as_of)
        candidate_start = _first_schedulable_start(data, task, earliest_candidate)
        if candidate_start is None or candidate_start == task.planned_start_at:
            continue
        duration = task.planned_end_at - task.planned_start_at
        candidate_end = candidate_start + duration
        if candidate_end > data.horizon_end:
            continue
        shift = _DateShiftSpec(task.id, candidate_start, candidate_end)
        if task.id in represented_task_ids:
            continue
        candidates.append(
            _CandidateAction(
                id=f"date-shift-{task.id}-{candidate_start.strftime('%Y%m%dT%H%M%S')}",
                date_shifts=(shift,),
            )
        )
    return candidates


def _generate_material_candidates(
    data: _Input,
    implemented_action: _CandidateAction | None,
) -> list[_CandidateAction]:
    """Expose conditional manual material-expediting requests.

    The generated action deliberately has no asserted availability timestamp
    and is unconfirmed.  It therefore cannot improve a forecast; it gives the
    owner an explicit manual step whose success must be confirmed separately.
    """

    represented_task_ids = {
        item.task_id for item in (implemented_action.materials if implemented_action else ())
    }
    candidates: list[_CandidateAction] = []
    for task in sorted(data.tasks, key=lambda item: item.id):
        if task.status in {"done", "canceled"}:
            continue
        if task.id in represented_task_ids:
            continue
        if task.material_ready_at is None or task.material_ready_at <= data.as_of:
            continue
        candidates.append(
            _CandidateAction(
                id=f"material-expedite-{task.id}",
                materials=(
                    _MaterialSpec(
                        task_id=task.id,
                        available_at=None,
                        material_id=None,
                        confirmed=False,
                    ),
                ),
            )
        )
    return candidates


def _generate_weather_candidates(
    data: _Input,
    implemented_action: _CandidateAction | None,
) -> list[_CandidateAction]:
    """Expose conditional manual weather-window confirmation requests."""

    represented = {
        (item.task_id, item.project_id)
        for item in (implemented_action.weather if implemented_action else ())
    }
    candidates: list[_CandidateAction] = []
    for task in sorted(data.tasks, key=lambda item: item.id):
        if task.status in {"done", "canceled"}:
            continue
        if (task.id, task.project_id) in represented:
            continue
        if task.planned_start_at is None or task.planned_end_at is None:
            continue
        project = next(project for project in data.projects if project.id == task.project_id)
        if task.workability is None and project.workability is None:
            continue
        if _task_workability(data, task, task.planned_start_at) >= 1.0 - EPSILON:
            continue
        candidate_start = _first_schedulable_start(data, task, data.as_of)
        if candidate_start is None or candidate_start == task.planned_start_at:
            continue
        duration = task.planned_end_at - task.planned_start_at
        candidate_end = candidate_start + duration
        if candidate_end > data.horizon_end:
            continue
        candidates.append(
            _CandidateAction(
                id=f"weather-window-{task.id}-{candidate_start.strftime('%Y%m%dT%H%M%S')}",
                weather=(
                    _WeatherSpec(
                        task_id=task.id,
                        project_id=task.project_id,
                        starts_at=candidate_start,
                        ends_at=candidate_end,
                        capacity=None,
                        rule_id=None,
                        confirmed=False,
                    ),
                ),
            )
        )
    return candidates


def _first_schedulable_start(
    data: _Input,
    task: _Task,
    not_before: datetime,
) -> datetime | None:
    """Find the first covered worker slot with positive explicit workability."""

    for slot_start in _slot_starts(data.as_of, data.horizon_end):
        if slot_start < max(data.as_of, not_before):
            continue
        slot_end = slot_start + HOUR
        if _task_workability(data, task, slot_start) <= EPSILON:
            continue
        if _task_reserved(data.reservations, task, _Interval(slot_start, slot_end)):
            continue
        if any(
            worker.active
            and _baseline_worker_can_do_task(worker, task)
            and _normal_calendar_capacity(worker, slot_start, slot_end) > EPSILON
            for worker in data.workers
        ):
            return slot_start
    return None


def _compatible_action_parts(parts: Sequence[_CandidateAction]) -> bool:
    """Reject bundles that compete for the same interval or overlay."""

    priority_signatures = {
        (part.project_order, part.task_order)
        for part in parts
        if part.project_order or part.task_order
    }
    if len(priority_signatures) > 1:
        return False
    transfers = [transfer for part in parts for transfer in part.transfers]
    overtime = [item for part in parts for item in part.overtime]
    for left, right in combinations(transfers, 2):
        if left.worker_id == right.worker_id and _overlaps(
            _Interval(left.starts_at, left.ends_at),
            _Interval(right.starts_at, right.ends_at),
        ):
            return False
    for transfer in transfers:
        transfer_interval = _Interval(transfer.starts_at, transfer.ends_at)
        if any(
            transfer.worker_id == item.worker_id
            and _overlaps(transfer_interval, _Interval(item.starts_at, item.ends_at))
            for item in overtime
        ):
            return False
    for left, right in combinations(overtime, 2):
        if left.worker_id == right.worker_id and _overlaps(
            _Interval(left.starts_at, left.ends_at),
            _Interval(right.starts_at, right.ends_at),
        ):
            return False
    date_shifts = [shift for part in parts for shift in part.date_shifts]
    for left, right in combinations(date_shifts, 2):
        if left.task_id == right.task_id and left != right:
            return False
    materials = [material for part in parts for material in part.materials]
    for left, right in combinations(materials, 2):
        if left.task_id == right.task_id and left != right:
            return False
    weather = [item for part in parts for item in part.weather]
    for left, right in combinations(weather, 2):
        if (left.task_id, left.project_id) == (right.task_id, right.project_id) and _overlaps(
            _Interval(left.starts_at, left.ends_at),
            _Interval(right.starts_at, right.ends_at),
        ):
            return False
    return True


def _bundle_actions(parts: Sequence[_CandidateAction]) -> _CandidateAction:
    """Combine compatible elementary actions into one stable bundle."""

    first = parts[0]
    transfers = tuple(item for part in parts for item in part.transfers)
    overtime = tuple(item for part in parts for item in part.overtime)
    date_shifts = tuple(item for part in parts for item in part.date_shifts)
    materials = tuple(item for part in parts for item in part.materials)
    weather = tuple(item for part in parts for item in part.weather)
    project_order = next((part.project_order for part in parts if part.project_order), ())
    task_order = next((part.task_order for part in parts if part.task_order), ())
    ids = "+".join(part.id for part in parts)
    return _CandidateAction(
        id=first.id if len(parts) == 1 else f"bundle-{ids}",
        transfers=transfers,
        overtime=overtime,
        date_shifts=date_shifts,
        materials=materials,
        weather=weather,
        project_order=project_order,
        task_order=task_order,
        priority_action_type=next(
            (part.priority_action_type for part in parts if part.project_order or part.task_order),
            "priority",
        ),
        source="generated" if len(parts) == 1 else "generated_bundle",
    )


def _generate_overtime_candidates(data: _Input) -> list[_CandidateAction]:
    """Enumerate one permitted overtime window at a time, within worker caps."""

    target_tasks = [
        task
        for task in data.tasks
        if task.project_id == data.target_project_id and task.status not in {"done", "canceled"}
    ]
    candidates: list[_CandidateAction] = []
    for worker in sorted(data.workers, key=lambda item: item.id):
        if not worker.active or not worker.permitted_overtime_slots:
            continue
        if not any(_baseline_worker_can_do_task(worker, task) for task in target_tasks):
            continue
        for slot in worker.permitted_overtime_slots:
            starts_at = max(slot.start, data.as_of)
            ends_at = min(slot.end, data.horizon_end)
            if ends_at <= starts_at:
                continue
            allowed_hours = (ends_at - starts_at).total_seconds() / 3600.0
            if worker.max_overtime_hours is not None:
                allowed_hours = min(allowed_hours, worker.max_overtime_hours)
            if allowed_hours <= EPSILON:
                continue
            bounded_end = starts_at + timedelta(hours=allowed_hours)
            overtime = _OvertimeSpec(worker.id, starts_at, bounded_end)
            candidates.append(
                _CandidateAction(
                    id=f"overtime-{worker.id}-{starts_at.strftime('%Y%m%dT%H%M%S')}",
                    overtime=(overtime,),
                )
            )
    return candidates


def _generate_priority_candidates(data: _Input) -> list[_CandidateAction]:
    """Generate small dispatch-order alternatives only where contention exists."""

    active_tasks = [task for task in data.tasks if task.status not in {"done", "canceled"}]
    eligible_by_worker = {
        worker.id: [task for task in active_tasks if _baseline_worker_can_do_task(worker, task)]
        for worker in data.workers
        if worker.active
    }
    cross_project_contention = any(
        len({task.project_id for task in tasks}) > 1 for tasks in eligible_by_worker.values()
    )
    same_project_contention = any(
        len([task for task in tasks if task.project_id == data.target_project_id]) > 1
        for tasks in eligible_by_worker.values()
    )
    if not cross_project_contention and not same_project_contention:
        return []

    projects = {project.id: project for project in data.projects}
    default_project_order = tuple(
        project.id for project in sorted(data.projects, key=lambda item: (-item.priority, item.id))
    )
    target_first_order = (
        data.target_project_id,
        *tuple(
            project_id
            for project_id in default_project_order
            if project_id != data.target_project_id
        ),
    )
    candidates: list[_CandidateAction] = []
    if cross_project_contention and target_first_order != default_project_order:
        candidates.append(
            _CandidateAction(
                id="priority-target-project-first",
                project_order=target_first_order,
            )
        )

    target_tasks = [task for task in active_tasks if task.project_id == data.target_project_id]
    if same_project_contention and len(target_tasks) > 1:
        task_order = tuple(
            task.id for task in sorted(target_tasks, key=lambda item: (-item.priority, item.id))
        )
        baseline_order = tuple(
            task.id
            for task in sorted(
                target_tasks,
                key=lambda item: (-item.priority, -projects[item.project_id].priority, item.id),
            )
        )
        if task_order != baseline_order:
            candidates.append(
                _CandidateAction(
                    id="priority-target-task-first",
                    task_order=task_order,
                )
            )
    return candidates


def _baseline_worker_can_do_task(worker: _Worker, task: _Task) -> bool:
    if (
        task.required_specialty_id is not None
        and task.required_specialty_id not in worker.specialties
    ):
        return False
    if task.assigned_worker_ids:
        return worker.id in task.assigned_worker_ids
    return _worker_has_project(worker, task.project_id)


def _generate_transfer_candidates(data: _Input) -> list[_CandidateAction]:
    task_by_project: dict[str, list[_Task]] = defaultdict(list)
    for task in data.tasks:
        if task.status not in {"done", "canceled"}:
            task_by_project[task.project_id].append(task)
    candidates: list[_CandidateAction] = []
    for assumption in sorted(
        data.transfer_assumptions,
        key=lambda item: (item.from_project_id, item.to_project_id, item.worker_id),
    ):
        if assumption.to_project_id != data.target_project_id:
            continue
        if None in (
            assumption.outbound_travel_hours,
            assumption.return_travel_hours,
            assumption.setup_hours,
        ):
            continue
        candidate_workers = [
            worker
            for worker in data.workers
            if worker.active
            and worker.can_transfer
            and (assumption.worker_id == "*" or assumption.worker_id == worker.id)
            and _worker_has_project(worker, assumption.from_project_id)
        ]
        for worker in sorted(candidate_workers, key=lambda item: item.id):
            eligible_tasks = [
                task
                for task in sorted(
                    task_by_project.get(assumption.to_project_id, []), key=lambda item: item.id
                )
                if task.required_specialty_id is None
                or task.required_specialty_id in worker.specialties
            ]
            if not eligible_tasks:
                continue
            windows = _plausible_transfer_windows(data, worker, assumption)
            for window in windows[:MAX_TRANSFER_WINDOWS_PER_DONOR]:
                spec = replace(
                    assumption,
                    worker_id=worker.id,
                    starts_at=window.start,
                    ends_at=window.end,
                    eligible_target_task_ids=tuple(task.id for task in eligible_tasks),
                )
                action_id = (
                    f"transfer-{worker.id}-{assumption.from_project_id}-{assumption.to_project_id}-"
                    f"{window.start.strftime('%Y%m%dT%H%M%S')}"
                )
                candidates.append(_CandidateAction(id=action_id, transfers=(spec,)))
    return candidates


def _plausible_transfer_windows(
    data: _Input,
    worker: _Worker,
    assumption: _TransferSpec,
) -> list[_Interval]:
    if (
        assumption.starts_at < data.horizon_end
        and assumption.ends_at > data.as_of
        and (assumption.starts_at != data.as_of or assumption.ends_at != data.horizon_end)
    ):
        return [
            _Interval(
                max(assumption.starts_at, data.as_of), min(assumption.ends_at, data.horizon_end)
            )
        ]
    windows: list[_Interval] = []
    local_start = data.as_of.astimezone(worker.timezone).date()
    local_end = data.horizon_end.astimezone(worker.timezone).date()
    current = local_start
    while current <= local_end and len(windows) < MAX_TRANSFER_WINDOWS_PER_DONOR:
        if current.weekday() in worker.working_weekdays:
            shift = _worker_shift_interval(worker, current)
            start = shift.start
            end = shift.end
            start = max(start, data.as_of)
            end = min(end, data.horizon_end)
            if end > start:
                windows.append(_Interval(start, end))
        current += timedelta(days=1)
    return windows


def _transfer_output(transfer: _PreparedTransfer) -> dict[str, object]:
    spec = transfer.spec
    return {
        "type": "transfer",
        "workerId": spec.worker_id,
        "fromProjectId": spec.from_project_id,
        "toProjectId": spec.to_project_id,
        "startsAt": _format_datetime(spec.starts_at),
        "endsAt": _format_datetime(spec.ends_at),
        "productiveStartsAt": _format_datetime(transfer.productive_start),
        "productiveEndsAt": _format_datetime(transfer.productive_end),
        "eligibleTargetTaskIds": list(spec.eligible_target_task_ids),
        "outboundTravelHours": spec.outbound_travel_hours,
        "returnTravelHours": spec.return_travel_hours,
        "setupHours": spec.setup_hours,
        "nonProductiveHours": round(transfer.non_productive_hours, 6),
        "capability": "manual",
        "modelledEffect": "temporary_worker_transfer",
    }


def _overtime_output(overtime: _OvertimeSpec) -> dict[str, object]:
    return {
        "type": "overtime",
        "workerId": overtime.worker_id,
        "startsAt": _format_datetime(overtime.starts_at),
        "endsAt": _format_datetime(overtime.ends_at),
        "hours": round((overtime.ends_at - overtime.starts_at).total_seconds() / 3600.0, 6),
        "capability": "manual",
        "modelledEffect": "permitted_overtime_slot",
    }


def _date_shift_output(date_shift: _DateShiftSpec) -> dict[str, object]:
    """Return the verified Timecue task-date mutation envelope."""

    return {
        "type": date_shift.action_type,
        "taskId": date_shift.task_id,
        "startsAt": _format_datetime(date_shift.starts_at),
        "endsAt": _format_datetime(date_shift.ends_at),
        "capability": "api",
        "modelledEffect": "planned_task_dates",
        "upstream": {
            "method": "PATCH",
            "resource": "task",
            "fields": ["plannedStartAt", "plannedEndAt"],
            "permission": "tasks.write",
        },
    }


def _material_output(material: _MaterialSpec) -> dict[str, object]:
    return {
        "type": material.action_type,
        "taskId": material.task_id,
        "materialId": material.material_id,
        "availableAt": (
            _format_datetime(material.available_at) if material.available_at is not None else None
        ),
        "confirmed": material.confirmed,
        "capability": "manual",
        "modelledEffect": (
            "confirmed_material_gate_override"
            if material.confirmed and material.available_at is not None
            else "manual_confirmation_required"
        ),
    }


def _weather_output(weather: _WeatherSpec) -> dict[str, object]:
    return {
        "type": weather.action_type,
        "taskId": weather.task_id,
        "projectId": weather.project_id,
        "startsAt": _format_datetime(weather.starts_at),
        "endsAt": _format_datetime(weather.ends_at),
        "capacity": weather.capacity,
        "ruleId": weather.rule_id,
        "confirmed": weather.confirmed,
        "capability": "manual",
        "modelledEffect": (
            "confirmed_workability_cap"
            if weather.confirmed and weather.capacity is not None
            else "manual_context_only"
        ),
    }


def _slot_starts(as_of: datetime, horizon_end: datetime) -> list[datetime]:
    starts: list[datetime] = []
    cursor = as_of
    while cursor < horizon_end:
        starts.append(cursor)
        cursor += HOUR
    return starts


def _quantile_datetime(values: Sequence[datetime | None], quantile: float) -> datetime:
    present = sorted(value for value in values if value is not None)
    if not present:
        raise SimulationInputError("cannot calculate a finish quantile without finishes")
    if len(present) == 1:
        return present[0]
    position = (len(present) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return present[lower]
    fraction = position - lower
    return present[lower] + (present[upper] - present[lower]) * fraction


def _stable_seed(seed: int, *parts: str) -> int:
    material = "|".join((str(seed), *parts)).encode("utf-8")
    return int.from_bytes(sha256(material).digest()[:8], "big", signed=False)


def _interval_from_object(
    value: JsonObject,
    label: str,
    local_timezone: tzinfo = UTC,
) -> _Interval:
    starts_value = value.get(
        "startsAt", value.get("starts_at", value.get("startAt", value.get("start_at")))
    )
    ends_value = value.get("endsAt", value.get("ends_at", value.get("endAt", value.get("end_at"))))
    if starts_value in (None, "") or ends_value in (None, ""):
        date_value = value.get("date")
        start_time = value.get("startTime", value.get("start_time"))
        end_time = value.get("endTime", value.get("end_time"))
        if date_value is not None and start_time is not None and end_time is not None:
            try:
                local_date = date.fromisoformat(str(date_value))
                starts_value = datetime.combine(
                    local_date,
                    _clock_time(start_time, f"{label}.startTime"),
                    local_timezone,
                )
                ends_value = datetime.combine(
                    local_date,
                    _clock_time(end_time, f"{label}.endTime"),
                    local_timezone,
                )
            except ValueError as exc:
                raise SimulationInputError(f"{label}.date must be YYYY-MM-DD") from exc
    if starts_value in (None, "") or ends_value in (None, ""):
        raise SimulationInputError(f"{label} requires startsAt and endsAt")
    starts_at = _datetime(starts_value, f"{label}.startsAt")
    ends_at = _datetime(ends_value, f"{label}.endsAt")
    if ends_at <= starts_at:
        raise SimulationInputError(f"{label} must have a positive interval")
    return _Interval(starts_at, ends_at)


def _interval_contained_by_any(interval: _Interval, candidates: Sequence[_Interval]) -> bool:
    return any(item.start <= interval.start and item.end >= interval.end for item in candidates)


def _overlaps(left: _Interval, right: _Interval) -> bool:
    return left.start < right.end and right.start < left.end


def _overlap_hours(left: _Interval, right: _Interval) -> float:
    start = max(left.start, right.start)
    end = min(left.end, right.end)
    return max(0.0, (end - start).total_seconds() / 3600.0)


def _format_project_datetime(value: datetime, project: _Project) -> str:
    return value.astimezone(project.timezone).isoformat(timespec="seconds")


def _format_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, Mapping):
        raise SimulationInputError(f"{label} must be an object")
    return value


def _objects(value: object, label: str) -> list[JsonObject]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise SimulationInputError(f"{label} must be an array")
    result: list[JsonObject] = []
    for index, item in enumerate(value):
        result.append(_object(item, f"{label}[{index}]"))
    return result


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise SimulationInputError(f"{label} must be an array")
    return list(value)


def _required(mapping: JsonObject, *keys: str) -> object:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    raise SimulationInputError(f"missing required field {keys[0]}")


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SimulationInputError(f"{label} must be a non-empty string")
    return value.strip()


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    values = _list(value, label)
    result: list[str] = []
    for item in values:
        result.append(_string(item, label))
    return tuple(dict.fromkeys(result))


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SimulationInputError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise SimulationInputError(f"{label} must be a finite number")
    return result


def _optional_number(value: object, label: str) -> float | None:
    return None if value in (None, "") else _number(value, label)


def _bounded_int(value: object, label: str, minimum: int, maximum: int) -> int:
    number = _number(value, label)
    if not number.is_integer() or number < minimum or number > maximum:
        raise SimulationInputError(f"{label} must be an integer between {minimum} and {maximum}")
    return int(number)


def _datetime(value: object, label: str) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            result = datetime.fromisoformat(text)
        except ValueError as exc:
            raise SimulationInputError(f"{label} must be an ISO 8601 timestamp") from exc
    else:
        raise SimulationInputError(f"{label} must be an ISO 8601 timestamp")
    if result.tzinfo is None or result.utcoffset() is None:
        raise SimulationInputError(f"{label} must include a timezone offset")
    return result.astimezone(UTC)


def _zone(value: object, label: str) -> ZoneInfo:
    if isinstance(value, ZoneInfo):
        return value
    name = _string(value, label)
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise SimulationInputError(f"{label} has unknown timezone {name!r}") from exc


def _clock_time(value: object, label: str) -> time:
    if isinstance(value, time):
        return value.replace(tzinfo=None)
    if not isinstance(value, str):
        raise SimulationInputError(f"{label} must be HH:MM or HH:MM:SS")
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise SimulationInputError(f"{label} must be HH:MM or HH:MM:SS") from exc
    return parsed.replace(tzinfo=None)


def _weekday(value: object, label: str) -> int:
    if isinstance(value, str):
        names = {
            "mon": 0,
            "monday": 0,
            "tue": 1,
            "tuesday": 1,
            "wed": 2,
            "wednesday": 2,
            "thu": 3,
            "thursday": 3,
            "fri": 4,
            "friday": 4,
            "sat": 5,
            "saturday": 5,
            "sun": 6,
            "sunday": 6,
        }
        if value.lower() not in names:
            raise SimulationInputError(f"{label} contains an unknown weekday {value!r}")
        return names[value.lower()]
    number = _number(value, label)
    if not number.is_integer() or not 0 <= number <= 6:
        raise SimulationInputError(f"{label} must contain weekdays 0 through 6")
    return int(number)


__all__ = ["SimulationInputError", "evaluate_portfolio"]
