"""Versioned extraction cache; accepted knowledge never mutates planning itself."""

import hashlib
import json
from typing import Any

from src.app.integrations.evidence_bridge import extract_evidence
from src.app.intelligence.evidence import LEXICAL_EXTRACTOR_VERSION


def ingest_worklogs(state: Any, snapshot: Any) -> dict[str, Any]:
    task_ids = {str(t["id"]) for t in snapshot.tasks}
    org = snapshot.organization_id
    provider = (
        state.settings.llm_model if state.settings.allow_external_text_processing else "local"
    )
    processed = 0
    pending = 0
    for worklog in snapshot.worklogs:
        description = str(worklog.get("description", ""))
        if not description.strip():
            continue
        source_id = str(worklog["id"])
        revision = str(worklog.get("updatedAt") or worklog.get("knownAt") or "unknown")
        cache_key = hashlib.sha256(
            json.dumps(
                [
                    source_id,
                    revision,
                    description,
                    provider,
                    LEXICAL_EXTRACTOR_VERSION,
                    sorted(task_ids),
                ]
            ).encode()
        ).hexdigest()
        if state.store.get_record(org, "extraction_cache", cache_key):
            continue
        if provider != "local" and processed >= 5:
            pending += 1
            continue
        linked = worklog.get("taskId")
        source = {
            "sourceType": "worklog",
            "sourceId": source_id,
            "sourceRevision": revision,
            "knownAt": worklog.get("updatedAt") or worklog.get("knownAt"),
            "projectId": worklog.get("projectId"),
            "taskId": linked,
            "workDate": worklog.get("workDate"),
            "timezone": worklog.get("projectTimezone", "UTC"),
        }
        observations = extract_evidence(
            description,
            {linked} if linked in task_ids else task_ids,
            source,
            settings=state.settings,
        )
        for index, observation in enumerate(observations):
            signal_id = hashlib.sha256(f"{cache_key}:{index}".encode()).hexdigest()[:32]
            payload = {
                "id": signal_id,
                "organizationId": org,
                "projectId": worklog.get("projectId"),
                "taskId": observation.get("linkedTaskId", linked),
                "status": observation.get("status", "proposed"),
                "observation": observation,
                "sourceText": description,
                "sourceRevision": revision,
                "sourceId": source_id,
                "provider": provider,
                "extractionVersion": LEXICAL_EXTRACTOR_VERSION,
            }
            state.store.save_signal(
                org, signal_id, worklog.get("projectId"), payload["status"], payload
            )
        state.store.save_record(
            org,
            "extraction_cache",
            cache_key,
            {
                "sourceId": source_id,
                "sourceRevision": revision,
                "provider": provider,
                "observationCount": len(observations),
            },
        )
        processed += 1
    _publish_source_knowledge(state.store, snapshot)
    return {
        "processed": processed,
        "pending": pending,
        "status": "partial" if pending else "complete",
        "provider": provider,
    }


def _publish_source_knowledge(store: Any, snapshot: Any) -> None:
    """Expose current extracted Timecue notes in the same deletable knowledge graph."""
    org = snapshot.organization_id
    worklogs = {str(row["id"]): row for row in snapshot.worklogs}
    for signal in store.list_signals(org):
        if signal.get("status") in {"rejected", "superseded", "deleted"}:
            continue
        source_id = str(signal.get("sourceId", ""))
        worklog = worklogs.get(source_id)
        if worklog is None:
            continue
        revision = str(worklog.get("updatedAt") or worklog.get("knownAt") or "unknown")
        if signal.get("sourceRevision") != revision:
            continue
        document_id = f"timecue-worklog:{source_id}"
        claim_id = f"worklog-claim:{signal['id']}"
        document = store.get_record(org, "knowledge_documents", document_id)
        if document and document.get("active") is False:
            continue  # User deletion is persistent, including later source refreshes.
        claim = store.get_record(org, "knowledge_claims", claim_id)
        if claim is not None:
            continue
        known_at = worklog.get("updatedAt") or worklog.get("knownAt") or snapshot.as_of.isoformat()
        observation = signal.get("observation", {})
        quotes = observation.get("evidenceQuotes", [])
        quote = quotes[0].get("text") if quotes else None
        if not observation.get("summary") or not quote:
            continue
        store.save_record(
            org,
            "knowledge_documents",
            document_id,
            {
                "id": document_id,
                "organizationId": org,
                "projectId": worklog.get("projectId"),
                "sourceMode": snapshot.source_mode.value,
                "sourceRevision": revision,
                "fileName": f"Site note · {str(worklog.get('workDate') or known_at)[:10]}",
                "mediaType": "text/plain",
                "status": "extracted",
                "active": True,
                "knownAt": known_at,
                "capturedAt": known_at,
                "provenance": {"sourceType": "timecue_worklog", "sourceId": source_id},
            },
        )
        store.save_record(
            org,
            "knowledge_claims",
            claim_id,
            {
                "id": claim_id,
                "organizationId": org,
                "projectId": worklog.get("projectId"),
                "taskId": signal.get("taskId"),
                "sourceDocumentId": document_id,
                "documentId": document_id,
                "sourceRevision": revision,
                "summary": observation["summary"],
                "quote": quote,
                "knownAt": known_at,
                "kind": observation.get("kind"),
                "status": "confirmed",
                "active": True,
                "acceptance": "automatic",
                "acceptedAt": snapshot.as_of.isoformat(),
                "source": {"documentId": document_id, "sourceRevision": revision, "quote": quote},
            },
        )
