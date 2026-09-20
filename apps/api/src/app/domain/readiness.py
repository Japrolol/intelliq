"""Readiness gates that prevent incomplete planning inputs becoming forecasts."""

from collections.abc import Mapping
from typing import Any

from src.app.domain.contracts import PlanningInputs, PortfolioSnapshot
from src.app.domain.status import archived_project_ids


def readiness_issues(snapshot: PortfolioSnapshot, planning: PlanningInputs) -> list[dict[str, str]]:
    """Return actionable issues; unknown effort is never inferred from worklogs."""

    issues: list[dict[str, str]] = []
    skipped_project_ids = archived_project_ids(planning.projects)
    active_projects = [
        project
        for project in planning.projects
        if str(project.get("id") or "") not in skipped_project_ids
    ]
    project_ids = {str(project.get("id")) for project in active_projects if project.get("id")}
    if not project_ids:
        issues.append({"code": "missing_projects", "message": "Add at least one project target."})

    for project in active_projects:
        if not project.get("targetFinishAt"):
            issues.append(
                {
                    "code": "missing_target_finish",
                    "message": (
                        f"{project.get('name') or project.get('id', 'Project')} "
                        "needs a target finish date."
                    ),
                }
            )

    active_tasks = [
        task
        for task in planning.tasks
        if str(task.get("projectId") or "") not in skipped_project_ids
    ]
    task_ids = {str(task.get("id")) for task in active_tasks if task.get("id")}
    for task in active_tasks:
        effort = task.get("remainingPersonHours")
        if not isinstance(effort, Mapping) or any(
            effort.get(key) is None for key in ("optimistic", "mostLikely", "pessimistic")
        ):
            issues.append(
                {
                    "code": "missing_remaining_effort",
                    "message": f"Task {task.get('id', '?')} needs confirmed remaining effort.",
                }
            )
        for predecessor in task.get("predecessorIds", []) or []:
            if predecessor not in task_ids:
                issues.append(
                    {
                        "code": "unknown_predecessor",
                        "message": (
                            f"Task {task.get('id', '?')} references unknown predecessor "
                            f"{predecessor}."
                        ),
                    }
                )

    if not snapshot.complete:
        issues.append(
            {
                "code": "incomplete_snapshot",
                "message": "The source snapshot is incomplete; refresh before forecasting.",
            }
        )
    return _dedupe(issues)


def has_cycle(tasks: list[dict[str, Any]]) -> bool:
    """Detect dependency cycles before delegating to the simulation engine."""

    graph = {
        str(task.get("id")): set(task.get("predecessorIds", []) or [])
        for task in tasks
        if task.get("id")
    }
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> bool:
        if task_id in visiting:
            return True
        if task_id in visited:
            return False
        visiting.add(task_id)
        if any(
            predecessor in graph and visit(predecessor) for predecessor in graph.get(task_id, set())
        ):
            return True
        visiting.remove(task_id)
        visited.add(task_id)
        return False

    return any(visit(task_id) for task_id in graph)


def _dedupe(issues: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, str]] = []
    for issue in issues:
        key = (issue["code"], issue["message"])
        if key not in seen:
            result.append(issue)
            seen.add(key)
    return result


__all__ = ["has_cycle", "readiness_issues"]
