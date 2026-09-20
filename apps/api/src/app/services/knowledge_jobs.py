"""Durable document import staging and worker execution."""

import hashlib
from typing import Any
from src.app.integrations.object_storage import ObjectStorageError, get_object

from src.app.services.knowledge import (
    MAX_KNOWLEDGE_BYTES,
    KnowledgeServiceError,
    UploadedKnowledge,
    _object_ref,
    _safe_filename,
    _store_object,
    ingest_document,
)
from src.app.workers.service import enqueue_analysis


def enqueue_knowledge(
    state: Any, org_id: str, session_id: str, upload: UploadedKnowledge, **metadata: Any
) -> dict[str, Any]:
    if not upload.content or len(upload.content) > MAX_KNOWLEDGE_BYTES:
        raise KnowledgeServiceError(
            "knowledge_file_size_invalid", "Choose a non-empty file up to 10 MB."
        )
    filename = _safe_filename(upload.filename)
    object_ref = _object_ref(org_id, hashlib.sha256(upload.content).hexdigest(), filename)
    _store_object(state.settings, object_ref, upload.content)
    job = enqueue_analysis(
        state.store,
        org_id,
        session_id,
        {
            "objectRef": object_ref,
            "fileName": filename,
            "contentType": upload.content_type,
            **metadata,
        },
        job_type="knowledge_import",
    )
    return {**job, "jobId": job["id"]}


def run_knowledge_import(
    state: Any, org_id: str, session: Any, body: dict[str, Any], progress: Any
) -> dict[str, Any]:
    job_id = body["jobId"]
    receipt = state.store.get_record(org_id, "knowledge_import_result", job_id)
    if receipt:
        return receipt
    object_ref = str(body.get("objectRef", ""))
    expected_prefix = _object_ref(org_id, "", "").rsplit("/", 1)[0] + "/"
    if not object_ref.startswith(expected_prefix):
        raise KnowledgeServiceError("knowledge_upload_missing", "The uploaded file is unavailable.")
    progress("extracting", 30)
    try:
        content = get_object(state.settings, object_ref, max_bytes=MAX_KNOWLEDGE_BYTES)
    except ObjectStorageError as exc:
        raise KnowledgeServiceError(
            "knowledge_storage_unavailable", str(exc), status_code=503
        ) from exc
    document = ingest_document(
        state.store,
        org_id,
        UploadedKnowledge(
            filename=str(body["fileName"]),
            content=content,
            content_type=body.get("contentType"),
        ),
        state.settings,
        project_id=body.get("projectId"),
        captured_at=body.get("capturedAt"),
        known_at=body.get("knownAt"),
    )
    result = {"status": "completed", "documentId": document["id"]}
    state.store.save_record(org_id, "knowledge_import_result", job_id, result)
    progress("connecting", 80)
    from src.app.services.knowledge_embeddings import refresh_embeddings

    result["embeddingStatus"] = refresh_embeddings(state.store, org_id, state.settings)
    return result
