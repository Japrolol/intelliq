"""Focused coverage for bounded v4 imports, knowledge, and protected routes."""

from __future__ import annotations

import base64
import csv
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from xml.sax.saxutils import escape

import httpx
import pytest
from fastapi.testclient import TestClient

from src.app.config import Settings
from src.app.main import create_app
from src.app.persistence.store import Store
from src.app.services import knowledge
from src.app.services.imports import (
    IMPORT_COMMIT_LEASE,
    MAX_IMPORT_FILES,
    ImportServiceError,
    UploadedImport,
    commit_import,
    preview_import,
    template_bytes,
)
from src.app.services.knowledge import (
    KnowledgeServiceError,
    UploadedKnowledge,
    ingest_document,
    review_claim,
)

ROOT = Path(__file__).resolve().parents[3]
IMPORT_FIXTURES = ROOT / "fixtures" / "imports"
ENTITY_FILES = ("projects", "tasks", "workers", "skills", "assignments", "planning")


def _store() -> Store:
    store = Store("sqlite:///:memory:")
    store.migrate_schema()
    return store


def _members() -> list[dict[str, str]]:
    seen: list[str] = []
    with (IMPORT_FIXTURES / "workers.csv").open(newline="", encoding="utf-8") as source:
        for row in csv.DictReader(source):
            member_id = row["timecueMemberId"].strip()
            if member_id and member_id not in seen:
                seen.append(member_id)
    return [
        {"id": member_id, "workerProfileId": f"profile-{index:03d}"}
        for index, member_id in enumerate(seen, start=1)
    ]


def _uploads() -> list[UploadedImport]:
    return [
        UploadedImport(
            f"{entity}.csv",
            (IMPORT_FIXTURES / f"{entity}.csv").read_bytes(),
            "text/csv",
        )
        for entity in ENTITY_FILES
    ]


def _snapshot_with_specialties() -> dict[str, object]:
    with (IMPORT_FIXTURES / "skills.csv").open(newline="", encoding="utf-8") as source:
        specialty_ids = [
            row["timecueSpecialtyId"].strip()
            for row in csv.DictReader(source)
            if row.get("timecueSpecialtyId", "").strip()
        ]
    return {"specialties": [{"id": specialty_id} for specialty_id in specialty_ids]}


class RecordingTimecue:
    """Small existing-route fake with canonical collection/readback behavior."""

    def __init__(self, *, drop_assignment_readback: bool = False) -> None:
        self.projects: list[dict[str, object]] = []
        self.tasks: dict[str, list[dict[str, object]]] = {}
        self.assignments: dict[str, dict[str, object]] = {}
        self.calls: list[tuple[str, str, object]] = []
        self.drop_assignment_readback = drop_assignment_readback

    def request(
        self,
        method: str,
        path: str,
        *,
        params: object = None,
        json_body: dict[str, object] | None = None,
    ) -> object:
        del params
        self.calls.append((method, path, json_body))
        if method == "POST" and path.endswith("/projects"):
            project_id = f"project-{len(self.projects) + 1}"
            project = {"id": project_id, **(json_body or {})}
            self.projects.append(project)
            self.tasks[project_id] = []
            return project
        if method == "GET" and path.endswith("/projects"):
            return self.projects
        if method == "PATCH" and "/projects/" in path and "/tasks/" in path:
            project_id = path.split("/projects/", 1)[1].split("/tasks/", 1)[0]
            task_id = path.rsplit("/tasks/", 1)[1]
            task = next(item for item in self.tasks[project_id] if str(item["id"]) == task_id)
            task.update(json_body or {})
            return task
        if method == "POST" and "/projects/" in path and path.endswith("/tasks"):
            project_id = path.split("/projects/", 1)[1].split("/tasks", 1)[0]
            task_id = f"task-{len(self.tasks[project_id]) + 1}-{project_id}"
            task = {"id": task_id, **(json_body or {})}
            self.tasks[project_id].append(task)
            return task
        if method == "GET" and "/projects/" in path and path.endswith("/tasks"):
            project_id = path.split("/projects/", 1)[1].split("/tasks", 1)[0]
            return self.tasks[project_id]
        if path.endswith("/assignments") and method == "GET":
            value = self.assignments.setdefault(
                path,
                {
                    "directWorkers": ["existing-worker"],
                    "teams": ["existing-team"],
                    "effectiveWorkers": [{"id": "existing-worker"}],
                },
            )
            if self.drop_assignment_readback:
                return {**value, "effectiveWorkers": []}
            return value
        if path.endswith("/assignments") and method == "PUT":
            value = {
                "directWorkers": (json_body or {}).get("workerProfileIds", []),
                "teams": (json_body or {}).get("teamIds", []),
                "effectiveWorkers": [
                    {"id": worker_id} for worker_id in (json_body or {}).get("workerProfileIds", [])
                ],
            }
            self.assignments[path] = value
            return value
        raise AssertionError(f"unexpected fake Timecue request: {method} {path}")


