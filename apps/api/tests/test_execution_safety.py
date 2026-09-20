"""Focused tests for the pure execution safety helpers."""

import hashlib
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.app.api import v4_routes
from src.app.api.v4_routes import _continue_execution
from src.app.integrations.timecue import UpstreamIntegrationError
from src.app.persistence.store import Store
from src.app.services.execution import (
    ReconciliationStatus,
    classify_reconciliation,
    compare_readback_fields,
    execution_request_fingerprint,
    normalize_timestamp,
    readback_field_mismatches,
    step_dependencies,
)


class _FakeTaskAdapter:
    def __init__(self, current: dict[str, object], *, timeout_after_patch: bool = False):
        self.current = dict(current)
        self.timeout_after_patch = timeout_after_patch
        self.patch_calls = 0

    def _get(self, _session: object, _path: str) -> dict[str, object]:
        return dict(self.current)

    def _request(self, _session: object, method: str, _path: str, **kwargs: object) -> None:
        assert method == "PATCH"
        self.patch_calls += 1
        body = kwargs.get("json")
        assert isinstance(body, dict)
        self.current.update(body)
        if self.timeout_after_patch:
            self.timeout_after_patch = False
            raise UpstreamIntegrationError("timecue_write_timeout", 504)


@pytest.fixture
def execution_context():
    store = Store("sqlite:///:memory:")
    store.migrate_schema()
    adapter = _FakeTaskAdapter(
        {
            "id": "task-1",
            "plannedStartAt": "2026-09-21T08:00:00Z",
            "plannedEndAt": "2026-09-21T10:00:00Z",
        }
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(store=store, adapter=adapter, settings=SimpleNamespace())
        )
    )
    session = SimpleNamespace(
        record=SimpleNamespace(),
        user=SimpleNamespace(
            id="manager-1",
            app_admin=True,
            organization_permissions={"org-1": ["tasks.write"]},
        ),
    )
    yield store, request, session, adapter
    store.engine.dispose()


def _task_execution() -> dict[str, object]:
    return {
        "analysisId": "analysis-1",
        "sourceRevision": "source-1",
        "assumptionsVersion": 1,
        "status": "applying",
        "steps": [
            {
                "id": "date-step",
                "kind": "api",
                "status": "pending",
                "action": {"type": "date_shift", "taskId": "task-1"},
                "path": "/organizations/org-1/projects/project-1/tasks/task-1",
                "before": {
                    "plannedStartAt": "2026-09-21T08:00:00Z",
                    "plannedEndAt": "2026-09-21T10:00:00Z",
                },
                "body": {
                    "plannedStartAt": "2026-09-21T09:00:00Z",
                    "plannedEndAt": "2026-09-21T11:00:00Z",
                },
                "permission": "tasks.write",
            }
        ],
    }


def test_readback_comparison_normalizes_timestamp_offsets_but_not_other_values():
    expected = {
        "plannedStartAt": "2026-09-21T08:00:00Z",
        "plannedEndAt": "2026-09-21T10:00:00+00:00",
        "status": "planned",
    }
    actual = {
        "plannedStartAt": "2026-09-21T10:00:00+02:00",
        "plannedEndAt": "2026-09-21T12:00:00+02:00",
        "status": "planned",
    }

    assert compare_readback_fields(expected, actual)
    assert readback_field_mismatches(expected, {**actual, "status": "assigned"}) == ("status",)
    assert normalize_timestamp("2026-09-21T08:00:00Z") == normalize_timestamp(
        "2026-09-21T10:00:00+02:00"
    )
    assert normalize_timestamp("2026-09-21T08:00:00") != normalize_timestamp(
        "2026-09-21T08:00:00Z"
    )


def test_readback_comparison_requires_missing_and_type_changed_fields():
    assert readback_field_mismatches({"status": "planned"}, {}) == ("status",)
    assert not compare_readback_fields({"count": 1}, {"count": True})


def test_reconciliation_classifies_applied_unchanged_and_conflict():
    before = {"plannedStartAt": "2026-09-21T08:00:00Z", "plannedEndAt": "2026-09-21T10:00:00Z"}
    desired = {"plannedStartAt": "2026-09-21T09:00:00Z", "plannedEndAt": "2026-09-21T11:00:00Z"}

    assert classify_reconciliation(before, desired, desired) is ReconciliationStatus.APPLIED
    assert classify_reconciliation(before, desired, before) is ReconciliationStatus.UNCHANGED
    assert (
        classify_reconciliation(
            before,
            desired,
            {"plannedStartAt": "2026-09-21T09:30:00Z", "plannedEndAt": "2026-09-21T11:00:00Z"},
        )
        is ReconciliationStatus.CONFLICT
    )


def test_date_shifts_depend_on_manual_transfer_material_and_weather_steps():
    steps = [
        {"id": "transfer", "kind": "manual", "action": {"type": "transfer"}},
        {"id": "material", "kind": "manual", "action": {"type": "material_shift"}},
        {"id": "weather", "kind": "manual", "action": {"type": "weather_shift"}},
        {"id": "shift", "kind": "api", "action": {"type": "date_shift"}},
        {"id": "api-material", "kind": "api", "action": {"type": "material_shift"}},
    ]

    dependencies = step_dependencies(steps)

    assert dependencies["shift"] == ("transfer", "material", "weather")
    assert dependencies["transfer"] == ()
    assert dependencies["api-material"] == ()


