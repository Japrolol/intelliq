"""Pure presentation adapters for portfolio simulation results.

The numerical engine intentionally returns source IDs and computed metrics.  This
module resolves those IDs against the frozen planning payload for UI consumers,
without changing the underlying outcomes or inventing causal explanations.
Graph nodes and edges use stable typed IDs so the same planning snapshot can be
rendered in 3D, 2D, or an accessible list.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy

type JsonObject = dict[str, object]


_DIAGNOSTIC_LABELS: dict[str, str] = {
    "task_blocked_by_hard_reservation": "Hard reservation blocks task",
    "waiting_on_confirmed_predecessor": "Waiting on confirmed prerequisite",
    "canceled_predecessor_requires_waiver": "Canceled prerequisite needs review",
    "waiting_on_confirmed_material_gate": "Waiting on confirmed material gate",
    "unavailable_specialty_capacity": "Required specialty unavailable",
    "shared_worker_contention": "Shared worker contention",
    "crew_below_minimum": "Minimum crew unavailable",
    "task_stop_work_gate": "Workability stop gate",
    "task_exposed_to_reduced_workability": "Reduced workability exposure",
    "transfer_donor_capacity_reserved": "Transfer reserves donor capacity",
    "unfinished_at_horizon": "Beyond simulated horizon",
}


def enrich_result(result: dict, planning: dict) -> dict:
    """Resolve names and presentation aliases while preserving raw metrics.

    The returned value is a deep copy.  Every original outcome field, action
    field, diagnostic code, and source ID remains available; additional fields
    are intentionally additive for the existing frontend and the v4 DTO seam.
    """

    enriched = deepcopy(result)
    indexes = _planning_indexes(planning)
    project_names = indexes["projectNames"]
    task_names = indexes["taskNames"]
    worker_names = indexes["workerNames"]
    specialty_names = indexes["specialtyNames"]
    target_project_id = _string_value(
        enriched.get("targetProjectId") or planning.get("targetProjectId")
    )

    baseline = enriched.get("baselineByProject")
    if isinstance(baseline, Mapping):
        enriched["baselineByProject"] = {
            str(project_id): _enrich_outcome(outcome, str(project_id), project_names)
            for project_id, outcome in baseline.items()
            if isinstance(outcome, Mapping)
        }

    for collection_key in ("strategies", "candidateEvaluations"):
        collection = enriched.get(collection_key)
        if not isinstance(collection, Sequence) or isinstance(collection, (str, bytes, bytearray)):
            continue
        enriched[collection_key] = [
            _enrich_strategy(
                item,
                target_project_id,
                project_names,
                task_names,
                worker_names,
            )
            for item in collection
            if isinstance(item, Mapping)
        ]

    diagnostics = enriched.get("diagnostics")
    if isinstance(diagnostics, Sequence) and not isinstance(diagnostics, (str, bytes, bytearray)):
        enriched["diagnostics"] = [
            _enrich_diagnostic(
                item,
                project_names,
                task_names,
                worker_names,
                specialty_names,
            )
            for item in diagnostics
            if isinstance(item, Mapping)
        ]

    if target_project_id is not None:
        enriched["targetProjectName"] = project_names.get(target_project_id, target_project_id)
    enriched["presentation"] = {
        "numericSource": "simulation-engine",
        "namesResolvedFrom": "planning",
        "causalAttribution": False,
    }
    return enriched


def build_graph(planning: dict) -> dict:
    """Build the v4 typed graph contract from one planning snapshot.

    Only explicit relationships are emitted: confirmed prerequisites,
    assignments, explicit shared-worker overlap, and named material
    requirements.  Project membership is carried in node data instead of being
    turned into a causal edge.
    """

    indexes = _planning_indexes(planning)
    projects = indexes["projects"]
    tasks = indexes["tasks"]
    workers = indexes["workers"]
    materials = indexes["materials"]
    assignments = indexes["assignments"]

    nodes: list[dict[str, object]] = []
    node_ids: set[str] = set()
    for project_id in sorted(projects):
        project = projects[project_id]
        nodes.append(
            {
                "id": _typed_id("project", project_id),
                "type": "project",
                "label": _display_name(project, project_id),
                "data": {"sourceId": project_id, **project},
            }
        )
        node_ids.add(_typed_id("project", project_id))
    for task_id in sorted(tasks):
        task = tasks[task_id]
        project_id = _string_value(task.get("projectId", task.get("project_id")))
        node = {
            "id": _typed_id("task", task_id),
            "type": "task",
            "label": _display_name(task, task_id),
            "data": {"sourceId": task_id, **task},
        }
        if project_id is not None:
            node["projectId"] = project_id
        nodes.append(node)
        node_ids.add(_typed_id("task", task_id))
    for worker_id in sorted(workers):
        worker = workers[worker_id]
        nodes.append(
            {
                "id": _typed_id("worker", worker_id),
                "type": "worker",
                "label": _display_name(worker, worker_id),
                "data": {"sourceId": worker_id, **worker},
            }
        )
        node_ids.add(_typed_id("worker", worker_id))
    for material_id in sorted(materials):
        material = materials[material_id]
        nodes.append(
            {
                "id": _typed_id("material", material_id),
                "type": "material",
                "label": _display_name(material, material_id),
                "data": {"sourceId": material_id, **material},
            }
        )
        node_ids.add(_typed_id("material", material_id))

    edges: list[dict[str, object]] = []
    edge_ids: set[str] = set()

    def add_edge(edge_id: str, source: str, target: str, edge_type: str) -> None:
        if source not in node_ids or target not in node_ids or edge_id in edge_ids:
            return
        edge_ids.add(edge_id)
        edges.append(
            {
                "id": edge_id,
                "source": source,
                "target": target,
                "type": edge_type,
            }
        )

    for task_id in sorted(tasks):
        task = tasks[task_id]
        for predecessor_id in _string_values(
            task.get("predecessorIds", task.get("predecessor_ids", []))
        ):
            add_edge(
                f"edge:prerequisite:{predecessor_id}:{task_id}",
                _typed_id("task", predecessor_id),
                _typed_id("task", task_id),
                "prerequisite",
            )
        for worker_id in assignments.get(task_id, ()):
            add_edge(
                f"edge:assignment:{worker_id}:{task_id}",
                _typed_id("worker", worker_id),
                _typed_id("task", task_id),
                "assignment",
            )
        for material_id in _task_material_ids(task):
            add_edge(
                f"edge:material_requirement:{material_id}:{task_id}",
                _typed_id("material", material_id),
                _typed_id("task", task_id),
                "material_requirement",
            )

    for dependency in _objects(planning.get("dependencies", [])):
        source_id = _string_value(dependency.get("fromTaskId", dependency.get("predecessorTaskId")))
        target_id = _string_value(dependency.get("toTaskId", dependency.get("taskId")))
        if source_id is not None and target_id is not None:
            add_edge(
                f"edge:prerequisite:{source_id}:{target_id}",
                _typed_id("task", source_id),
                _typed_id("task", target_id),
                "prerequisite",
            )

    worker_tasks: dict[str, list[str]] = {}
    for task_id, worker_ids in assignments.items():
        for worker_id in worker_ids:
            worker_tasks.setdefault(worker_id, []).append(task_id)
    for worker_id, task_ids in sorted(worker_tasks.items()):
        ordered = sorted(set(task_ids))
        for index, source_id in enumerate(ordered):
            for target_id in ordered[index + 1 :]:
                add_edge(
                    f"edge:shared_resource:{worker_id}:{source_id}:{target_id}",
                    _typed_id("task", source_id),
                    _typed_id("task", target_id),
                    "shared_resource",
                )

    return {
        "nodes": nodes,
        "edges": edges,
        "projects": [
            {"id": project_id, "name": _display_name(project, project_id)}
            for project_id, project in sorted(projects.items())
        ],
    }


def _planning_indexes(planning: Mapping[str, object]) -> dict[str, object]:
    projects = {
        _required_id(item, "projects", index): item
        for index, item in enumerate(_objects(planning.get("projects", [])))
    }
    tasks = {
        _required_id(item, "tasks", index): item
        for index, item in enumerate(_objects(planning.get("tasks", [])))
    }
    workers = {
        _required_id(item, "workers", index): item
        for index, item in enumerate(_objects(planning.get("workers", [])))
    }
    materials = {
        _required_id(item, "materials", index): item
        for index, item in enumerate(_objects(planning.get("materials", [])))
    }
    assignments: dict[str, list[str]] = {}
    for task_id, task in tasks.items():
        assignments[task_id] = _string_values(
            task.get(
                "assignedWorkerIds",
                task.get("assigned_worker_ids", task.get("workerIds", task.get("worker_ids", []))),
            )
        )
    for row in _objects(planning.get("assignments", planning.get("effectiveAssignments", []))):
        task_id = _string_value(row.get("taskId", row.get("task_id")))
        if task_id is None:
            continue
        assignments[task_id] = list(
            dict.fromkeys(
                [
                    *assignments.get(task_id, []),
                    *_string_values(row.get("workerIds", row.get("worker_ids", []))),
                ]
            )
        )
    return {
        "projects": projects,
        "tasks": tasks,
        "workers": workers,
        "materials": materials,
        "assignments": assignments,
        "projectNames": {
            project_id: _display_name(project, project_id)
            for project_id, project in projects.items()
        },
        "taskNames": {task_id: _display_name(task, task_id) for task_id, task in tasks.items()},
        "workerNames": {
            worker_id: _display_name(worker, worker_id) for worker_id, worker in workers.items()
        },
        "specialtyNames": _specialty_names(planning),
    }


def _enrich_outcome(
    outcome: Mapping[str, object],
    project_id: str,
    project_names: Mapping[str, str],
) -> dict[str, object]:
    enriched = dict(outcome)
    enriched.setdefault("projectId", project_id)
    enriched["projectName"] = project_names.get(project_id, project_id)
    return enriched


def _enrich_strategy(
    value: Mapping[str, object],
    target_project_id: str | None,
    project_names: Mapping[str, str],
    task_names: Mapping[str, str],
    worker_names: Mapping[str, str],
) -> dict[str, object]:
    strategy = dict(value)
    strategy_id = _string_value(strategy.get("id")) or "candidate"
    role, role_name = _strategy_role(strategy, strategy_id)
    strategy.setdefault("strategy", role)
    strategy.setdefault("strategyName", role_name)
    strategy.setdefault("name", role_name)
    strategy.setdefault("label", role_name)
    strategy.setdefault("strategyRoles", [role])

    outcomes = strategy.get("outcomesByProject")
    if isinstance(outcomes, Mapping):
        enriched_outcomes = {
            str(project_id): _enrich_outcome(outcome, str(project_id), project_names)
            for project_id, outcome in outcomes.items()
            if isinstance(outcome, Mapping)
        }
        strategy["outcomesByProject"] = enriched_outcomes
        if target_project_id is not None and target_project_id in enriched_outcomes:
            strategy["target"] = deepcopy(enriched_outcomes[target_project_id])
        donor_id = _donor_project_id(strategy, target_project_id, enriched_outcomes)
        if donor_id is not None:
            strategy["donor"] = deepcopy(enriched_outcomes[donor_id])
            strategy["donorProjectName"] = project_names.get(donor_id, donor_id)

    actions = strategy.get("actions")
    if isinstance(actions, Sequence) and not isinstance(actions, (str, bytes, bytearray)):
        strategy["actions"] = [
            _enrich_action(action, project_names, task_names, worker_names)
            for action in actions
            if isinstance(action, Mapping)
        ]
    strategy["summary"] = strategy.get("summary") or _strategy_summary(strategy)
    strategy["rationale"] = strategy.get("rationale") or _strategy_rationale(role)
    return strategy


def _enrich_action(
    value: Mapping[str, object],
    project_names: Mapping[str, str],
    task_names: Mapping[str, str],
    worker_names: Mapping[str, str],
) -> dict[str, object]:
    action = dict(value)
    for key, names in (
        ("fromProjectId", project_names),
        ("toProjectId", project_names),
        ("projectId", project_names),
        ("taskId", task_names),
        ("workerId", worker_names),
    ):
        source_id = _string_value(action.get(key))
        if source_id is not None and source_id in names:
            suffix = key[:-2] if key.endswith("Id") else key
            action[f"{suffix}Name"] = names[source_id]
    task_ids = _string_values(action.get("eligibleTargetTaskIds", []))
    if task_ids:
        action["eligibleTargetTaskNames"] = [task_names.get(item, item) for item in task_ids]
    if action.get("type") in {"priority", "resequence"}:
        action["projectOrderNames"] = [
            project_names.get(item, item) for item in _string_values(action.get("projectOrder", []))
        ]
        action["capability"] = action.get("capability", "manual")
        action["modelledEffect"] = action.get("modelledEffect", "dispatch_order_only")
    return action


def _enrich_diagnostic(
    value: Mapping[str, object],
    project_names: Mapping[str, str],
    task_names: Mapping[str, str],
    worker_names: Mapping[str, str],
    specialty_names: Mapping[str, str],
) -> dict[str, object]:
    diagnostic = dict(value)
    code = _string_value(diagnostic.get("code", diagnostic.get("kind"))) or "diagnostic"
    diagnostic.setdefault("kind", code)
    diagnostic.setdefault("label", _DIAGNOSTIC_LABELS.get(code, code.replace("_", " ").title()))
    diagnostic.setdefault("severity", "info")
    project_id = _string_value(diagnostic.get("projectId"))
    task_id = _string_value(diagnostic.get("taskId"))
    worker_id = _string_value(diagnostic.get("workerId"))
    specialty_id = _string_value(diagnostic.get("specialtyId"))
    if project_id is not None:
        diagnostic["projectName"] = project_names.get(project_id, project_id)
    if task_id is not None:
        diagnostic["taskName"] = task_names.get(task_id, task_id)
    if worker_id is not None:
        diagnostic["workerName"] = worker_names.get(worker_id, worker_id)
    if specialty_id is not None:
        diagnostic["specialtyName"] = specialty_names.get(specialty_id, specialty_id)
    diagnostic.setdefault("detail", _diagnostic_detail(diagnostic))
    diagnostic.setdefault("message", diagnostic["detail"])
    diagnostic.setdefault(
        "id",
        ":".join(item for item in (code, project_id, task_id, worker_id, specialty_id) if item),
    )
    return diagnostic


def _diagnostic_detail(diagnostic: Mapping[str, object]) -> str:
    code = _string_value(diagnostic.get("code", diagnostic.get("kind"))) or "diagnostic"
    task = _string_value(diagnostic.get("taskName", diagnostic.get("taskId"))) or "the task"
    project = _string_value(diagnostic.get("projectName", diagnostic.get("projectId")))
    if code == "unfinished_at_horizon":
        return f"{task} did not finish by the simulated horizon; its final finish is censored."
    if code == "waiting_on_confirmed_predecessor":
        return f"{task} is waiting on a confirmed prerequisite."
    if code == "unavailable_specialty_capacity":
        specialty = (
            _string_value(diagnostic.get("specialtyName", diagnostic.get("specialtyId")))
            or "the required specialty"
        )
        return f"{task} has no active worker with {specialty} capacity."
    if project is not None:
        return f"{code.replace('_', ' ')} affects {task} in {project}."
    return f"{code.replace('_', ' ')} affects {task}."


def _strategy_role(strategy: Mapping[str, object], strategy_id: str) -> tuple[str, str]:
    existing = _string_value(strategy.get("strategy"))
    if existing in {"baseline", "fast", "balanced", "safe"}:
        role_labels = {
            "baseline": "Current plan",
            "fast": "Fast",
            "balanced": "Balanced",
            "safe": "Safe",
        }
        return existing, role_labels[existing]
    labels = {_string_value(item) for item in _objects_as_values(strategy.get("labels", []))}
    if strategy_id == "baseline" or "unchangedBaseline" in labels:
        return "baseline", "Current plan"
    for role, label in (
        ("fast", "fastestRecovery"),
        ("balanced", "portfolioBalance"),
        ("safe", "leastDisruption"),
    ):
        if label in labels or role in labels:
            return role, role.title()
    return "option", "Option"


def _strategy_summary(strategy: Mapping[str, object]) -> str:
    actions = strategy.get("actions")
    if (
        not isinstance(actions, Sequence)
        or isinstance(actions, (str, bytes, bytearray))
        or not actions
    ):
        return "Keep the current plan."
    summaries: list[str] = []
    overtime: dict[str, list[float]] = {}
    for action in actions:
        if not isinstance(action, Mapping):
            continue
        kind = _string_value(action.get("type")) or "action"
        if kind == "transfer":
            worker = _string_value(action.get("workerName", action.get("workerId"))) or "a worker"
            source = (
                _string_value(action.get("fromProjectName", action.get("fromProjectId")))
                or "the donor project"
            )
            target = (
                _string_value(action.get("toProjectName", action.get("toProjectId")))
                or "the target project"
            )
            summaries.append(f"Transfer {worker} from {source} to {target}.")
        elif kind == "overtime":
            worker = _string_value(action.get("workerName", action.get("workerId"))) or "a worker"
            hours = action.get("hours")
            if isinstance(hours, (int, float)):
                overtime.setdefault(worker, []).append(float(hours))
        elif kind in {"priority", "resequence"}:
            names = _string_values(action.get("projectOrderNames", []))
            summaries.append(
                f"Prioritize {' → '.join(names)}; task dependencies stay unchanged."
                if names
                else "Change dispatch priority; task dependencies stay unchanged."
            )
        elif kind == "date_shift":
            task = _string_value(action.get("taskName", action.get("taskId"))) or "the task"
            summaries.append(f"Shift the planned dates for {task}.")
        elif kind == "material":
            task = _string_value(action.get("taskName", action.get("taskId"))) or "the task"
            if action.get("confirmed") is True:
                summaries.append(f"Use the confirmed material readiness for {task}.")
            else:
                summaries.append(
                    f"Request or confirm material readiness for {task}; no success is assumed."
                )
        elif kind == "weather":
            if action.get("confirmed") is True:
                summaries.append("Use the confirmed weather workability window.")
            else:
                summaries.append(
                    "Confirm the weather workability window; no improvement is assumed."
                )
        else:
            summaries.append(kind.replace("_", " ").capitalize())
    for worker, hours in overtime.items():
        summaries.insert(
            0,
            f"Add {sum(hours):g} hours of overtime for {worker} across {len(hours)} scheduled shift{'s' if len(hours) != 1 else ''}.",
        )
    return " ".join(summaries) if summaries else "Keep the current plan."


def _strategy_rationale(role: str) -> str:
    return {
        "baseline": "No new action is proposed; this is the current-plan comparison.",
        "fast": (
            "Ranks feasible options by the priority project's late risk, delay, and tail finish."
        ),
        "balanced": (
            "Ranks feasible options by priority-weighted portfolio risk, delay, "
            "buffer exposure, and disruption."
        ),
        "safe": (
            "Ranks feasible options by worst-project risk, tail exposure, "
            "buffer exposure, and disruption."
        ),
    }.get(role, "This option is a distinct feasible bundle from the bounded simulation search.")


def _donor_project_id(
    strategy: Mapping[str, object],
    target_project_id: str | None,
    outcomes: Mapping[str, object],
) -> str | None:
    affected = _string_values(strategy.get("affectedProjectIds", []))
    for project_id in affected:
        if project_id != target_project_id and project_id in outcomes:
            return project_id
    return None


def _task_material_ids(task: Mapping[str, object]) -> list[str]:
    values = task.get(
        "requiredMaterialIds",
        task.get("required_material_ids", task.get("materialIds", task.get("material_ids", []))),
    )
    ids = _string_values(values)
    single = _string_value(task.get("materialId", task.get("material_id")))
    if single is not None:
        ids.append(single)
    material = task.get("material")
    if isinstance(material, Mapping):
        material_id = _string_value(material.get("id"))
        if material_id is not None:
            ids.append(material_id)
    return list(dict.fromkeys(ids))


def _specialty_names(planning: Mapping[str, object]) -> dict[str, str]:
    result: dict[str, str] = {}
    for _index, item in enumerate(_objects(planning.get("specialties", []))):
        identifier = _string_value(item.get("id", item.get("key")))
        if identifier is not None:
            result[identifier] = _display_name(item, identifier)
    return result


def _typed_id(kind: str, identifier: str) -> str:
    return f"{kind}:{identifier}"


def _objects(value: object) -> list[JsonObject]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _objects_as_values(value: object) -> list[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return list(value)


def _required_id(item: Mapping[str, object], label: str, index: int) -> str:
    identifier = _string_value(item.get("id"))
    return identifier or f"{label}-{index}"


def _string_value(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _string_values(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _display_name(value: Mapping[str, object], fallback: str) -> str:
    for key in ("name", "title", "label", "displayName"):
        name = _string_value(value.get(key))
        if name is not None:
            return name
    return fallback


__all__ = ["build_graph", "enrich_result"]
