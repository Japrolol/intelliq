"""Narrow import boundary for the separately owned evidence implementation."""

from __future__ import annotations

import importlib
from datetime import date, datetime
from typing import Any


class EvidenceIntegrationError(RuntimeError):
    """A configured evidence provider failed without exposing text or secrets."""


def extract_evidence(
    text: str, task_ids: set[str], source: dict[str, Any], settings: Any = None
) -> list[dict[str, Any]]:
    """Call the evidence seam when present, with a bounded local fallback meanwhile."""

    try:
        module = importlib.import_module("src.app.intelligence.evidence")
    except ModuleNotFoundError:
        try:
            module = importlib.import_module("app.intelligence.evidence")
        except ModuleNotFoundError:
            return _lexical_fallback(text, task_ids, source)
    extractor = getattr(module, "extract_evidence", None)
    if extractor is not None:
        try:
            result = extractor(text, sorted(task_ids), source)
        except TypeError:
            result = extractor(text=text, canonical_task_ids=sorted(task_ids), source=source)
        return _normalize_result(result, source)
    extractor = getattr(module, "extract_signals", None)
    context_class = getattr(module, "ExtractionContext", None)
    if extractor is None or context_class is None:
        return _lexical_fallback(text, task_ids, source)
    try:
        work_date = date.fromisoformat(str(source["workDate"])) if source.get("workDate") else None
        known_at = _parse_datetime(source.get("knownAt"))
        context = context_class(
            source_id=str(source.get("sourceId", "unknown")),
            source_revision=str(source.get("sourceRevision", "unknown")),
            source_kind=str(source.get("sourceType", "worklog")),
            canonical_task_ids=tuple(sorted(task_ids)),
            project_id=str(source["projectId"]) if source.get("projectId") else None,
            task_id=str(source["taskId"]) if source.get("taskId") else None,
            work_date=work_date,
            known_at=known_at,
            timezone=str(source.get("timezone", "UTC")),
        )
        try:
            from src.app.intelligence.instruction_provider import (
                InstructionModelSettings,
                instruction_model_config,
            )

            provider_settings = (
                InstructionModelSettings(
                    allow_external_text_processing=settings.allow_external_text_processing,
                    nlp_enabled=settings.nlp_enabled,
                    api_base_url=settings.llm_api_base_url,
                    model=settings.llm_model,
                    api_key=settings.llm_api_key,
                )
                if settings is not None
                else InstructionModelSettings.from_environment()
            )
            config = instruction_model_config(provider_settings)
            result = extractor(text, context, config=config)
        except Exception as exc:
            if (
                settings is not None
                and settings.allow_external_text_processing
                and settings.nlp_enabled
            ) or _external_provider_enabled():
                raise EvidenceIntegrationError("evidence_provider_failed") from exc
            raise
    except (TypeError, ValueError, KeyError):
        return _lexical_fallback(text, task_ids, source)
    return _normalize_result(result, source)


def _external_provider_enabled() -> bool:
    """Read only enablement flags; never read or log the provider secret."""

    import os

    return (
        os.environ.get("NLP_ENABLED", "true") == "true"
        and os.environ.get("ALLOW_EXTERNAL_TEXT_PROCESSING") == "true"
    )


def _normalize_result(result: Any, source: dict[str, Any]) -> list[dict[str, Any]]:
    observations = (
        result.get("observations", result.get("signals", []))
        if isinstance(result, dict)
        else getattr(result, "observations", [])
    )
    normalized: list[dict[str, Any]] = []
    for item in observations:
        if isinstance(item, dict):
            normalized.append(item)
            continue
        quotes = []
        for quote in getattr(item, "evidence_quotes", ()):
            quotes.append({"text": quote.text, "start": quote.start, "end": quote.end})
        item_source = getattr(item, "source", None)
        normalized.append(
            {
                "signalId": str(getattr(item, "signal_id", "")),
                "kind": str(getattr(item, "kind", "")),
                "assertion": str(getattr(item, "assertion", "")),
                "reportedState": str(getattr(item, "reported_state", "")),
                "summary": str(getattr(item, "summary", "")),
                "evidenceQuotes": quotes,
                "linkedTaskId": getattr(item, "linked_task_id", None),
                "relatedTaskCandidateIds": list(getattr(item, "related_task_candidate_ids", ())),
                "status": str(getattr(item, "review_status", "proposed")),
                "source": {
                    "sourceId": getattr(item_source, "source_id", source.get("sourceId")),
                    "sourceRevision": getattr(
                        item_source, "source_revision", source.get("sourceRevision")
                    ),
                    "sourceKind": getattr(
                        item_source, "source_kind", source.get("sourceType", "worklog")
                    ),
                },
            }
        )
    return normalized


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _lexical_fallback(
    text: str, task_ids: set[str], source: dict[str, Any]
) -> list[dict[str, Any]]:
    lowered = text.lower()
    if "cannot close" not in lowered and "unfinished" not in lowered and "blocked" not in lowered:
        return []
    return [
        {
            "kind": "dependency_blocker",
            "assertion": "reported",
            "reportedState": "active",
            "summary": "The note reports a dependency blocking work.",
            "evidenceQuotes": [text],
            "linkedTaskId": None,
            "relatedTaskCandidateIds": [],
            "ambiguityReasons": ["The source is not canonically linked to a task."],
            "status": "proposed",
            "source": source,
            "provider": "local_lexical_fallback",
        }
    ]


__all__ = ["EvidenceIntegrationError", "extract_evidence"]
