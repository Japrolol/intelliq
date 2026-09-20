"""Pure safety helpers for owner-approved execution."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum

Step = Mapping[str, object]

_DATE_SHIFT_TYPES = frozenset(
    {"date_shift", "planned_date_shift", "planned_dates", "schedule_change", "schedule_shift"}
)
_MANUAL_PREREQUISITE_TYPES = frozenset(
    {
        "material",
        "material_ready",
        "material_response",
        "material_shift",
        "move_worker",
        "transfer",
        "weather",
        "weather_reschedule",
        "weather_response",
        "weather_shift",
        "worker_transfer",
    }
)


class ReconciliationStatus(StrEnum):
    """Safe outcomes for a mutation's post-write readback."""

    APPLIED = "applied"
    UNCHANGED = "unchanged"
    CONFLICT = "conflict"


def normalize_timestamp(value: object) -> object:
    """Normalize ISO timestamps to a comparable UTC/naive representation."""

    parsed = _parse_timestamp(value)
    if parsed is None:
        return value
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return f"naive:{parsed.isoformat(timespec='microseconds')}"
    return f"aware:{parsed.astimezone(UTC).isoformat(timespec='microseconds')}"


def readback_field_mismatches(
    expected: Mapping[str, object],
    actual: Mapping[str, object],
    fields: Iterable[str] | None = None,
) -> tuple[str, ...]:
    """Return requested fields missing or different in an upstream readback."""

    requested = tuple(dict.fromkeys(fields if fields is not None else expected))
    mismatches: list[str] = []
    for field in requested:
        if field not in expected or field not in actual:
            mismatches.append(field)
            continue
        left = normalize_timestamp(expected[field]) if _timestamp_field(field) else expected[field]
        right = normalize_timestamp(actual[field]) if _timestamp_field(field) else actual[field]
        if type(left) is not type(right) or left != right:
            mismatches.append(field)
    return tuple(mismatches)


def compare_readback_fields(
    expected: Mapping[str, object],
    actual: Mapping[str, object],
    fields: Iterable[str] | None = None,
) -> bool:
    """Return true only when every requested field matches exactly."""

    return not readback_field_mismatches(expected, actual, fields)


def classify_reconciliation(
    before: Mapping[str, object],
    desired: Mapping[str, object],
    readback: Mapping[str, object],
    fields: Iterable[str] | None = None,
) -> ReconciliationStatus:
    """Classify readback as applied, unchanged, or a conflict."""

    requested = tuple(dict.fromkeys(fields if fields is not None else desired))
    if not requested:
        return ReconciliationStatus.CONFLICT
    if compare_readback_fields(before, readback, requested):
        return ReconciliationStatus.UNCHANGED
    if compare_readback_fields(desired, readback, requested):
        return ReconciliationStatus.APPLIED
    return ReconciliationStatus.CONFLICT


def step_dependencies(steps: Sequence[Step]) -> dict[str, tuple[str, ...]]:
    """Infer same-bundle manual prerequisites for date-shift steps."""

    ids: list[str] = []
    seen: set[str] = set()
    for step in steps:
        step_id = step.get("id")
        if not isinstance(step_id, str) or not step_id:
            raise ValueError("execution steps require non-empty string ids")
        if step_id in seen:
            raise ValueError(f"duplicate execution step id: {step_id}")
        seen.add(step_id)
        ids.append(step_id)
    prerequisites = tuple(
        step_id for step, step_id in zip(steps, ids, strict=True) if _manual_prerequisite(step)
    )
    return {
        step_id: prerequisites if _action_type(step) in _DATE_SHIFT_TYPES else ()
        for step, step_id in zip(steps, ids, strict=True)
    }


def execution_request_fingerprint(
    analysis_id: str,
    scenario_id: str,
    review_hash: str,
) -> str:
    """Hash the complete approved request identity, excluding its key."""

    payload = {
        "analysisId": analysis_id,
        "scenarioId": scenario_id,
        "reviewHash": review_hash,
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _parse_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if candidate.endswith(("Z", "z")):
        candidate = f"{candidate[:-1]}+00:00"
    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        return None


def _timestamp_field(field: str) -> bool:
    return field.replace("_", "").lower().endswith(("at", "date", "timestamp"))


def _action_type(step: Step) -> str:
    action = step.get("action")
    value = action.get("type", action.get("kind")) if isinstance(action, Mapping) else None
    if not isinstance(value, str):
        value = step.get("actionType")
    if not isinstance(value, str):
        return ""
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _manual_prerequisite(step: Step) -> bool:
    if _action_type(step) not in _MANUAL_PREREQUISITE_TYPES:
        return False
    action = step.get("action")
    capability = action.get("capability") if isinstance(action, Mapping) else None
    return step.get("kind") == "manual" or capability == "manual"


__all__ = [
    "ReconciliationStatus",
    "compare_readback_fields",
    "execution_request_fingerprint",
    "normalize_timestamp",
    "readback_field_mismatches",
    "step_dependencies",
]