def test_import_preview_has_stable_keys_member_matches_and_xlsx_template() -> None:
    store = _store()
    record = preview_import(
        store,
        "org-a",
        _uploads(),
        member_rows=_members(),
        current_snapshot=_snapshot_with_specialties(),
    )

    expected_rows = 0
    for entity in ENTITY_FILES:
        with (IMPORT_FIXTURES / f"{entity}.csv").open(newline="", encoding="utf-8") as source:
            expected_rows += max(0, sum(1 for _ in csv.DictReader(source)))

    assert record["status"] == "ready"
    assert record["counts"]["total"] == expected_rows
    assert MAX_IMPORT_FILES == 16
    transfer = next(
        row for row in record["rows"] if row["entity"] == "planning" and "transfer" in row["rowKey"]
    )
    assert transfer["rowKey"].startswith("planning:transfer:project-beta:project-alpha:worker-02")
    worker = next(row for row in record["rows"] if row["entity"] == "workers")
    assert worker["memberMatch"]["status"] == "matched"
    assert worker["mapping"]["workerProfileId"] == "profile-001"

    content, media_type, filename = template_bytes("xlsx")
    assert media_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert filename.endswith(".xlsx")
    assert content.startswith(b"PK")


def test_mock_portfolio_preview_keeps_custom_keys_and_unmatched_members() -> None:
    folder = IMPORT_FIXTURES / "mock"
    uploads = [
        UploadedImport(
            f"{entity}.csv",
            (folder / f"{entity}.csv").read_bytes(),
            "text/csv",
        )
        for entity in ENTITY_FILES
    ]
    record = preview_import(_store(), "org-a", uploads)
    names = {
        str(row["values"]["name"])
        for row in record["rows"]
        if row["entity"] == "projects"
    }
    assert names == {
        "Harbor Clinic Expansion",
        "Nowy Targ School Gym",
        "Widzew Cold Storage Retrofit",
    }
    transfer = next(
        row
        for row in record["rows"]
        if row["entity"] == "planning" and "transfer" in str(row["rowKey"])
    )
    assert transfer["rowKey"].startswith(
        "planning:transfer:project-harbor-clinic:project-school-gym:worker-bartek"
    )
    worker = next(row for row in record["rows"] if row["entity"] == "workers")
    assert worker["memberMatch"]["status"] == "unmatched"

    xlsx = preview_import(
        _store(),
        "org-a",
        [
            UploadedImport(
                "mock-portfolio.xlsx",
                (IMPORT_FIXTURES / "mock-portfolio.xlsx").read_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        ],
    )
    assert {
        str(row["values"]["name"]) for row in xlsx["rows"] if row["entity"] == "projects"
    } == names


def test_import_commit_readbacks_and_confirmed_planning_overlay() -> None:
    store = _store()
    preview = preview_import(
        store,
        "org-a",
        _uploads(),
        member_rows=_members(),
        current_snapshot=_snapshot_with_specialties(),
    )
    upstream = RecordingTimecue()

    result = commit_import(
        store,
        "org-a",
        str(preview["id"]),
        upstream=upstream,
        actor_id="manager-1",
        confirm_planning=True,
    )

    assert result["status"] == "applied"
    assert result["planningStatus"] == "confirmed"
    assert result["planningMerge"]["status"] == "applied"
    planning = store.get_planning("org-a")
    assert planning is not None
    assert planning["version"] == 1
    project = next(
        item
        for item in planning["projects"]
        if item.get("location") == {"latitude": 52.2374, "longitude": 21.0295}
    )
    assert project["targetFinishAt"]
    assert project["timezone"] == "Europe/Warsaw"
    task = next(item for item in planning["tasks"] if item.get("predecessorIds"))
    assert task["remainingPersonHours"]["optimistic"] <= task["remainingPersonHours"]["mostLikely"]
    assert task["remainingPersonHours"]["mostLikely"] <= task["remainingPersonHours"]["pessimistic"]
    assert task["predecessorIds"]
    assert planning["transfers"][0]["confirmed"] is True
    assert "existing-team" in next(iter(upstream.assignments.values()))["teams"]
    project_create = next(
        body
        for method, path, body in upstream.calls
        if method == "POST" and path.endswith("/projects")
    )
    assert project_create["status"] == "scheduled"
    assert project_create["currency"] == "PLN"
    assert project_create["countryCode"] == "PL"
    assert project_create["timezone"] == "Europe/Warsaw"

    call_count = len(upstream.calls)
    again = commit_import(
        store, "org-a", str(preview["id"]), upstream=upstream, confirm_planning=True
    )
    assert again["status"] == "applied"
    assert len(upstream.calls) == call_count


def test_spreadsheet_planned_status_maps_to_timecue_project_enum() -> None:
    from src.app.services.imports import _project_create_body

    body = _project_create_body(
        {"name": "Riverside", "timezone": "Europe/Warsaw", "status": "planned"}
    )
    assert body["status"] == "scheduled"
    assert body["currency"] == "PLN"
    assert body["countryCode"] == "PL"


def test_import_partial_readback_never_confirms_planning() -> None:
    store = _store()
    preview = preview_import(
        store,
        "org-a",
        _uploads(),
        member_rows=_members(),
        current_snapshot=_snapshot_with_specialties(),
    )
    result = commit_import(
        store,
        "org-a",
        str(preview["id"]),
        upstream=RecordingTimecue(drop_assignment_readback=True),
        confirm_planning=True,
    )

    assert result["status"] == "partially_applied"
    assert result["planningStatus"] == "proposed"
    assert result["planningMerge"]["code"] == "upstream_receipts_incomplete"
    assert store.get_planning("org-a") is None


def test_import_updates_mapped_task_dates_through_verified_patch() -> None:
    store = _store()
    uploads = [
        UploadedImport(
            "projects.csv",
            (
                b"externalKey,name,timezone,address,targetFinishAt,timecueProjectId\n"
                b"p1,Existing Project,Europe/Warsaw,Street,2026-10-01T17:00:00+02:00,up-project\n"
            ),
            "text/csv",
        ),
        UploadedImport(
            "tasks.csv",
            (
                b"externalKey,projectKey,title,plannedStart,plannedEnd,timecueTaskId\n"
                b"t1,p1,Existing Task,2026-09-21T08:00:00+02:00,"
                b"2026-09-21T16:00:00+02:00,up-task\n"
            ),
            "text/csv",
        ),
    ]
    preview = preview_import(
        store,
        "org-a",
        uploads,
        current_snapshot={
            "projects": [{"id": "up-project"}],
            "tasks": [{"id": "up-task", "projectId": "up-project"}],
        },
    )
    upstream = RecordingTimecue()
    upstream.projects = [{"id": "up-project"}]
    upstream.tasks["up-project"] = [
        {
            "id": "up-task",
            "projectId": "up-project",
            "plannedStartAt": "2026-09-20T08:00:00+02:00",
            "plannedEndAt": "2026-09-20T16:00:00+02:00",
        }
    ]

    result = commit_import(
        store,
        "org-a",
        str(preview["id"]),
        upstream=upstream,
        actor_id="manager-1",
    )

    task_receipt = next(receipt for receipt in result["receipts"] if receipt["entity"] == "tasks")
    assert task_receipt["status"] == "applied"
    assert task_receipt["precondition"]["plannedStartAt"].startswith("2026-09-20")
    assert any(
        method == "PATCH" and path.endswith("/tasks/up-task")
        for method, path, _body in upstream.calls
    )
    assert upstream.tasks["up-project"][0]["plannedStartAt"].startswith("2026-09-21")


def test_import_lease_rejects_concurrent_commit() -> None:
    store = _store()
    preview = preview_import(store, "org-a", [_uploads()[0]])
    token = store.acquire_lease("org-a", IMPORT_COMMIT_LEASE)
    assert token
    try:
        with pytest.raises(ImportServiceError, match="already running") as error:
            commit_import(store, "org-a", str(preview["id"]), upstream=RecordingTimecue())
        assert error.value.code == "import_commit_in_progress"
    finally:
        store.release_lease("org-a", IMPORT_COMMIT_LEASE, token)


def _knowledge_settings(tmp_path: Path, *, provider: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        upload_dir=str(tmp_path),
        nlp_enabled=True,
        allow_external_text_processing=provider,
        llm_api_key="test-key" if provider else "",
        llm_api_base_url="https://provider.test/v1" if provider else "",
        llm_model="test-model" if provider else "",
    )


def test_knowledge_text_cache_provenance_and_stale_review_are_explicit(tmp_path: Path) -> None:
    store = _store()
    settings = _knowledge_settings(tmp_path)
    first = ingest_document(
        store,
        "org-a",
        UploadedKnowledge("field-note.txt", b"18 cable trays delivered", "text/plain"),
        settings,
        project_id="project-alpha",
    )
    old_claim = first["claims"][0]
    assert old_claim["status"] == "proposed"
    assert old_claim["source"]["contentHash"] == first["contentHash"]
    assert old_claim["source"]["charEnd"] > old_claim["source"]["charStart"]
    review_claim(store, "org-a", old_claim["id"], "confirmed", actor_id="manager-1")

    second = ingest_document(
        store,
        "org-a",
        UploadedKnowledge("field-note.txt", b"22 cable trays delivered", "text/plain"),
        settings,
        project_id="project-alpha",
    )
    assert second["version"] == 2
    assert store.get_record("org-a", "knowledge_claims", old_claim["id"])["status"] == "superseded"
    assert store.get_record("org-a", "knowledge_claims", old_claim["id"])["active"] is False
    with pytest.raises(KnowledgeServiceError, match="superseded"):
        review_claim(store, "org-a", old_claim["id"], "confirmed", actor_id="manager-1")

    duplicate = ingest_document(
        store,
        "org-a",
        UploadedKnowledge("field-note.txt", b"22 cable trays delivered", "text/plain"),
        settings,
        project_id="project-alpha",
    )
    assert duplicate["extraction"]["cacheHit"] is True
    assert duplicate["sourceRevision"] == second["sourceRevision"]


class ProviderDouble:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.requests: list[dict[str, object]] = []

    def post(self, url: str, **kwargs: object) -> httpx.Response:
        self.requests.append({"url": url, **kwargs})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(self.payload)}}]},
            request=httpx.Request("POST", url),
        )


