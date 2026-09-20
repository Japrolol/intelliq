"""Focused v4 contracts for simulation presentation and confirmed baselines."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from src.app.simulation.engine import evaluate_portfolio
from src.app.simulation.presentation import build_graph, enrich_result

FIXTURE_PATH = Path(__file__).resolve().parents[3] / "fixtures" / "portfolio-demo.json"


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _single_worker_payload(
    *,
    as_of: str = "2026-09-21T10:00:00+02:00",
    effort: float = 1,
    target_finish: str = "2026-09-21T16:00:00+02:00",
) -> dict:
    return {
        "asOf": as_of,
        "horizonDays": 1,
        "seed": 31,
        "samples": 1,
        "targetProjectId": "alpha",
        "projects": [
            {
                "id": "alpha",
                "name": "Alpha",
                "targetFinishAt": target_finish,
                "priority": 1,
                "timezone": "Europe/Warsaw",
            },
            {
                "id": "beta",
                "name": "Beta",
                "targetFinishAt": target_finish,
                "priority": 1,
                "timezone": "Europe/Warsaw",
            },
        ],
        "tasks": [
            {
                "id": "alpha-task",
                "projectId": "alpha",
                "title": "Alpha task",
                "status": "planned",
                "predecessorIds": [],
                "requiredSpecialtyId": "electrical",
                "remainingPersonHours": {
                    "optimistic": effort,
                    "mostLikely": effort,
                    "pessimistic": effort,
                },
                "minCrew": 1,
                "maxCrew": 1,
                "earliestStartAt": "2026-09-21T08:00:00+02:00",
            },
            {
                "id": "beta-task",
                "projectId": "beta",
                "title": "Beta task",
                "status": "planned",
                "predecessorIds": [],
                "requiredSpecialtyId": "electrical",
                "remainingPersonHours": {
                    "optimistic": effort,
                    "mostLikely": effort,
                    "pessimistic": effort,
                },
                "minCrew": 1,
                "maxCrew": 1,
                "earliestStartAt": "2026-09-21T08:00:00+02:00",
            },
        ],
        "workers": [
            {
                "id": "worker-b",
                "name": "Worker B",
                "specialtyIds": ["electrical"],
                "homeProjectId": "beta",
                "workingWeekdays": [0, 1, 2, 3, 4],
                "shiftStart": "08:00",
                "shiftEnd": "16:00",
                "canTransfer": True,
                "permittedOvertimeSlots": [],
            }
        ],
        "reservations": [],
        "transferAssumptions": [],
    }


def test_action_output_contracts_are_explicit_and_mappable() -> None:
    payload = _fixture()
    payload["samples"] = 1
    payload["candidateActions"] = [
        {
            "id": "date-shift",
            "actions": [
                {
                    "type": "date_shift",
                    "taskId": "a1",
                    "startsAt": "2026-09-21T08:00:00+02:00",
                    "endsAt": "2026-09-22T16:00:00+02:00",
                }
            ],
        },
        {
            "id": "material",
            "actions": [
                {
                    "type": "material",
                    "taskId": "a2",
                    "materialId": "cables",
                    "availableAt": "2026-09-21T08:00:00+02:00",
                    "confirmed": True,
                }
            ],
        },
        {
            "id": "weather",
            "actions": [
                {
                    "type": "weather",
                    "taskId": "a1",
                    "startsAt": "2026-09-21T08:00:00+02:00",
                    "endsAt": "2026-09-21T16:00:00+02:00",
                    "capacity": 0.5,
                    "ruleId": "rain-rule-1",
                    "confirmed": True,
                }
            ],
        },
        {
            "id": "resequence",
            "actions": [
                {
                    "type": "resequence",
                    "projectOrder": ["alpha", "beta", "gamma"],
                }
            ],
        },
    ]

    result = evaluate_portfolio(payload)
    actions = {
        evaluation["id"]: evaluation["actions"][0]
        for evaluation in result["candidateEvaluations"]
    }

    assert actions["date-shift"] == {
        "type": "date_shift",
        "taskId": "a1",
        "startsAt": "2026-09-21T06:00:00Z",
        "endsAt": "2026-09-22T14:00:00Z",
        "capability": "api",
        "modelledEffect": "planned_task_dates",
        "upstream": {
            "method": "PATCH",
            "resource": "task",
            "fields": ["plannedStartAt", "plannedEndAt"],
            "permission": "tasks.write",
        },
    }
    assert actions["material"] == {
        "type": "material",
        "taskId": "a2",
        "materialId": "cables",
        "availableAt": "2026-09-21T06:00:00Z",
        "confirmed": True,
        "capability": "manual",
        "modelledEffect": "confirmed_material_gate_override",
    }
    assert actions["weather"] == {
        "type": "weather",
        "taskId": "a1",
        "projectId": None,
        "startsAt": "2026-09-21T06:00:00Z",
        "endsAt": "2026-09-21T14:00:00Z",
        "capacity": 0.5,
        "ruleId": "rain-rule-1",
        "confirmed": True,
        "capability": "manual",
        "modelledEffect": "confirmed_workability_cap",
    }
    assert actions["resequence"] == {
        "type": "resequence",
        "projectOrder": ["alpha", "beta", "gamma"],
        "capability": "manual",
        "modelledEffect": "dispatch_order_only",
    }


def test_confirmed_baseline_clips_active_transfer_and_does_not_duplicate_it() -> None:
    payload = _single_worker_payload(effort=1)
    payload["implementedActions"] = [
        {
            "type": "transfer",
            "workerId": "worker-b",
            "fromProjectId": "beta",
            "toProjectId": "alpha",
            "startsAt": "2026-09-21T08:00:00+02:00",
            "endsAt": "2026-09-21T12:00:00+02:00",
            "outboundTravelHours": 1,
            "setupHours": 1,
            "returnTravelHours": 1,
        }
    ]
    payload["candidateActions"] = [
        {
            "id": "same-confirmed-transfer",
            "actions": [deepcopy(payload["implementedActions"][0])],
        }
    ]

    result = evaluate_portfolio(payload)
    baseline = result["strategies"][0]
    candidate = result["candidateEvaluations"][0]
    implemented = result["implementedActions"]

    assert baseline["implemented"] is True
    assert implemented[0]["productiveStartsAt"] == "2026-09-21T08:00:00Z"
    assert implemented[0]["productiveEndsAt"] == "2026-09-21T09:00:00Z"
    assert implemented[0]["nonProductiveHours"] == 1.0
    assert baseline["transferNonProductiveHours"] == 1.0
    assert candidate["feasible"] is True
    assert candidate["outcomesByProject"] == result["baselineByProject"]


def test_expired_or_duplicate_implemented_transfer_has_no_repeated_friction() -> None:
    payload = _single_worker_payload(effort=1)
    transfer = {
        "type": "transfer",
        "workerId": "worker-b",
        "fromProjectId": "beta",
        "toProjectId": "alpha",
        "startsAt": "2026-09-21T08:00:00+02:00",
        "endsAt": "2026-09-21T10:00:00+02:00",
        "outboundTravelHours": 1,
        "setupHours": 1,
        "returnTravelHours": 1,
    }
    payload["implementedActions"] = [transfer, deepcopy(transfer)]

    result = evaluate_portfolio(payload)

    assert result["strategies"][0]["transferNonProductiveHours"] == 0.0
    assert result["strategies"][0]["actions"] == []
    assert result["implementedActions"] == []


def test_confirmed_overtime_uses_only_future_window_and_expired_window_is_skipped() -> None:
    payload = _single_worker_payload(
        as_of="2026-09-21T17:00:00+02:00",
        effort=3,
        target_finish="2026-09-21T17:00:00+02:00",
    )
    payload["workers"][0].update(
        {
            "homeProjectId": "alpha",
            "canTransfer": False,
            "permittedOvertimeSlots": [
                {
                    "startsAt": "2026-09-21T16:00:00+02:00",
                    "endsAt": "2026-09-21T20:00:00+02:00",
                }
            ],
            "maxOvertimeHours": 4,
        }
    )
    payload["tasks"] = [payload["tasks"][0]]
    payload["implementedActions"] = [
        {
            "type": "overtime",
            "workerId": "worker-b",
            "startsAt": "2026-09-21T16:00:00+02:00",
            "endsAt": "2026-09-21T20:00:00+02:00",
        }
    ]

    result = evaluate_portfolio(payload)
    baseline = result["strategies"][0]
    assert baseline["overtimeHours"] == 3.0
    assert result["baselineByProject"]["alpha"]["finishP50"] == "2026-09-21T20:00:00+02:00"

    expired = deepcopy(payload)
    expired["implementedActions"] = [
        {
            "type": "overtime",
            "workerId": "worker-b",
            "startsAt": "2026-09-21T16:00:00+02:00",
            "endsAt": "2026-09-21T17:00:00+02:00",
        }
    ]
    expired_result = evaluate_portfolio(expired)
    expired_baseline = expired_result["strategies"][0]
    assert expired_baseline["overtimeHours"] == 0.0
    assert expired_result["baselineByProject"]["alpha"]["finishP50"] == (
        "2026-09-22T11:00:00+02:00"
    )


def test_generated_constraints_propose_date_shift_and_manual_conditional_steps() -> None:
    payload = _single_worker_payload(
        as_of="2026-09-21T08:00:00+02:00",
        effort=8,
        target_finish="2026-09-22T16:00:00+02:00",
    )
    payload["horizonDays"] = 2
    payload["workers"].append(
        {
            "id": "worker-a",
            "name": "Worker A",
            "specialtyIds": ["electrical"],
            "homeProjectId": "alpha",
            "workingWeekdays": [0, 1, 2, 3, 4],
            "shiftStart": "08:00",
            "shiftEnd": "16:00",
            "canTransfer": False,
            "permittedOvertimeSlots": [],
        }
    )
    weather_task = payload["tasks"][0]
    weather_task.update(
        {
            "assignedWorkerIds": ["worker-a"],
            "earliestStartAt": "2026-09-21T08:00:00+02:00",
            "plannedStartAt": "2026-09-22T08:00:00+02:00",
            "plannedEndAt": "2026-09-22T16:00:00+02:00",
            "weatherMode": "outdoor",
            "workability": {
                "capacityByDate": {
                    "2026-09-21": 1,
                    "2026-09-22": 0,
                }
            },
        }
    )
    material_task = payload["tasks"][1]
    material_task.update(
        {
            "materialReadyAt": "2026-09-22T08:00:00+02:00",
            "assignedWorkerIds": ["worker-b"],
        }
    )

    result = evaluate_portfolio(payload)
    generated_actions = [
        action
        for evaluation in result["candidateEvaluations"]
        for action in evaluation["actions"]
    ]
    selected = result["strategies"][1]

    assert selected["actions"][0]["type"] == "date_shift"
    assert selected["actions"][0]["taskId"] == "alpha-task"
    assert selected["actions"][0]["capability"] == "api"
    assert selected["actions"][0]["upstream"]["fields"] == [
        "plannedStartAt",
        "plannedEndAt",
    ]
    assert any(
        action["type"] == "material"
        and action["confirmed"] is False
        and action["modelledEffect"] == "manual_confirmation_required"
        for action in generated_actions
    )
    assert any(
        action["type"] == "weather"
        and action["confirmed"] is False
        and action["modelledEffect"] == "manual_context_only"
        for action in generated_actions
    )

    represented = deepcopy(payload)
    represented["implementedActions"] = [
        {
            "type": "date_shift",
            "taskId": "alpha-task",
            "startsAt": "2026-09-21T08:00:00+02:00",
            "endsAt": "2026-09-21T16:00:00+02:00",
        }
    ]
    represented_result = evaluate_portfolio(represented)
    assert not any(
        action["type"] == "date_shift"
        for evaluation in represented_result["candidateEvaluations"]
        for action in evaluation["actions"]
    )


def test_duplicate_explicit_candidate_ids_are_stable_and_unique() -> None:
    payload = _single_worker_payload()
    payload["candidateActions"] = [
        {"id": "same-id", "actions": [{"type": "priority", "taskOrder": ["alpha-task"]}]},
        {"id": "same-id", "actions": [{"type": "priority", "taskOrder": ["beta-task"]}]},
    ]

    result = evaluate_portfolio(payload)

    ids = [evaluation["id"] for evaluation in result["candidateEvaluations"]]
    assert ids == ["same-id", "same-id__duplicate-2"]


def test_duplicate_candidate_params_are_evaluated_once() -> None:
    payload = _single_worker_payload()
    payload["tasks"] = [payload["tasks"][0]]
    payload["proposedCandidates"] = [
        {"id": "proposed", "actions": [{"type": "priority", "taskOrder": ["alpha-task"]}]},
    ]
    payload["candidateActions"] = [
        {"id": "same-id", "actions": [{"type": "priority", "taskOrder": ["alpha-task"]}]},
        {"id": "same-id", "actions": [{"type": "priority", "taskOrder": ["alpha-task"]}]},
        {"id": "other-id", "actions": [{"type": "priority", "taskOrder": ["alpha-task"]}]},
    ]

    result = evaluate_portfolio(payload)

    ids = [evaluation["id"] for evaluation in result["candidateEvaluations"]]
    assert ids == ["proposed"]
    assert result["candidatesEvaluated"] == 1


def test_presentation_preserves_metrics_and_resolves_names() -> None:
    planning = {
        "targetProjectId": "p1",
        "projects": [{"id": "p1", "name": "Project One"}],
        "tasks": [{"id": "t1", "projectId": "p1", "title": "Install panel"}],
        "workers": [{"id": "w1", "name": "Worker One"}],
        "specialties": [{"id": "electrical", "name": "Electrical"}],
    }
    raw = {
        "targetProjectId": "p1",
        "baselineByProject": {"p1": {"finishP50": None, "unfinishedCount": 1}},
        "strategies": [
            {
                "id": "baseline",
                "actions": [],
                "outcomesByProject": {"p1": {"finishP50": None}},
            }
        ],
        "candidateEvaluations": [],
        "diagnostics": [
            {
                "code": "unavailable_specialty_capacity",
                "projectId": "p1",
                "taskId": "t1",
                "specialtyId": "electrical",
            }
        ],
    }

    enriched = enrich_result(raw, planning)

    assert raw["baselineByProject"]["p1"] == {"finishP50": None, "unfinishedCount": 1}
    assert enriched["baselineByProject"]["p1"]["projectName"] == "Project One"
    assert enriched["strategies"][0]["strategy"] == "baseline"
    assert enriched["strategies"][0]["target"]["projectName"] == "Project One"
    diagnostic = enriched["diagnostics"][0]
    assert diagnostic["label"] == "Required specialty unavailable"
    assert diagnostic["taskName"] == "Install panel"
    assert diagnostic["specialtyName"] == "Electrical"
    assert enriched["presentation"]["causalAttribution"] is False


def test_graph_has_typed_nodes_and_explicit_relationships() -> None:
    planning = {
        "projects": [
            {"id": "p1", "name": "Project One"},
            {"id": "p2", "name": "Project Two"},
        ],
        "tasks": [
            {
                "id": "t1",
                "projectId": "p1",
                "title": "Foundation",
                "predecessorIds": [],
                "assignedWorkerIds": ["w1"],
                "requiredMaterialIds": ["m1"],
            },
            {
                "id": "t2",
                "projectId": "p1",
                "title": "Walls",
                "predecessorIds": ["t1"],
                "assignedWorkerIds": ["w1"],
            },
            {
                "id": "t3",
                "projectId": "p2",
                "title": "Inspection",
                "predecessorIds": [],
                "assignedWorkerIds": ["w1"],
            },
        ],
        "workers": [{"id": "w1", "name": "Worker One"}],
        "materials": [{"id": "m1", "name": "Concrete"}],
    }

    graph = build_graph(planning)
    nodes = {node["id"]: node for node in graph["nodes"]}
    edges = {(edge["source"], edge["target"], edge["type"]) for edge in graph["edges"]}

    assert set(graph) == {"nodes", "edges", "projects"}
    assert nodes["project:p1"]["label"] == "Project One"
    assert nodes["task:t1"]["projectId"] == "p1"
    assert nodes["worker:w1"]["label"] == "Worker One"
    assert nodes["material:m1"]["label"] == "Concrete"
    assert ("task:t1", "task:t2", "prerequisite") in edges
    assert ("worker:w1", "task:t1", "assignment") in edges
    assert ("material:m1", "task:t1", "material_requirement") in edges
    assert ("task:t1", "task:t2", "shared_resource") in edges
    assert ("task:t1", "task:t3", "shared_resource") in edges


def test_bounded_bundle_and_result_contracts_are_additive() -> None:
    result = evaluate_portfolio(_fixture())

    assert result["totalBundlesEvaluated"] == result["candidatesEvaluated"] + 1
    assert result["totalBundlesEvaluated"] <= result["candidateLimits"]["maxTotalBundles"]
    assert result["candidateLimits"]["baselineIncluded"] is True
    assert all(
        evaluation["actionCount"] <= result["candidateLimits"]["maxActionsPerBundle"]
        for evaluation in result["candidateEvaluations"]
    )
    assert result["sampling"]["pairedAcrossStrategies"] is True
    assert set(result["baselineByProject"]["alpha"]["finishHistogram"][-1]) >= {
        "bucket",
        "label",
        "count",
        "probability",
        "censored",
    }
    assert len(result["baselineByProject"]["alpha"]["representativeTraces"]) <= 3
