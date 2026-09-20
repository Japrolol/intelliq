"""Protected import and knowledge routes for the v4 data slice.

The router only persists IntelliQ-owned ledgers or calls existing authorized
Timecue routes during an explicit import commit.  Preview and knowledge reads
never enqueue extraction or mutate planning inputs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    field_validator,
    model_validator,
)

from src.app.api.auth_service import AuthenticatedSession
from src.app.api.dependencies import require_org_permissions
from src.app.domain.contracts import PortfolioSnapshot
from src.app.integrations.timecue import UpstreamIntegrationError
from src.app.services.imports import (
    MAX_IMPORT_BYTES,
    MAX_IMPORT_FILES,
    ImportServiceError,
    TimecueRouteClient,
    UploadedImport,
    commit_import,
    preview_import,
    template_bytes,
)
from src.app.services.knowledge import (
    MAX_KNOWLEDGE_BYTES,
    KnowledgeServiceError,
    UploadedKnowledge,
    apply_confirmed_claim_projection,
    list_knowledge,
    review_claim,
)

router = APIRouter(prefix="/api")
data_router = router

READ = require_org_permissions("projects.read", "tasks.read", "workers.read", "worklogs.read")
PREVIEW_WRITE = require_org_permissions(
    "projects.read", "tasks.read", "workers.read", "worklogs.read", write=True
)
IMPORT_WRITE = require_org_permissions(
    "projects.read",
    "tasks.read",
    "workers.read",
    "worklogs.read",
    "projects.write",
    "tasks.write",
    "tasks.assign",
    write=True,
)
KNOWLEDGE_WRITE = require_org_permissions(
    "projects.read", "tasks.read", "workers.read", "worklogs.read", write=True
)


class RemainingPersonHoursBody(BaseModel):
    """Bounded triangular estimate accepted by a confirmed local projection."""

    optimistic: StrictFloat = Field(ge=0, le=1_000_000)
    most_likely: StrictFloat = Field(alias="mostLikely", ge=0, le=1_000_000)
    pessimistic: StrictFloat = Field(ge=0, le=1_000_000)

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    @model_validator(mode="after")
    def validate_order(self) -> RemainingPersonHoursBody:
        if not self.optimistic <= self.most_likely <= self.pessimistic:
            raise ValueError(
                "remainingPersonHours must be ordered optimistic <= mostLikely <= pessimistic"
            )
        return self


class PlanningChangeBody(BaseModel):
    """Explicit, task-scoped local planning projection inputs."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    task_id: str = Field(alias="taskId", min_length=1, max_length=128)
    projection_key: str = Field(alias="projectionKey", min_length=1, max_length=200)
    expected_planning_version: StrictInt = Field(alias="expectedPlanningVersion", ge=0)
    remaining_person_hours: RemainingPersonHoursBody | None = Field(
        default=None, alias="remainingPersonHours"
    )
    material_available_at: datetime | None = Field(default=None, alias="materialAvailableAt")
    earliest_start_at: datetime | None = Field(default=None, alias="earliestStartAt")

    @field_validator("material_available_at", "earliest_start_at", mode="before")
    @classmethod
    def require_iso_datetime_input(cls, value: object) -> object:
        if value is not None and not isinstance(value, str):
            raise ValueError("planning dates must be ISO-8601 strings")
        return value


