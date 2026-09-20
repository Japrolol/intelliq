"""Shared operational status values used at snapshot and planning boundaries."""

from collections.abc import Mapping
from typing import Any

PROJECT_STATUS_ARCHIVED = "archived"


def is_archived_project(project: Mapping[str, Any]) -> bool:
    """Return True when Timecue reports the project as archived."""

    return str(project.get("status") or "").strip().casefold() == PROJECT_STATUS_ARCHIVED


def archived_project_ids(projects: list[Mapping[str, Any]]) -> set[str]:
    """Return identifiers of archived projects that must not enter a forecast."""

    return {
        str(project["id"])
        for project in projects
        if project.get("id") and is_archived_project(project)
    }


def without_archived_projects(
    projects: list[dict[str, Any]],
    *collections: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], ...]:
    """Drop archived projects and any rows that still point at those project ids."""

    archived_ids = archived_project_ids(projects)
    if not archived_ids:
        return (projects, *collections)
    active_projects = [
        project for project in projects if str(project.get("id") or "") not in archived_ids
    ]
    filtered = [
        [
            item
            for item in collection
            if str(item.get("projectId") or "") not in archived_ids
        ]
        for collection in collections
    ]
    return (active_projects, *filtered)


__all__ = [
    "PROJECT_STATUS_ARCHIVED",
    "archived_project_ids",
    "is_archived_project",
    "without_archived_projects",
]
