"""Retrieve accepted source knowledge and measured, comparable decision outcomes.

This is evidence retrieval, not training on previous AI answers. Callers provide
an authorized tenant and frozen planning snapshot; no extracted claim mutates
planning. Transfer calibration is rebuilt from unique measured observations.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any

from src.app.decisions.calibration import update_setup_hours
from src.app.persistence.store import Store

MAX_MEMORY_FACTS = 8


def retrieve_analysis_memory(
    store: Store, organization_id: str, planning: dict[str, Any], source_mode: str
) -> dict[str, Any]:
    """Rank current, accepted document claims by entity and topic relevance."""
    projects = {str(row["id"]) for row in planning.get("projects", [])}
    tasks = {str(row["id"]) for row in planning.get("tasks", [])}
    query = " ".join(
        str(row.get(field, ""))
        for row in planning.get("tasks", [])
        for field in ("title", "workType", "requiredSpecialtyId")
    )
    terms = set(re.findall(r"[a-z]{3,}", query.lower()))
    documents = {
        str(row["id"]): row
        for row in store.list_records(organization_id, "knowledge_documents")
        if row.get("status") != "superseded"
        and row.get("active") is not False
        and row.get("provenance", {}).get("sourceType") != "analysis_summary"
    }
    ranked: list[tuple[int, str, dict[str, Any]]] = []
    for claim in store.list_records(organization_id, "knowledge_claims"):
        if claim.get("status") != "confirmed" or claim.get("active") is False:
            continue
        document_id = str(claim.get("sourceDocumentId") or claim.get("documentId") or "")
        document = documents.get(document_id)
        if document is None or claim.get("sourceRevision") != document.get("sourceRevision"):
            continue
        if document.get("sourceMode", source_mode) != source_mode:
            continue
        project_id = str(claim.get("projectId") or document.get("projectId") or "")
        if project_id and project_id not in projects:
            continue
        known_at = claim.get("knownAt") or document.get("knownAt") or document.get("createdAt")
        if not _known_by(known_at, planning["asOf"]) or not _known_by(
            claim.get("updatedAt"), planning["asOf"]
        ):
            continue
        task_id = str(claim.get("taskId") or claim.get("linkedTaskId") or "")
        summary = str(claim.get("summary") or claim.get("assertion") or "")[:500]
        overlap = terms.intersection(re.findall(r"[a-z]{3,}", summary.lower()))
        score = (6 if task_id in tasks else 0) + (3 if project_id in projects else 0) + len(overlap)
        if not score:
            continue
        fact = {
            "id": f"knowledge:{claim['id']}",
            "claimId": claim["id"],
            "documentId": document_id,
            "sourceRevision": claim.get("sourceRevision"),
            "summary": summary,
            "quote": claim.get("quote"),
            "knownAt": known_at,
            "projectId": project_id or None,
            "taskId": task_id or None,
            "confirmed": True,
            "acceptance": claim.get("acceptance", "human_review"),
            "relevance": score,
        }
        ranked.append((score, str(claim["id"]), fact))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    facts = [item[2] for item in ranked[:MAX_MEMORY_FACTS]]
    return {
        "version": "reviewed-memory-v1",
        "facts": facts,
        "retrieval": "entity_links_and_topic_overlap",
        "revision": hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest(),
        "candidateCount": len(ranked),
    }


def calibrate_transfer_context(
    planning: dict[str, Any],
    executions: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
    source_mode: str,
) -> list[dict[str, Any]]:
    """Adjust only matching worker/site-pair setup priors from measured outcomes."""
    by_execution = {
        str(row["id"]): row for row in executions if row.get("sourceMode") == source_mode
    }
    observations: dict[tuple[str, str, str], dict[str, float]] = {}
    for outcome in outcomes:
        if (
            outcome.get("sourceMode") != source_mode
            or not _known_by(outcome.get("eventAt"), planning["asOf"])
            or not _known_by(outcome.get("createdAt") or outcome.get("updatedAt"), planning["asOf"])
        ):
            continue
        execution = by_execution.get(str(outcome.get("executionId")))
        hours = outcome.get("setupHours")
        if (
            execution is None
            or not isinstance(hours, (int, float))
            or isinstance(hours, bool)
            or not 0 <= hours <= 72
        ):
            continue
        for step in execution.get("steps", []):
            if outcome.get("id") != f"{execution['id']}:{step.get('id')}:setup":
                continue
            action = step.get("action", {})
            if (
                step.get("status") != "confirmed"
                or action.get("type") != "transfer"
                or action.get("workerId") != outcome.get("workerId")
            ):
                continue
            key = tuple(
                str(action.get(field, "")) for field in ("workerId", "fromProjectId", "toProjectId")
            )
            observations.setdefault(key, {})[str(outcome["id"])] = float(hours)
    updates = []
    for transfer in planning.get("transferAssumptions", []):
        key = tuple(
            str(transfer.get(field, "")) for field in ("workerId", "fromProjectId", "toProjectId")
        )
        measured = observations.get(key)
        prior = transfer.get("setupHours")
        if not measured or not isinstance(prior, (int, float)):
            continue
        calibrated = update_setup_hours(
            prior_mean_hours=float(prior),
            prior_strength=2,
            previous_sample_count=0,
            accepted_observation_hours=tuple(measured.values()),
        )
        transfer["setupHours"] = calibrated.updated_mean_hours
        updates.append(
            {
                "workerId": key[0],
                "fromProjectId": key[1],
                "toProjectId": key[2],
                "parameter": "setupHours",
                "before": prior,
                "after": calibrated.updated_mean_hours,
                "observationIds": sorted(measured),
                "sampleCount": len(measured),
                "source": "measured_comparable_transfers",
                "priorStrength": 2,
            }
        )
    return updates


def _known_by(value: object, as_of: str) -> bool:
    if not isinstance(value, str):
        return False
    try:
        observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        cutoff = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        return observed.tzinfo is not None and observed <= cutoff
    except (ValueError, TypeError):
        return False