def test_knowledge_provider_schema_is_closed_and_image_payload_is_normalized(
    tmp_path: Path,
) -> None:
    provider = ProviderDouble(
        {
            "claims": [
                {
                    "assertion": "reported",
                    "reportedState": "delivered",
                    "summary": "A delivery is reported.",
                    "value": 18,
                    "unit": "trays",
                    "entityRefs": [],
                    "quote": None,
                    "page": None,
                    "region": {"x": 0.1, "y": 0.1, "width": 0.5, "height": 0.5},
                }
            ]
        }
    )
    result = ingest_document(
        _store(),
        "org-a",
        UploadedKnowledge(
            "field-note.png",
            (IMPORT_FIXTURES / "annotated_field_note.png").read_bytes(),
            "image/png",
        ),
        _knowledge_settings(tmp_path, provider=True),
        provider_client=provider,
    )
    request = provider.requests[0]
    body = request["json"]
    schema = body["response_format"]["json_schema"]["schema"]
    value_schema = schema["properties"]["claims"]["items"]["properties"]["value"]
    assert value_schema["anyOf"]
    region_schema = schema["properties"]["claims"]["items"]["properties"]["region"]
    region_object = next(item for item in region_schema["anyOf"] if item.get("type") == "object")
    assert region_object["additionalProperties"] is False
    assert set(region_object["required"]) == {"x", "y", "width", "height"}
    message_content = body["messages"][1]["content"]
    encoded = message_content[1]["image_url"]["url"].split(",", 1)[1]
    from PIL import Image

    with Image.open(io.BytesIO(base64.b64decode(encoded))) as normalized:
        assert normalized.width <= 2048
        assert normalized.height <= 2048
        assert not normalized.getexif()
    assert result["claims"][0]["source"]["contentHash"] == result["contentHash"]
    assert result["claims"][0]["source"]["mediaType"] == "image/png"