def test_step_dependencies_reject_duplicate_ids():
    steps = [
        {"id": "same", "kind": "manual", "action": {"type": "transfer"}},
        {"id": "same", "kind": "api", "action": {"type": "date_shift"}},
    ]

    with pytest.raises(ValueError, match="duplicate"):
        step_dependencies(steps)


def test_request_fingerprint_includes_all_approved_request_fields():
    analysis_id = "analysis-1"
    scenario_id = "scenario-1"
    review_hash = "a" * 64
    expected_payload = json.dumps(
        {"analysisId": analysis_id, "scenarioId": scenario_id, "reviewHash": review_hash},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    assert execution_request_fingerprint(analysis_id, scenario_id, review_hash) == hashlib.sha256(
        expected_payload
    ).hexdigest()
    assert execution_request_fingerprint(analysis_id, "scenario-2", review_hash) != (
        execution_request_fingerprint(analysis_id, scenario_id, review_hash)
    )
    assert execution_request_fingerprint("analysis-2", scenario_id, review_hash) != (
        execution_request_fingerprint(analysis_id, scenario_id, review_hash)
    )
    assert execution_request_fingerprint(analysis_id, scenario_id, "b" * 64) != (
        execution_request_fingerprint(analysis_id, scenario_id, review_hash)
    )


def test_timeout_readback_reconciliation_does_not_duplicate_patch(execution_context):
    store, request, session, adapter = execution_context
    adapter.timeout_after_patch = True
    execution = store.save_record("org-1", "execution", "execution-1", _task_execution())

    token = store.acquire_lease("org-1", "execution")
    assert token
    first = _continue_execution("org-1", "execution-1", execution, request, session, token)
    assert first["steps"][0]["status"] == "needs_reconciliation"
    assert adapter.patch_calls == 1
    store.release_lease("org-1", "execution", token)

    token = store.acquire_lease("org-1", "execution")
    assert token
    reconciled = _continue_execution(
        "org-1", "execution-1", first, request, session, token, write=False
    )

    assert reconciled["steps"][0]["status"] == "applied"
    assert adapter.patch_calls == 1


def test_api_step_stays_blocked_until_manual_prerequisite_is_confirmed(execution_context):
    store, request, session, adapter = execution_context
    execution = _task_execution()
    execution["steps"].insert(
        0,
        {
            "id": "transfer-step",
            "kind": "manual",
            "status": "pending",
            "action": {"type": "transfer", "workerId": "worker-1"},
        },
    )
    execution["steps"][1]["dependsOn"] = ["transfer-step"]
    execution = store.save_record("org-1", "execution", "execution-1", execution)

    token = store.acquire_lease("org-1", "execution")
    assert token
    blocked = _continue_execution("org-1", "execution-1", execution, request, session, token)
    assert blocked["steps"][1]["status"] == "blocked"
    assert adapter.patch_calls == 0

    blocked["steps"][0]["status"] = "confirmed"
    store.save_record("org-1", "execution", "execution-1", blocked)
    resumed = _continue_execution("org-1", "execution-1", blocked, request, session, token)

    assert resumed["steps"][1]["status"] == "applied"
    assert adapter.patch_calls == 1


def test_reconcile_rejects_unapproved_source_change_with_409(execution_context, monkeypatch):
    store, request, session, _adapter = execution_context
    task = {
        "id": "task-1",
        "projectId": "project-1",
        "name": "Original",
        "plannedStartAt": "2026-09-21T08:00:00Z",
        "plannedEndAt": "2026-09-21T10:00:00Z",
        "updatedAt": "2026-09-20T08:00:00Z",
    }
    baseline = SimpleNamespace(
        tasks=[task],
        projects=[{"id": "project-1", "name": "Project"}],
        workers=[],
        reservations=[],
        effective_assignments=[],
    )
    steps = _task_execution()["steps"]
    steps[0]["status"] = "applied"
    execution = _task_execution()
    execution["steps"] = steps
    execution["sourceInvariant"] = v4_routes._source_invariant(baseline, steps)
    store.save_record("org-1", "execution", "execution-1", execution)

    date_only_change = SimpleNamespace(
        tasks=[
            {
                **task,
                "plannedStartAt": "2026-09-21T09:00:00Z",
                "plannedEndAt": "2026-09-21T11:00:00Z",
                "updatedAt": "2026-09-20T09:00:00Z",
            }
        ],
        projects=baseline.projects,
        workers=[],
        reservations=[],
        effective_assignments=[],
    )
    assert v4_routes._source_invariant(date_only_change, steps) == execution["sourceInvariant"]

    changed = SimpleNamespace(
        tasks=[{**date_only_change.tasks[0], "name": "Unapproved edit"}],
        projects=baseline.projects,
        workers=[],
        reservations=[],
        effective_assignments=[],
    )
    monkeypatch.setattr(v4_routes, "_load_snapshot", lambda *_args: changed)

    with pytest.raises(HTTPException) as error:
        v4_routes.reconcile_execution("org-1", "execution-1", request, session)

    assert error.value.status_code == 409
    assert error.value.detail == "source_changed_new_review_required"
