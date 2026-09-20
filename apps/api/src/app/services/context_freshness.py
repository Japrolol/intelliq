"""Approval-time validation for frozen portfolio context.

An analysis stores the engine planning snapshot in an ``analysis_input`` record.
Approval must re-run the same context boundary against the immutable input and
compare its revision with the analysis revision before compiling execution
steps.  This module is deliberately synchronous because the current approval
routes and :func:`enrich_context` are synchronous; provider work is bounded by
the adapters themselves and never runs per scenario.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from src.app.integrations.context import (
    ContextCache,
    OpenRouteServiceMatrixAdapter,
    ProviderStatus,
    WeatherAPIAdapter,
    enrich_context,
)

JsonObject = dict[str, Any]

CONTEXT_FRESHNESS_CONFLICT_CODE = "analysis_context_freshness_conflict"
_DEGRADED_STATUSES = frozenset(
    {
        ProviderStatus.PARTIAL.value,
        ProviderStatus.STALE.value,
        ProviderStatus.ERROR.value,
        ProviderStatus.UNAVAILABLE.value,
        ProviderStatus.NOT_CONFIGURED.value,
    }
)


class ContextFreshnessError(ValueError):
    """Framework-neutral approval conflict with an explicit HTTP 409 status."""

    def __init__(self, result: Mapping[str, object]) -> None:
        conflict = result.get("conflict")
        if not isinstance(conflict, Mapping):
            conflict = {
                "statusCode": 409,
                "code": CONTEXT_FRESHNESS_CONFLICT_CODE,
                "message": "The analysis context is not fresh enough for approval.",
            }
        self.status_code = 409
        self.code = str(conflict.get("code", CONTEXT_FRESHNESS_CONFLICT_CODE))
        self.message = str(conflict.get("message", "Context freshness conflict."))
        self.details = copy.deepcopy(dict(conflict))
        self.result = copy.deepcopy(dict(result))
        super().__init__(self.message)


def enrich_frozen_context_for_approval(
    analysis: Mapping[str, object],
    analysis_input: Mapping[str, object],
    settings: object,
    *,
    weather_adapter: WeatherAPIAdapter | None = None,
    routing_adapter: OpenRouteServiceMatrixAdapter | None = None,
    cache: ContextCache | None = None,
    now: datetime | None = None,
) -> JsonObject:
    """Re-enrich the saved analysis input and return an approval freshness result.

    ``contextInput`` is preferred when present because it is the exact payload
    before provider-derived fields were added.  The current portfolio writer
    stores only ``planning`` after enrichment, so that shape remains a supported
    fallback and is idempotent with the current context seam.  The sibling
    ``graph`` key is presentation-only and is never used as simulation input.

    The function does not raise on a stale or degraded result.  Its ``conflict``
    member contains a sanitized, route-ready 409 description.  Callers that
    want exception control flow can use
    :func:`require_fresh_context_for_approval`.
    """

    expected_revision = _optional_string(analysis.get("weatherRevision"))
    planning, input_key, input_error = _saved_planning(analysis_input)
    if planning is None:
        return _invalid_input_result(
            expected_revision,
            input_key=input_key,
            code=input_error or "analysis_input_planning_missing",
            message="The saved analysis input has no usable planning payload.",
        )
    if expected_revision is None:
        return _invalid_input_result(
            None,
            input_key=input_key,
            code="analysis_context_revision_missing",
            message="The completed analysis has no weatherRevision to validate.",
        )

    enriched = enrich_context(
        copy.deepcopy(planning),
        settings,
        weather_adapter=weather_adapter,
        routing_adapter=routing_adapter,
        cache=cache,
        now=now,
    )
    actual_revision = _optional_string(enriched.get("revision"))
    status = _mapping_copy(enriched.get("status"))
    warnings = _string_list(enriched.get("warnings"))
    degraded_providers = _degraded_providers(status)
    declared_status = _mapping_copy(analysis.get("providerStatus"))
    declared_degraded_providers = _degraded_providers(declared_status)
    new_degraded_providers = _new_degradation(
        degraded_providers, declared_degraded_providers
    )
    mismatch = actual_revision != expected_revision
    degraded = bool(degraded_providers)
    new_degradation = bool(new_degraded_providers)
    conflict = _conflict(
        expected_revision=expected_revision,
        actual_revision=actual_revision,
        input_key=input_key,
        mismatch=mismatch,
        new_degradation=new_degradation,
        degraded_providers=degraded_providers,
        warnings=warnings,
    )
    return {
        "planning": enriched.get("planning"),
        "status": status,
        "warnings": warnings,
        "revision": actual_revision,
        "expectedRevision": expected_revision,
        "inputKey": input_key,
        "matched": not mismatch,
        "mismatch": mismatch,
        "degraded": degraded,
        "newDegradation": new_degradation,
        "degradedProviders": degraded_providers,
        "declaredDegradedProviders": declared_degraded_providers,
        "fresh": not mismatch and not new_degradation,
        "conflict": conflict,
    }


def require_fresh_context_for_approval(
    analysis: Mapping[str, object],
    analysis_input: Mapping[str, object],
    settings: object,
    *,
    weather_adapter: WeatherAPIAdapter | None = None,
    routing_adapter: OpenRouteServiceMatrixAdapter | None = None,
    cache: ContextCache | None = None,
    now: datetime | None = None,
) -> JsonObject:
    """Return a fresh context or raise a sanitized 409-compatible error."""

    result = enrich_frozen_context_for_approval(
        analysis,
        analysis_input,
        settings,
        weather_adapter=weather_adapter,
        routing_adapter=routing_adapter,
        cache=cache,
        now=now,
    )
    if result["fresh"] is not True:
        raise ContextFreshnessError(result)
    return result


def _saved_planning(
    analysis_input: Mapping[str, object],
) -> tuple[JsonObject | None, str, str | None]:
    context_input = analysis_input.get("contextInput")
    if isinstance(context_input, Mapping):
        return copy.deepcopy(dict(context_input)), "contextInput", None
    if context_input is not None:
        return None, "contextInput", "analysis_context_input_invalid"

    planning = analysis_input.get("planning")
    if isinstance(planning, Mapping):
        return copy.deepcopy(dict(planning)), "planning", None
    return None, "planning", "analysis_input_planning_missing"


def _invalid_input_result(
    expected_revision: str | None,
    *,
    input_key: str,
    code: str,
    message: str,
) -> JsonObject:
    conflict = {
        "statusCode": 409,
        "code": code,
        "message": message,
        "reasons": [code],
        "expectedRevision": expected_revision,
        "actualRevision": None,
        "inputKey": input_key,
    }
    return {
        "planning": None,
        "status": {"overall": ProviderStatus.UNAVAILABLE.value, "error": {"code": code}},
        "warnings": [code],
        "revision": None,
        "expectedRevision": expected_revision,
        "inputKey": input_key,
        "matched": False,
        "mismatch": expected_revision is not None,
        "degraded": True,
        "newDegradation": True,
        "degradedProviders": {},
        "declaredDegradedProviders": {},
        "fresh": False,
        "conflict": conflict,
    }


def _degraded_providers(status: Mapping[str, object]) -> JsonObject:
    degraded: JsonObject = {}
    overall = _optional_string(status.get("overall"))
    if overall in _DEGRADED_STATUSES:
        degraded["overall"] = {"status": overall}
    for provider in ("weather", "routing"):
        value = status.get(provider)
        if not isinstance(value, Mapping):
            continue
        provider_status = _optional_string(value.get("status", value.get("state")))
        coverage = value.get("coverage")
        coverage_incomplete = isinstance(coverage, Mapping) and coverage.get("complete") is False
        project_coverage = value.get("coverageByProject")
        if isinstance(project_coverage, Mapping):
            coverage_incomplete = coverage_incomplete or any(
                isinstance(item, Mapping) and item.get("complete") is False
                for item in project_coverage.values()
            )
        project_status = value.get("projects")
        project_degraded = isinstance(project_status, Mapping) and any(
            isinstance(item, Mapping)
            and _optional_string(item.get("status", item.get("state"))) in _DEGRADED_STATUSES
            for item in project_status.values()
        )
        if provider_status in _DEGRADED_STATUSES or coverage_incomplete or project_degraded:
            degraded[provider] = {
                "status": provider_status,
                "coverage": copy.deepcopy(coverage),
                "coverageByProject": copy.deepcopy(project_coverage),
            }
    return degraded


def _new_degradation(
    current: Mapping[str, object], declared: Mapping[str, object]
) -> JsonObject:
    """Return only degraded provider entries absent or changed from the run."""

    result: JsonObject = {}
    for provider, current_value in current.items():
        # ``overall`` is a derived aggregate.  A legacy/provider-status DTO may
        # omit it even when its weather/routing entries are declared; the
        # provider entries are the approval comparison boundary.
        if provider == "overall" and provider not in declared:
            continue
        declared_value = declared.get(provider)
        if not isinstance(current_value, Mapping):
            continue
        if not isinstance(declared_value, Mapping) or not _same_degradation(
            current_value, declared_value
        ):
            result[provider] = copy.deepcopy(current_value)
    return result


def _same_degradation(current: Mapping[str, object], declared: Mapping[str, object]) -> bool:
    """Compare only persisted provider status and coverage, not volatile details."""

    fields = ("status", "coverage", "coverageByProject")
    return all(
        _stable_degradation_value(current.get(field))
        == _stable_degradation_value(declared.get(field))
        for field in fields
    )


def _stable_degradation_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _stable_degradation_value(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
            if item is not None
        }
    if isinstance(value, list):
        return [_stable_degradation_value(item) for item in value]
    return value


def _conflict(
    *,
    expected_revision: str,
    actual_revision: str | None,
    input_key: str,
    mismatch: bool,
    new_degradation: bool,
    degraded_providers: Mapping[str, object],
    warnings: list[str],
) -> JsonObject | None:
    if not mismatch and not new_degradation:
        return None
    reasons: list[str] = []
    if mismatch:
        reasons.append("revision_mismatch")
    if new_degradation:
        reasons.append("provider_context_worsened")
    if mismatch and new_degradation:
        code = "analysis_context_stale_and_degraded"
    elif mismatch:
        code = "analysis_context_revision_mismatch"
    else:
        code = "analysis_context_degraded"
    return {
        "statusCode": 409,
        "code": code,
        "message": "Refresh the analysis before approval; context changed or is degraded.",
        "reasons": reasons,
        "expectedRevision": expected_revision,
        "actualRevision": actual_revision,
        "inputKey": input_key,
        "degradedProviders": copy.deepcopy(dict(degraded_providers)),
        "warnings": list(warnings),
    }


def _mapping_copy(value: object) -> JsonObject:
    return copy.deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _optional_string(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


__all__ = [
    "CONTEXT_FRESHNESS_CONFLICT_CODE",
    "ContextFreshnessError",
    "enrich_frozen_context_for_approval",
    "require_fresh_context_for_approval",
]