def test_knowledge_image_rejects_multiple_frames_and_pixel_bombs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PIL import Image

    first = Image.new("RGB", (2, 2), "red")
    second = Image.new("RGB", (2, 2), "blue")
    animated = io.BytesIO()
    first.save(animated, format="GIF", save_all=True, append_images=[second], loop=0)
    with pytest.raises(KnowledgeServiceError, match="Animated"):
        ingest_document(
            _store(),
            "org-a",
            UploadedKnowledge("animated.gif", animated.getvalue(), "image/gif"),
            _knowledge_settings(tmp_path),
        )

    monkeypatch.setattr(knowledge, "MAX_IMAGE_PIXELS", 1)
    with pytest.raises(KnowledgeServiceError, match="dimensions"):
        ingest_document(
            _store(),
            "org-a",
            UploadedKnowledge(
                "field-note.png",
                (IMPORT_FIXTURES / "annotated_field_note.png").read_bytes(),
                "image/png",
            ),
            _knowledge_settings(tmp_path),
        )


def _pdf_bytes(*pages: str) -> bytes:
    objects = ["<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{3 + index} 0 R" for index in range(len(pages)))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>")
    streams: list[str] = []
    font_id = 3 + len(pages) * 2
    for index, text in enumerate(pages):
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        streams.append(f"BT /F1 12 Tf 72 {720 - index * 20} Td ({escaped}) Tj ET")
        content_id = 3 + len(pages) + index
        objects.append(
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Contents {content_id} 0 R /Resources << /Font << /F1 {font_id} 0 R >> >> >>"
        )
    for stream in streams:
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n{body}\nendobj\n".encode("latin-1"))
    xref_start = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        (
            f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF\n"
        ).encode()
    )
    return bytes(output)