class EvidenceReviewBody(BaseModel):
    """Review input with an explicit, bounded local planning projection."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    status: str = Field(min_length=1, max_length=32)
    source_revision: str | None = Field(default=None, alias="sourceRevision", max_length=256)
    note: str | None = Field(default=None, max_length=2_000)
    planning_change: PlanningChangeBody | None = Field(default=None, alias="planningChange")


@router.get("/organizations/{organization_id}/imports/template")
def download_import_template(
    organization_id: str,
    request: Request,
    format: str = Query(default="xlsx"),
    _session: AuthenticatedSession = Depends(READ),
) -> Response:
    """Download the static import workbook or a ZIP containing entity CSV templates."""

    del organization_id, request
    try:
        content, media_type, filename = template_bytes(format)
    except ImportServiceError as exc:
        raise _service_http_error(exc) from exc
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/organizations/{organization_id}/imports/preview")
def preview_import_route(
    organization_id: str,
    request: Request,
    files: list[UploadFile] = File(...),
    session: AuthenticatedSession = Depends(PREVIEW_WRITE),
) -> dict[str, object]:
    """Parse uploads and persist a preview; no Timecue write is possible here."""

    if len(files) > MAX_IMPORT_FILES:
        raise HTTPException(status_code=413, detail="import_file_count_limit_exceeded")
    uploads: list[UploadedImport] = []
    total_bytes = 0
    for file in files:
        remaining = MAX_IMPORT_BYTES - total_bytes
        if remaining < 0:
            raise HTTPException(status_code=413, detail="import_file_too_large")
        content = file.file.read(remaining + 1)
        total_bytes += len(content)
        if len(content) > remaining:
            raise HTTPException(status_code=413, detail="import_file_too_large")
        uploads.append(
            UploadedImport(
                filename=file.filename or "upload",
                content=content,
                content_type=file.content_type,
            )
        )
    snapshot = _load_snapshot(request, session, organization_id)
    members = _load_members(request, session, organization_id, snapshot)
    try:
        return preview_import(
            request.app.state.store,
            organization_id,
            uploads,
            member_rows=members,
            source_revision=snapshot.source_revision,
            current_snapshot=snapshot.model_dump(mode="json", by_alias=True),
        )
    except ImportServiceError as exc:
        raise _service_http_error(exc) from exc


@router.get("/organizations/{organization_id}/imports/{import_id}")
def get_import(
    organization_id: str,
    import_id: str,
    request: Request,
    _session: AuthenticatedSession = Depends(READ),
) -> dict[str, object]:
    """Return one tenant-scoped import ledger without upstream calls."""

    record = request.app.state.store.get_record(organization_id, "import_batches", import_id)
    if record is None:
        raise HTTPException(status_code=404, detail="import_not_found")
    return record


@router.post("/organizations/{organization_id}/imports/{import_id}/commit")
def commit_import_route(
    organization_id: str,
    import_id: str,
    request: Request,
    body: dict[str, object] | None = Body(default=None),
    session: AuthenticatedSession = Depends(IMPORT_WRITE),
) -> dict[str, object]:
    """Commit an explicitly reviewed batch using supported routes and readbacks."""

    payload = body or {}
    upstream = None
    if request.app.state.settings.data_mode == "live":
        upstream = TimecueRouteClient(request.app.state.adapter, session.record)
    try:
        return commit_import(
            request.app.state.store,
            organization_id,
            import_id,
            upstream=upstream,
            actor_id=session.user.id,
            confirm_planning=bool(payload.get("confirmPlanning", False)),
            expected_preview_hash=_string(payload.get("previewHash")),
            resume=bool(payload.get("resume", False)),
        )
    except ImportServiceError as exc:
        raise _service_http_error(exc) from exc


@router.get("/organizations/{organization_id}/knowledge")
def get_knowledge(
    organization_id: str,
    request: Request,
    project_id: str | None = Query(default=None, alias="projectId"),
    _session: AuthenticatedSession = Depends(READ),
) -> dict[str, object]:
    """Read documents, upload claims and separate worklog signals only."""

    result = list_knowledge(
        request.app.state.store,
        organization_id,
        request.app.state.settings,
        project_id=project_id,
    )
    result["signals"] = request.app.state.store.list_signals(organization_id, project_id)
    from src.app.services.knowledge_embeddings import semantic_links

    result["semanticLinks"] = semantic_links(
        request.app.state.store, organization_id, result["documents"]
    )
    return result


class KnowledgeSearchBody(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    project_id: str | None = Field(default=None, alias="projectId")


@router.post("/organizations/{organization_id}/knowledge/search")
def search_knowledge(
    organization_id: str,
    body: KnowledgeSearchBody,
    request: Request,
    session: AuthenticatedSession = Depends(KNOWLEDGE_WRITE),
) -> dict[str, object]:
    from src.app.services.knowledge_embeddings import semantic_search

    knowledge = list_knowledge(
        request.app.state.store,
        organization_id,
        request.app.state.settings,
        project_id=body.project_id,
    )
    return semantic_search(
        request.app.state.store,
        organization_id,
        request.app.state.settings,
        body.query.strip(),
        knowledge["documents"],
    )


@router.post("/organizations/{organization_id}/knowledge/documents")
def upload_knowledge_document(
    organization_id: str,
    request: Request,
    file: UploadFile = File(...),
    project_id: str | None = Form(default=None, alias="projectId"),
    captured_at: str | None = Form(default=None, alias="capturedAt"),
    known_at: str | None = Form(default=None, alias="knownAt"),
    session: AuthenticatedSession = Depends(KNOWLEDGE_WRITE),
) -> dict[str, object]:
    """Store and extract one text, PDF, Word, or image document for this organization."""

    if project_id:
        snapshot = _load_snapshot(request, session, organization_id)
        project_ids = {str(project.get("id")) for project in snapshot.projects}
        if project_id not in project_ids:
            raise HTTPException(status_code=422, detail="project_not_in_organization")
    content = file.file.read(MAX_KNOWLEDGE_BYTES + 1)
    from src.app.services.knowledge_jobs import enqueue_knowledge

    try:
        return enqueue_knowledge(
            request.app.state,
            organization_id,
            session.record.session_id,
            UploadedKnowledge(
                filename=file.filename or "upload",
                content=content,
                content_type=file.content_type,
            ),
            projectId=project_id,
            capturedAt=captured_at,
            knownAt=known_at,
        )
    except KnowledgeServiceError as exc:
        raise _service_http_error(exc) from exc


@router.delete("/organizations/{organization_id}/knowledge/documents/{document_id}")
def delete_knowledge_document(
    organization_id: str,
    document_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(KNOWLEDGE_WRITE),
) -> dict[str, object]:
    from src.app.services.knowledge import delete_knowledge

    try:
        return delete_knowledge(
            request.app.state.store,
            organization_id,
            document_id,
            kind="document",
            actor_id=session.user.id,
        )
    except KnowledgeServiceError as exc:
        raise _service_http_error(exc) from exc


@router.delete("/organizations/{organization_id}/knowledge/claims/{claim_id}")
def delete_knowledge_claim(
    organization_id: str,
    claim_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(KNOWLEDGE_WRITE),
) -> dict[str, object]:
    from src.app.services.knowledge import delete_knowledge

    try:
        return delete_knowledge(
            request.app.state.store,
            organization_id,
            claim_id,
            kind="claim",
            actor_id=session.user.id,
        )
    except KnowledgeServiceError as exc:
        raise _service_http_error(exc) from exc


@router.post("/organizations/{organization_id}/evidence/{claim_id}/review")
def review_knowledge_claim(
    organization_id: str,
    claim_id: str,
    body: EvidenceReviewBody,
    request: Request,
    session: AuthenticatedSession = Depends(KNOWLEDGE_WRITE),
) -> dict[str, object]:
    """Review a claim and, when explicitly confirmed, project approved fields locally."""

    if body.planning_change is not None:
        if body.status != "confirmed":
            raise HTTPException(
                status_code=422,
                detail="planning_change_requires_confirmed_status",
            )
        snapshot = _load_snapshot(request, session, organization_id)
        try:
            return apply_confirmed_claim_projection(
                request.app.state.store,
                organization_id,
                claim_id,
                body.planning_change.model_dump(mode="json", by_alias=True, exclude_none=True),
                snapshot.model_dump(mode="json", by_alias=True),
                actor_id=session.user.id,
                source_revision=body.source_revision,
            )
        except KnowledgeServiceError as exc:
            raise _service_http_error(exc) from exc
    try:
        return review_claim(
            request.app.state.store,
            organization_id,
            claim_id,
            body.status,
            actor_id=session.user.id,
            source_revision=body.source_revision,
            note=body.note,
        )
    except KnowledgeServiceError as exc:
        raise _service_http_error(exc) from exc


def _load_snapshot(
    request: Request, session: AuthenticatedSession, organization_id: str
) -> PortfolioSnapshot:
    try:
        return request.app.state.adapter.snapshot(session.record, organization_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except UpstreamIntegrationError as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": str(exc), "source": "timecue"},
        ) from exc


def _load_members(
    request: Request,
    session: AuthenticatedSession,
    organization_id: str,
    snapshot: PortfolioSnapshot,
) -> list[Mapping[str, object]]:
    """Load exact Timecue members for import matching.

    Live mode reads organization members and joins existing worker-profile IDs.
    Fixture mode uses snapshot workers as the member directory and never invents
    accounts.
    """
    if request.app.state.settings.data_mode != "live":
        return [
            {
                **dict(worker),
                "id": worker.get("memberId") or worker.get("id"),
                "workerProfileId": worker.get("workerProfileId") or worker.get("id"),
            }
            for worker in snapshot.workers
        ]
    client = TimecueRouteClient(request.app.state.adapter, session.record)
    try:
        payload = client.request("GET", f"/organizations/{organization_id}/members")
        workers_payload = client.request(
            "GET",
            f"/organizations/{organization_id}/workforce/workers",
            params={"limit": 100, "offset": 0},
        )
    except ImportServiceError as exc:
        raise _service_http_error(exc) from exc
    members = _mapping_items(payload)
    if members is None:
        raise HTTPException(status_code=502, detail="timecue_members_response_shape_invalid")
    return _attach_member_worker_profiles(members, _mapping_items(workers_payload) or [])


def _mapping_items(payload: object) -> list[Mapping[str, object]] | None:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping) and isinstance(payload.get("items"), list):
        return [item for item in payload["items"] if isinstance(item, Mapping)]
    return None


def _attach_member_worker_profiles(
    members: Sequence[Mapping[str, object]],
    workers: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Join member rows to existing worker profiles without inventing accounts."""

    profiles: dict[str, str] = {}
    for worker in workers:
        member_id = worker.get("organizationMemberId") or worker.get("memberId")
        profile_id = worker.get("id") or worker.get("workerProfileId")
        if isinstance(member_id, str) and member_id and isinstance(profile_id, str) and profile_id:
            profiles[member_id] = profile_id
    attached: list[dict[str, object]] = []
    for member in members:
        item = dict(member)
        member_id = item.get("id")
        if not item.get("workerProfileId") and isinstance(member_id, str):
            profile_id = profiles.get(member_id)
            if profile_id:
                item["workerProfileId"] = profile_id
        attached.append(item)
    return attached


def _service_http_error(exc: ImportServiceError | KnowledgeServiceError) -> HTTPException:
    detail: object = {"code": exc.code, "message": exc.message}
    if isinstance(exc, ImportServiceError) and exc.details:
        detail = {**detail, "items": exc.details}
    return HTTPException(status_code=exc.status_code, detail=detail)


def _string(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


__all__ = ["data_router", "router"]
