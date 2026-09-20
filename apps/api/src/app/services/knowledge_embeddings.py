"""Bounded semantic links for the tenant-scoped knowledge graph.

This module keeps embeddings in the existing workflow-record store so the
hackathon corpus does not need a vector extension or a second persistence
path.  Provider failures are explicit and sanitized.  Semantic linking is
always local and only reports similarity; it does not establish causation or
alter planning decisions.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass

import httpx

from src.app.persistence.store import Store

DOCUMENT_RECORD_KIND = "knowledge_documents"
CLAIM_RECORD_KIND = "knowledge_claims"
EMBEDDING_RECORD_KIND = "knowledge_embedding"

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 512
MAX_DOCUMENTS = 256
MAX_BATCH_SIZE = 64
MAX_DOCUMENT_CHARS = 6_000
MAX_LINKS_PER_DOCUMENT = 2
MAX_LINKS_TOTAL = 40
MIN_SIMILARITY = 0.55
MAX_QUERY_CHARS = 500
MAX_SEARCH_RESULTS = 10
MIN_SEARCH_SIMILARITY = 0.25
EMBEDDING_TIMEOUT_SECONDS = 15.0

_DELETED_STATUSES = frozenset({"deleted", "superseded"})


@dataclass(frozen=True, slots=True)
class _PreparedDocument:
    """The bounded, deterministic text and cache identity for one document."""

    document_id: str
    text: str
    source_revision: str
    content_hash: str


class _EmbeddingProviderError(ValueError):
    """Sanitized provider failure used internally by the refresh boundary."""

    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(status)


def refresh_embeddings(
    store: Store, organization_id: str, settings: object
) -> dict[str, object]:
    """Refresh bounded embeddings for current knowledge documents.

    Documents and confirmed claims are read through the tenant-scoped Store
    API.  At most 256 current documents are considered and each provider
    request contains at most 64 texts.  Existing valid records with the same
    model/text/source-revision hash are reused without a provider call.
    """

    documents = [
        document
        for document in store.list_records(organization_id, DOCUMENT_RECORD_KIND)
        if _is_current_record(document)
    ]
    limited = len(documents) > MAX_DOCUMENTS
    documents = documents[:MAX_DOCUMENTS]
    claims_by_document = _current_confirmed_claims(store, organization_id, documents)

    prepared: list[_PreparedDocument] = []
    cached_count = 0
    for document in documents:
        item = _prepare_document(document, claims_by_document)
        if item is None:
            continue
        prepared.append(item)
        existing = store.get_record(organization_id, EMBEDDING_RECORD_KIND, item.document_id)
        if _embedding_matches(existing, item):
            cached_count += 1

    if not prepared:
        return {"status": "partial" if limited else "empty", "count": 0}

    pending = [
        item
        for item in prepared
        if not _embedding_matches(
            store.get_record(organization_id, EMBEDDING_RECORD_KIND, item.document_id), item
        )
    ]
    if not pending:
        return {"status": "partial" if limited else "cached", "count": cached_count}

    if not bool(getattr(settings, "allow_external_text_processing", False)):
        return {"status": "disabled", "count": cached_count}

    api_key = str(getattr(settings, "llm_api_key", "") or "").strip()
    base_url = str(getattr(settings, "llm_api_base_url", "") or "").strip()
    if not api_key or not base_url:
        return {"status": "unconfigured", "count": cached_count}

    endpoint = _embeddings_endpoint(base_url)
    count = cached_count
    client: httpx.Client | None = None
    try:
        client = httpx.Client(follow_redirects=False, trust_env=False)
        for start in range(0, len(pending), MAX_BATCH_SIZE):
            batch = pending[start : start + MAX_BATCH_SIZE]
            vectors = _request_embeddings(
                client,
                endpoint,
                api_key,
                [item.text for item in batch],
            )
            for item, vector in zip(batch, vectors, strict=True):
                store.save_record(
                    organization_id,
                    EMBEDDING_RECORD_KIND,
                    item.document_id,
                    {
                        "vector": vector,
                        "model": EMBEDDING_MODEL,
                        "contentHash": item.content_hash,
                        "sourceRevision": item.source_revision,
                    },
                )
                count += 1
    except _EmbeddingProviderError as exc:
        return {"status": exc.status, "count": count}
    finally:
        if client is not None:
            client.close()

    return {"status": "partial" if limited else "complete", "count": count}


def semantic_links(
    store: Store, organization_id: str, documents: list[dict[str, object]]
) -> list[dict[str, object]]:
    """Return bounded cosine-similarity links for current tenant documents.

    No network or provider work occurs here.  A stored vector is eligible only
    when its model, source revision, and model/text/revision content hash still
    match the current document and its active confirmed claims.
    """

    vectors = _eligible_document_vectors(store, organization_id, documents)

    if len(vectors) < 2:
        return []

    vector_ids = sorted(vectors)
    candidates: list[tuple[float, str, str]] = []
    for index, source_id in enumerate(vector_ids):
        for target_id in vector_ids[index + 1 :]:
            similarity = _cosine_similarity(vectors[source_id], vectors[target_id])
            if similarity >= MIN_SIMILARITY:
                candidates.append((similarity, source_id, target_id))

    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    degrees: dict[str, int] = {}
    selected: list[tuple[float, str, str]] = []
    for similarity, source_id, target_id in candidates:
        if (
            degrees.get(source_id, 0) >= MAX_LINKS_PER_DOCUMENT
            or degrees.get(target_id, 0) >= MAX_LINKS_PER_DOCUMENT
        ):
            continue
        selected.append((similarity, source_id, target_id))
        degrees[source_id] = degrees.get(source_id, 0) + 1
        degrees[target_id] = degrees.get(target_id, 0) + 1
        if len(selected) >= MAX_LINKS_TOTAL:
            break

    return [
        {
            "source": source_id,
            "target": target_id,
            "similarity": float(similarity),
            "kind": "related",
        }
        for similarity, source_id, target_id in selected
    ]


def semantic_search(
    store: Store,
    organization_id: str,
    settings: object,
    query: str,
    documents: list[dict[str, object]],
) -> dict[str, object]:
    """Search scoped current documents with one real query embedding.

    Search has no lexical or synthetic fallback.  It returns ``unavailable``
    when external processing is disabled, the provider is not configured or
    the query provider response fails validation.  Candidate document vectors
    are read locally and must still match their current source revision and
    content hash.
    """

    query_text = query.strip()[:MAX_QUERY_CHARS] if isinstance(query, str) else ""
    if not query_text:
        return {"status": "unavailable", "results": []}

    vectors = _eligible_document_vectors(store, organization_id, documents)
    if not vectors:
        return {"status": "unavailable", "results": []}
    if not bool(getattr(settings, "allow_external_text_processing", False)):
        return {"status": "unavailable", "results": []}

    api_key = str(getattr(settings, "llm_api_key", "") or "").strip()
    base_url = str(getattr(settings, "llm_api_base_url", "") or "").strip()
    if not api_key or not base_url:
        return {"status": "unavailable", "results": []}

    client: httpx.Client | None = None
    try:
        client = httpx.Client(follow_redirects=False, trust_env=False)
        query_vectors = _request_embeddings(
            client,
            _embeddings_endpoint(base_url),
            api_key,
            [query_text],
        )
    except _EmbeddingProviderError:
        return {"status": "unavailable", "results": []}
    except Exception:
        return {"status": "unavailable", "results": []}
    finally:
        if client is not None:
            client.close()

    query_vector = _validated_vector(query_vectors[0]) if query_vectors else None
    if query_vector is None:
        return {"status": "unavailable", "results": []}

    results = [
        {
            "documentId": document_id,
            "similarity": float(similarity),
        }
        for document_id, vector in vectors.items()
        if (similarity := _cosine_similarity(query_vector, vector))
        >= MIN_SEARCH_SIMILARITY
    ]
    results.sort(key=lambda result: (-float(result["similarity"]), str(result["documentId"])))
    return {"status": "available", "results": results[:MAX_SEARCH_RESULTS]}


def _is_current_record(record: Mapping[str, object]) -> bool:
    """Recognize active, non-tombstoned, non-superseded knowledge records."""

    status = str(record.get("status") or "").strip().lower()
    return (
        record.get("active") is not False
        and status not in _DELETED_STATUSES
        and record.get("deleted") is not True
        and record.get("isDeleted") is not True
        and not record.get("deletedAt")
    )


def _record_id(record: Mapping[str, object]) -> str | None:
    value = record.get("id")
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _source_revision(record: Mapping[str, object]) -> str | None:
    value = record.get("sourceRevision")
    if isinstance(value, str) and value.strip():
        return value.strip()
    provenance = record.get("provenance")
    if isinstance(provenance, Mapping):
        value = provenance.get("sourceRevision")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _eligible_document_vectors(
    store: Store, organization_id: str, documents: list[dict[str, object]]
) -> dict[str, tuple[tuple[float, ...], float]]:
    """Load only requested tenant documents with current validated vectors."""

    current_documents = {
        document_id: document
        for document in store.list_records(organization_id, DOCUMENT_RECORD_KIND)
        if _is_current_record(document)
        and (document_id := _record_id(document)) is not None
    }
    requested_ids = sorted(
        {
            document_id
            for document in documents
            if (document_id := _record_id(document)) is not None
            and document_id in current_documents
        }
    )
    if not requested_ids:
        return {}

    current_claims = _current_confirmed_claims(
        store,
        organization_id,
        [current_documents[document_id] for document_id in requested_ids],
    )
    vectors: dict[str, tuple[tuple[float, ...], float]] = {}
    for document_id in requested_ids:
        prepared = _prepare_document(current_documents[document_id], current_claims)
        if prepared is None:
            continue
        embedding = store.get_record(organization_id, EMBEDDING_RECORD_KIND, document_id)
        if not _embedding_matches(embedding, prepared):
            continue
        vector = _validated_vector(embedding.get("vector") if embedding else None)
        if vector is not None:
            vectors[document_id] = vector
    return vectors


def _current_confirmed_claims(
    store: Store,
    organization_id: str,
    documents: list[dict[str, object]],
) -> dict[str, list[str]]:
    """Group only active confirmed summaries attached to each current revision."""

    document_revisions = {
        document_id: revision
        for document in documents
        if (document_id := _record_id(document)) is not None
        and (revision := _source_revision(document)) is not None
    }
    summaries: dict[str, list[tuple[str, str]]] = {}
    for claim in store.list_records(organization_id, CLAIM_RECORD_KIND):
        if not _is_current_record(claim) or claim.get("status") != "confirmed":
            continue
        document_id = claim.get("sourceDocumentId") or claim.get("documentId")
        if not isinstance(document_id, str) or document_id not in document_revisions:
            continue
        if _source_revision(claim) != document_revisions[document_id]:
            continue
        summary = claim.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            continue
        claim_id = _record_id(claim) or summary
        summaries.setdefault(document_id, []).append((claim_id, summary.strip()))

    return {
        document_id: [summary for _claim_id, summary in sorted(values)]
        for document_id, values in summaries.items()
    }


def _prepare_document(
    document: Mapping[str, object], claims_by_document: Mapping[str, list[str]]
) -> _PreparedDocument | None:
    document_id = _record_id(document)
    source_revision = _source_revision(document)
    if document_id is None or source_revision is None:
        return None

    parts: list[str] = []
    file_name = document.get("fileName")
    if isinstance(file_name, str) and file_name.strip():
        parts.append(file_name.strip())
    provenance = document.get("provenance")
    if (
        isinstance(provenance, Mapping)
        and provenance.get("sourceType") == "analysis_summary"
    ):
        summary = document.get("summary")
        if isinstance(summary, str) and summary.strip():
            parts.append(summary.strip())
    parts.extend(claims_by_document.get(document_id, []))
    text = "\n".join(parts)[:MAX_DOCUMENT_CHARS]
    if not text:
        return None
    return _PreparedDocument(
        document_id=document_id,
        text=text,
        source_revision=source_revision,
        content_hash=_content_hash(EMBEDDING_MODEL, text, source_revision),
    )


def _content_hash(model: str, text: str, source_revision: str) -> str:
    """Hash the exact provider model, bounded text, and source revision."""

    digest = hashlib.sha256()
    for value in (model, text, source_revision):
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _embedding_matches(
    embedding: Mapping[str, object] | None, document: _PreparedDocument
) -> bool:
    """Check the complete cache identity and reject malformed cached vectors."""

    if embedding is None:
        return False
    return (
        embedding.get("model") == EMBEDDING_MODEL
        and embedding.get("contentHash") == document.content_hash
        and embedding.get("sourceRevision") == document.source_revision
        and _validated_vector(embedding.get("vector")) is not None
    )


def _validated_vector(value: object) -> tuple[tuple[float, ...], float] | None:
    """Validate one finite, non-zero 512-dimensional embedding vector."""

    if not isinstance(value, list) or len(value) != EMBEDDING_DIMENSIONS:
        return None
    values: list[float] = []
    for raw in value:
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return None
        number = float(raw)
        if not math.isfinite(number):
            return None
        values.append(number)
    norm = math.sqrt(math.fsum(number * number for number in values))
    if not math.isfinite(norm) or norm <= 0:
        return None
    return tuple(values), norm


def _embeddings_endpoint(base_url: str) -> str:
    """Convert an OpenAI-compatible base or chat-completions URL to embeddings."""

    endpoint = base_url.rstrip("/")
    suffix = "/chat/completions"
    if endpoint.endswith(suffix):
        endpoint = endpoint[: -len(suffix)]
    return f"{endpoint}/embeddings"


def _request_embeddings(
    client: httpx.Client,
    endpoint: str,
    api_key: str,
    texts: list[str],
) -> list[list[float]]:
    """Make and validate one bounded embeddings request without exposing errors."""

    try:
        response = client.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": EMBEDDING_MODEL,
                "input": texts,
                "dimensions": EMBEDDING_DIMENSIONS,
            },
            timeout=httpx.Timeout(EMBEDDING_TIMEOUT_SECONDS),
            follow_redirects=False,
        )
    except httpx.TimeoutException as exc:
        raise _EmbeddingProviderError("timeout") from exc
    except httpx.HTTPError as exc:
        raise _EmbeddingProviderError("provider_error") from exc
    except Exception as exc:
        raise _EmbeddingProviderError("provider_error") from exc

    if response.status_code >= 400:
        raise _EmbeddingProviderError("provider_error")
    try:
        payload = response.json()
        return _parse_embedding_response(payload, len(texts))
    except _EmbeddingProviderError:
        raise
    except Exception as exc:
        raise _EmbeddingProviderError("invalid_response") from exc


def _parse_embedding_response(value: object, expected_count: int) -> list[list[float]]:
    """Validate response shape, indices, dimensions, finiteness, and norms."""

    if not isinstance(value, Mapping) or not isinstance(value.get("data"), list):
        raise _EmbeddingProviderError("invalid_response")
    data = value["data"]
    if len(data) != expected_count:
        raise _EmbeddingProviderError("invalid_response")

    indexed: dict[int, list[float]] = {}
    for item in data:
        if not isinstance(item, Mapping):
            raise _EmbeddingProviderError("invalid_response")
        index = item.get("index")
        raw_vector = item.get("embedding")
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or index < 0
            or index >= expected_count
            or index in indexed
            or not isinstance(raw_vector, list)
            or len(raw_vector) != EMBEDDING_DIMENSIONS
        ):
            raise _EmbeddingProviderError("invalid_response")
        vector: list[float] = []
        for raw_value in raw_vector:
            if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
                raise _EmbeddingProviderError("invalid_response")
            number = float(raw_value)
            if not math.isfinite(number):
                raise _EmbeddingProviderError("invalid_response")
            vector.append(number)
        norm = math.sqrt(math.fsum(number * number for number in vector))
        if not math.isfinite(norm) or norm <= 0:
            raise _EmbeddingProviderError("invalid_response")
        indexed[index] = vector

    if set(indexed) != set(range(expected_count)):
        raise _EmbeddingProviderError("invalid_response")
    return [indexed[index] for index in range(expected_count)]


def _cosine_similarity(
    left: tuple[tuple[float, ...], float], right: tuple[tuple[float, ...], float]
) -> float:
    """Compute cosine similarity for two already validated vectors."""

    left_values, left_norm = left
    right_values, right_norm = right
    dot = math.fsum(a * b for a, b in zip(left_values, right_values, strict=True))
    return float(dot / (left_norm * right_norm))


__all__ = ["refresh_embeddings", "semantic_links", "semantic_search"]