def _docx_bytes(text: str) -> bytes:
    word_ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    package_ns = "http://schemas.openxmlformats.org/package/2006/content-types"
    rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    office_rel = (
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{word_ns}">'
        f"<w:body><w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p></w:body>"
        "</w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                f'<Types xmlns="{package_ns}">'
                '<Default Extension="rels" '
                'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/word/document.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.'
                'wordprocessingml.document.main+xml"/>'
                "</Types>"
            ),
        )
        archive.writestr(
            "_rels/.rels",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                f'<Relationships xmlns="{rel_ns}">'
                f'<Relationship Id="rId1" Type="{office_rel}" '
                'Target="word/document.xml"/>'
                "</Relationships>"
            ),
        )
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


def test_knowledge_pdf_and_docx_extract_reviewable_text(tmp_path: Path) -> None:
    settings = _knowledge_settings(tmp_path)
    pdf = ingest_document(
        _store(),
        "org-a",
        UploadedKnowledge(
            "delivery-note.pdf",
            _pdf_bytes("18 cable trays delivered", "waiting on inspection"),
            "application/octet-stream",
        ),
        settings,
    )
    assert pdf["mediaType"] == "application/pdf"
    assert pdf["status"] == "extracted"
    assert pdf["extraction"]["mode"] == "local_lexical"
    assert pdf["claims"]
    assert any(claim["source"].get("page") == 1 for claim in pdf["claims"])
    assert any("cable trays" in str(claim.get("quote") or "") for claim in pdf["claims"])

    scanned = ingest_document(
        _store(),
        "org-a",
        UploadedKnowledge("scan.pdf", _pdf_bytes(""), "application/pdf"),
        settings,
    )
    assert scanned["status"] == "needs_review"
    assert scanned["claims"] == []
    assert scanned["extraction"]["mode"] == "document_without_extractable_text"

    docx = ingest_document(
        _store(),
        "org-a",
        UploadedKnowledge(
            "site-note.docx",
            _docx_bytes("18 cable trays delivered"),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        settings,
    )
    assert docx["mediaType"].endswith("wordprocessingml.document")
    assert docx["claims"]
    assert any("cable trays" in str(claim.get("quote") or "") for claim in docx["claims"])


def test_knowledge_rejects_invalid_or_legacy_office_uploads(tmp_path: Path) -> None:
    settings = _knowledge_settings(tmp_path)
    with pytest.raises(KnowledgeServiceError, match="PDF") as pdf_error:
        ingest_document(
            _store(),
            "org-a",
            UploadedKnowledge("note.pdf", b"not-a-pdf", "application/pdf"),
            settings,
        )
    assert pdf_error.value.code == "knowledge_pdf_invalid"

    with pytest.raises(KnowledgeServiceError, match="Legacy Word") as doc_error:
        ingest_document(
            _store(),
            "org-a",
            UploadedKnowledge("note.doc", b"OLE", "application/msword"),
            settings,
        )
    assert doc_error.value.code == "knowledge_file_type_unsupported"

    with pytest.raises(KnowledgeServiceError, match="PDF, Word") as type_error:
        ingest_document(
            _store(),
            "org-a",
            UploadedKnowledge("note.exe", b"MZ", "application/octet-stream"),
            settings,
        )
    assert type_error.value.code == "knowledge_file_type_unsupported"


def test_knowledge_pdf_upload_route_accepts_application_pdf() -> None:
    settings = Settings(data_mode="fixture", database_url="sqlite:///:memory:")
    with TestClient(create_app(settings)) as client:
        login = client.post(
            "/api/auth/login", json={"email": "demo@example.com", "password": "demo"}
        )
        assert login.status_code == 200
        csrf = {"X-CSRF-Token": client.cookies["intelliq_csrf"]}
        upload = client.post(
            "/api/organizations/demo-org/knowledge/documents",
            files={
                "file": (
                    "delivery-note.pdf",
                    _pdf_bytes("18 cable trays delivered"),
                    "application/pdf",
                )
            },
            headers=csrf,
        )
        assert upload.status_code == 200, upload.text
        payload = upload.json()
        assert payload["mediaType"] == "application/pdf"
        assert payload["claims"]
        assert any("cable trays" in str(claim.get("quote") or "") for claim in payload["claims"])


def test_protected_data_router_applies_bounded_confirmed_projection() -> None:
    settings = Settings(data_mode="fixture", database_url="sqlite:///:memory:")
    with TestClient(create_app(settings)) as client:
        login = client.post(
            "/api/auth/login", json={"email": "demo@example.com", "password": "demo"}
        )
        assert login.status_code == 200
        csrf = {"X-CSRF-Token": client.cookies["intelliq_csrf"]}
        template = client.get("/api/organizations/demo-org/imports/template?format=csv")
        assert template.status_code == 200
        assert template.headers["content-type"].startswith("application/zip")
        upload = client.post(
            "/api/organizations/demo-org/knowledge/documents",
            files={"file": ("note.txt", b"18 cable trays delivered", "text/plain")},
            headers=csrf,
        )
        assert upload.status_code == 200, upload.text
        claim_id = upload.json()["claims"][0]["id"]
        review = client.post(
            f"/api/organizations/demo-org/evidence/{claim_id}/review",
            json={
                "status": "confirmed",
                "planningChange": {
                    "taskId": "a1",
                    "projectionKey": f"knowledge:{claim_id}:effort",
                    "expectedPlanningVersion": 0,
                    "remainingPersonHours": {
                        "optimistic": 4,
                        "mostLikely": 8,
                        "pessimistic": 16,
                    },
                    "earliestStartAt": "2026-09-21T08:00:00+02:00",
                },
            },
            headers=csrf,
        )
        assert review.status_code == 200, review.text
        assert review.json()["status"] == "confirmed"
        assert review.json()["planningMutation"]["status"] == "applied"
        assert review.json()["planningMutation"]["sourceRevision"].startswith("sha256:")
        assert review.json()["planningMutation"]["planningVersion"] == 1
        planning = client.get("/api/organizations/demo-org/planning-inputs")
        assert planning.status_code == 200, planning.text
        projected_task = next(task for task in planning.json()["tasks"] if task["id"] == "a1")
        assert projected_task["remainingPersonHours"] == {
            "optimistic": 4.0,
            "mostLikely": 8.0,
            "pessimistic": 16.0,
        }
        assert projected_task["earliestStartAt"] == "2026-09-21T08:00:00+02:00"
        assert projected_task["planningNotes"]["knowledgeProjections"][0][
            "projectionKey"
        ].startswith(f"knowledge:{claim_id}:")

        replay = client.post(
            f"/api/organizations/demo-org/evidence/{claim_id}/review",
            json={
                "status": "confirmed",
                "planningChange": {
                    "taskId": "a1",
                    "projectionKey": f"knowledge:{claim_id}:effort",
                    "expectedPlanningVersion": 0,
                    "remainingPersonHours": {
                        "optimistic": 4,
                        "mostLikely": 8,
                        "pessimistic": 16,
                    },
                    "earliestStartAt": "2026-09-21T08:00:00+02:00",
                },
            },
            headers=csrf,
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["planningMutation"]["status"] == "idempotent_replay"
        assert replay.json()["planningMutation"]["planningVersion"] == 1
        assert client.get("/api/organizations/demo-org/planning-inputs").json()["version"] == 1

        conflict = client.post(
            f"/api/organizations/demo-org/evidence/{claim_id}/review",
            json={
                "status": "confirmed",
                "planningChange": {
                    "taskId": "a1",
                    "projectionKey": f"knowledge:{claim_id}:different",
                    "expectedPlanningVersion": 0,
                    "materialAvailableAt": "2026-09-22T08:00:00+02:00",
                },
            },
            headers=csrf,
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "knowledge_claim_already_projected"

        invalid = client.post(
            f"/api/organizations/demo-org/evidence/{claim_id}/review",
            json={
                "status": "confirmed",
                "planningChange": {
                    "taskId": "a1",
                    "projectionKey": "invalid",
                    "expectedPlanningVersion": 1,
                    "unsupported": True,
                },
            },
            headers=csrf,
        )
        assert invalid.status_code == 422


def test_apply_estimates_uses_timecue_estimated_minutes() -> None:
    from src.app.domain.contracts import PlanningInputs
    from src.app.services.portfolio import (
        SOURCE_TIMECUE_ESTIMATED_MINUTES,
        apply_estimates,
    )

    planning = PlanningInputs(
        version=0,
        tasks=[
            {
                "id": "task-live",
                "title": "Electrical installation",
                "status": "in_progress",
                "estimatedMinutes": 960,
            }
        ],
    )

    assumed = apply_estimates(planning)

    assert planning.tasks[0]["remainingPersonHours"] == {
        "optimistic": 8.0,
        "mostLikely": 16.0,
        "pessimistic": 32.0,
    }
    assert assumed[0]["source"] == SOURCE_TIMECUE_ESTIMATED_MINUTES
    assert assumed[0]["status"] == "estimated"


def test_apply_estimates_uses_planned_window_when_minutes_missing() -> None:
    from src.app.domain.contracts import PlanningInputs
    from src.app.services.portfolio import (
        SOURCE_TIMECUE_PLANNED_WINDOW,
        apply_estimates,
    )

    planning = PlanningInputs(
        version=0,
        tasks=[
            {
                "id": "task-window",
                "status": "planned",
                "plannedStartAt": "2026-09-21T08:00:00+02:00",
                "plannedEndAt": "2026-09-21T16:00:00+02:00",
            }
        ],
    )

    assumed = apply_estimates(planning)

    assert planning.tasks[0]["remainingPersonHours"] == {
        "optimistic": 4.0,
        "mostLikely": 8.0,
        "pessimistic": 16.0,
    }
    assert assumed[0]["source"] == SOURCE_TIMECUE_PLANNED_WINDOW


def test_apply_estimates_keeps_confirmed_overlay_hours() -> None:
    from src.app.domain.contracts import PlanningInputs
    from src.app.services.portfolio import apply_estimates

    remaining = {"optimistic": 16.0, "mostLikely": 24.0, "pessimistic": 32.0}
    planning = PlanningInputs(
        version=1,
        tasks=[
            {
                "id": "task-overlay",
                "status": "planned",
                "estimatedMinutes": 960,
                "plannedStartAt": "2026-09-21T08:00:00+02:00",
                "plannedEndAt": "2026-09-22T16:00:00+02:00",
                "remainingPersonHours": remaining,
            }
        ],
    )

    assumed = apply_estimates(planning)

    assert assumed == []
    assert planning.tasks[0]["remainingPersonHours"] == remaining
