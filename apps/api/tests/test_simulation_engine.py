"""Focused tests for the IntelliQ P0 simulation seam."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from src.app.simulation.engine import SimulationInputError, evaluate_portfolio

FIXTURE_PATH = Path(__file__).resolve().parents[3] / "fixtures" / "portfolio-demo.json"


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _strategy(result: dict, prefix: str) -> dict:
    for strategy in result["strategies"]:
        if str(strategy["id"]).startswith(prefix):
            return strategy
    raise AssertionError(f"strategy {prefix!r} was not selected")


def _small_payload(
    projects: list[dict],
    tasks: list[dict],
    workers: list[dict],
    *,
    target_project_id: str = "alpha",
    horizon_days: int = 2,
    samples: int = 1,
) -> dict:
    return {
        "asOf": "2026-09-21T08:00:00+02:00",
        "horizonDays": horizon_days,
        "seed": 17,
        "samples": samples,
        "targetProjectId": target_project_id,
        "projects": projects,
        "tasks": tasks,
        "workers": workers,
        "reservations": [],
        "transferAssumptions": [],
    }


def _project(project_id: str = "alpha", *, priority: int = 1, **extra: object) -> dict:
    return {
        "id": project_id,
        "name": project_id.title(),
        "targetFinishAt": "2026-09-22T16:00:00+02:00",
        "priority": priority,
        "timezone": "Europe/Warsaw",
        **extra,
    }


def _task(
    task_id: str,
    *,
    project_id: str = "alpha",
    effort: float = 8,
    specialty: str = "electrical",
    predecessors: list[str] | None = None,
    **extra: object,
) -> dict:
    return {
        "id": task_id,
        "projectId": project_id,
        "status": "planned",
        "predecessorIds": predecessors or [],
        "requiredSpecialtyId": specialty,
        "remainingPersonHours": {
            "optimistic": effort,
            "mostLikely": effort,
            "pessimistic": effort,
        },
        "minCrew": 1,
        "maxCrew": 1,
        "earliestStartAt": "2026-09-21T08:00:00+02:00",
        **extra,
    }


def _worker(
    worker_id: str,
    *,
    home_project_id: str | None = "alpha",
    specialties: list[str] | None = None,
    **extra: object,
) -> dict:
    return {
        "id": worker_id,
        "specialtyIds": specialties or ["electrical"],
        "homeProjectId": home_project_id,
        "workingWeekdays": [0, 1, 2, 3, 4],
        "shiftStart": "08:00",
        "shiftEnd": "16:00",
        "canTransfer": False,
        **extra,
    }


def test_section_16_fixture_has_hand_checkable_baseline_and_transfer() -> None:
    result = evaluate_portfolio(_fixture())

    baseline = result["baselineByProject"]
    assert baseline["alpha"]["finishP50"] == "2026-09-24T16:00:00+02:00"
    assert baseline["beta"]["finishP50"] == "2026-09-22T16:00:00+02:00"
    assert baseline["gamma"]["finishP50"] == "2026-09-21T16:00:00+02:00"
    assert baseline["alpha"]["expectedPositiveDelayDays"] == 2.0
    assert baseline["beta"]["expectedPositiveDelayDays"] == 0.0

    transfer = _strategy(result, "transfer-eb-beta-alpha-")
    assert transfer["outcomesByProject"]["alpha"]["finishP50"] == "2026-09-23T16:00:00+02:00"
    assert transfer["outcomesByProject"]["beta"]["finishP50"] == "2026-09-23T16:00:00+02:00"
    assert transfer["outcomesByProject"]["gamma"]["finishP50"] == baseline["gamma"]["finishP50"]
    assert transfer["outcomesByProject"]["alpha"]["expectedPositiveDelayDays"] == 1.0
    assert transfer["outcomesByProject"]["beta"]["expectedPositiveDelayDays"] == 0.0
    assert transfer["transferNonProductiveHours"] == 0.0
    assert transfer["deltasByProject"]["alpha"]["targetDateBufferDaysBefore"] == -2.0
    assert transfer["deltasByProject"]["alpha"]["targetDateBufferDaysAfter"] == -1.0
    assert transfer["deltasByProject"]["beta"]["targetDateBufferDaysBefore"] == 1.0
    assert transfer["deltasByProject"]["beta"]["targetDateBufferDaysAfter"] == 0.0


def test_fixture_is_reproducible_and_candidates_share_paired_draws() -> None:
    payload = _fixture()
    first = evaluate_portfolio(payload)
    second = evaluate_portfolio(deepcopy(payload))

    assert first == second
    transfer = _strategy(first, "transfer-eb-beta-alpha-")
    assert transfer["deltasByProject"]["beta"]["expectedPositiveDelayChangeDays"] == 0.0
    assert transfer["deltasByProject"]["beta"]["finishP50ShiftDays"] == 1.0


def test_unfinished_runs_are_censored_without_horizon_finish_substitution() -> None:
    payload = _fixture()
    payload["horizonDays"] = 1
    payload["samples"] = 3
    payload["projects"][0]["targetFinishAt"] = "2026-09-21T16:00:00+02:00"
    for task in payload["tasks"]:
        if task["id"] == "a1":
            task["remainingPersonHours"] = {
                "optimistic": 100,
                "mostLikely": 100,
                "pessimistic": 100,
            }

    result = evaluate_portfolio(payload)
    outcome = result["baselineByProject"]["alpha"]

    assert outcome["unfinishedCount"] == 3
    assert outcome["censored"] is True
    assert outcome["finishP50"] is None
    assert outcome["expectedPositiveDelayDays"] is None
    assert outcome["delayProbability"] == 1.0


def test_cycles_and_missing_effort_block_simulation() -> None:
    payload = _fixture()
    payload["tasks"][1]["predecessorIds"] = ["a2"]
    with pytest.raises(SimulationInputError, match="cycle"):
        evaluate_portfolio(payload)

    payload = _fixture()
    payload["tasks"][0].pop("remainingPersonHours")
    with pytest.raises(SimulationInputError, match="remainingPersonHours"):
        evaluate_portfolio(payload)


def test_task_specific_weather_gate_blocks_outdoor_but_not_indoor_work() -> None:
    payload = _small_payload(
        [
            _project("alpha"),
            _project("beta", workability={"outdoor": {"2026-09-21": 0}}),
        ],
        [
            _task(
                "outdoor-task",
                project_id="alpha",
                weatherMode="outdoor",
                workability={"capacityByDate": {"2026-09-21": 0}},
            ),
            _task("indoor-task", project_id="beta", specialty="carpentry", weatherMode="indoor"),
        ],
        [
            _worker("outdoor-worker", home_project_id="alpha"),
            _worker("indoor-worker", home_project_id="beta", specialties=["carpentry"]),
        ],
    )

    result = evaluate_portfolio(payload)
    outdoor = result["baselineByProject"]["alpha"]
    indoor = result["baselineByProject"]["beta"]

    assert outdoor["finishP50"] is None
    assert outdoor["unfinishedCount"] == 1
    assert indoor["finishP50"] == "2026-09-21T16:00:00+02:00"
    assert indoor["unfinishedCount"] == 0
    assert any(
        item["code"] == "task_exposed_to_reduced_workability" and item["taskId"] == "outdoor-task"
        for item in result["diagnostics"]
    )


def test_overtime_candidates_are_generated_inside_allowed_slots_and_caps() -> None:
    payload = _small_payload(
        [_project(targetFinishAt="2026-09-21T16:00:00+02:00")],
        [_task("overtime-task", effort=12)],
        [
            _worker(
                "overtime-worker",
                permittedOvertimeSlots=[
                    {
                        "startsAt": "2026-09-21T16:00:00+02:00",
                        "endsAt": "2026-09-21T20:00:00+02:00",
                    }
                ],
                maxOvertimeHours=2,
            )
        ],
    )

    result = evaluate_portfolio(payload)
    candidate = _strategy(result, "overtime-overtime-worker-")
    overtime_action = candidate["actions"][0]

    assert overtime_action["type"] == "overtime"
    assert overtime_action["startsAt"] == "2026-09-21T14:00:00Z"
    assert overtime_action["endsAt"] == "2026-09-21T16:00:00Z"
    assert overtime_action["hours"] == 2.0
    assert candidate["overtimeHours"] == 2.0
    assert candidate["outcomesByProject"]["alpha"]["finishP50"] == "2026-09-22T10:00:00+02:00"

    over_cap = deepcopy(payload)
    over_cap["candidateActions"] = [
        {
            "id": "over-cap",
            "actions": [
                {
                    "type": "overtime",
                    "workerId": "overtime-worker",
                    "startsAt": "2026-09-21T16:00:00+02:00",
                    "endsAt": "2026-09-21T19:00:00+02:00",
                }
            ],
        }
    ]
    rejected = evaluate_portfolio(over_cap)["candidateEvaluations"][0]
    assert rejected["feasible"] is False
    assert "overtime_limit_exceeded:overtime-worker" in rejected["rejectionReasons"]

    outside_slot = deepcopy(payload)
    outside_slot["candidateActions"] = [
        {
            "id": "outside-slot",
            "actions": [
                {
                    "type": "overtime",
                    "workerId": "overtime-worker",
                    "startsAt": "2026-09-21T15:00:00+02:00",
                    "endsAt": "2026-09-21T17:00:00+02:00",
                }
            ],
        }
    ]
    outside_evaluation = evaluate_portfolio(outside_slot)["candidateEvaluations"][0]
    assert outside_evaluation["feasible"] is False
    assert "overtime_not_permitted:overtime-worker" in outside_evaluation["rejectionReasons"]


def test_generated_priority_candidate_uses_target_project_first_for_shared_worker() -> None:
    payload = _small_payload(
        [
            _project("alpha", priority=1, targetFinishAt="2026-09-21T16:00:00+02:00"),
            _project("beta", priority=2),
        ],
        [
            _task("alpha-task", project_id="alpha"),
            _task("beta-task", project_id="beta"),
        ],
        [_worker("shared-worker", home_project_id=None)],
    )

    result = evaluate_portfolio(payload)
    baseline = result["baselineByProject"]["alpha"]
    priority = _strategy(result, "priority-target-project-first")

    assert baseline["finishP50"] == "2026-09-22T16:00:00+02:00"
    assert priority["outcomesByProject"]["alpha"]["finishP50"] == "2026-09-21T16:00:00+02:00"
    assert priority["actions"] == [{"type": "priority", "projectOrder": ["alpha", "beta"]}]


def test_priority_order_cannot_bypass_predecessors() -> None:
    payload = _small_payload(
        [_project(targetFinishAt="2026-09-21T16:00:00+02:00")],
        [
            _task("predecessor", effort=8),
            _task("successor", effort=8, predecessors=["predecessor"]),
        ],
        [
            _worker("worker-a"),
            _worker("worker-b"),
        ],
    )
    payload["candidateActions"] = [
        {
            "id": "successor-first",
            "actions": [{"type": "priority", "taskOrder": ["successor", "predecessor"]}],
        }
    ]

    result = evaluate_portfolio(payload)
    evaluation = result["candidateEvaluations"][0]

    assert evaluation["feasible"] is True
    assert (
        evaluation["outcomesByProject"]["alpha"]["finishP50"]
        == result["baselineByProject"]["alpha"]["finishP50"]
    )
    assert evaluation["outcomesByProject"]["alpha"]["finishP50"] == "2026-09-22T16:00:00+02:00"
    assert [strategy["id"] for strategy in result["strategies"]] == ["baseline"]


def test_priority_order_cannot_bypass_hard_reservation() -> None:
    payload = _small_payload(
        [_project(targetFinishAt="2026-09-21T16:00:00+02:00")],
        [_task("reserved-task", effort=8)],
        [_worker("worker-a")],
    )
    payload["reservations"] = [
        {
            "taskId": "reserved-task",
            "startsAt": "2026-09-21T08:00:00+02:00",
            "endsAt": "2026-09-23T08:00:00+02:00",
        }
    ]
    payload["candidateActions"] = [
        {
            "id": "reserved-task-first",
            "actions": [{"type": "priority", "taskOrder": ["reserved-task"]}],
        }
    ]

    result = evaluate_portfolio(payload)
    evaluation = result["candidateEvaluations"][0]

    assert evaluation["feasible"] is True
    assert evaluation["outcomesByProject"]["alpha"]["finishP50"] is None
    assert result["strategies"][0]["id"] == "baseline"
    assert any(item["code"] == "task_blocked_by_hard_reservation" for item in result["diagnostics"])


def test_no_beneficial_feasible_action_keeps_baseline_only() -> None:
    payload = _small_payload(
        [_project(targetFinishAt="2026-09-21T16:00:00+02:00")],
        [_task("only-task", effort=8)],
        [_worker("worker-a")],
    )
    payload["candidateActions"] = [
        {
            "id": "same-order",
            "actions": [{"type": "priority", "taskOrder": ["only-task"]}],
        }
    ]

    result = evaluate_portfolio(payload)

    assert result["candidateEvaluations"][0]["feasible"] is True
    assert [strategy["id"] for strategy in result["strategies"]] == ["baseline"]
    assert "no_feasible_beneficial_recovery_candidate" in result["warnings"]


def test_no_beneficial_candidate_returns_baseline_only() -> None:
    payload = _fixture()
    payload["transferAssumptions"] = []

    result = evaluate_portfolio(payload)

    assert [strategy["id"] for strategy in result["strategies"]] == ["baseline"]
    assert "no_feasible_beneficial_recovery_candidate" in result["warnings"]


def test_uncertain_effort_and_nonzero_transfer_friction_are_reproducible() -> None:
    payload = _fixture()
    payload["samples"] = 30
    payload["tasks"][0]["remainingPersonHours"] = {
        "optimistic": 16,
        "mostLikely": 24,
        "pessimistic": 32,
    }
    payload["transferAssumptions"][0].update(
        {"outboundTravelHours": 1, "returnTravelHours": 0.5, "setupHours": 0.5}
    )

    first = evaluate_portfolio(payload)
    second = evaluate_portfolio(deepcopy(payload))

    transfer = next(
        item
        for item in first["candidateEvaluations"]
        if str(item["id"]).startswith("transfer-eb-beta-alpha-")
    )
    assert first == second
    assert (
        first["baselineByProject"]["alpha"]["finishP10"]
        != first["baselineByProject"]["alpha"]["finishP90"]
    )
    assert transfer["feasible"] is True
    assert transfer["transferNonProductiveHours"] == 2.0
    assert transfer["actions"][0]["nonProductiveHours"] == 2.0
    assert first["sampling"]["pairedAcrossStrategies"] is True


def test_reordering_input_rows_preserves_paired_reproducible_results() -> None:
    payload = _fixture()
    reordered = deepcopy(payload)
    reordered["projects"] = list(reversed(reordered["projects"]))
    reordered["tasks"] = list(reversed(reordered["tasks"]))
    reordered["workers"] = list(reversed(reordered["workers"]))
    reordered["transferAssumptions"] = list(reversed(reordered["transferAssumptions"]))

    assert evaluate_portfolio(payload) == evaluate_portfolio(reordered)


def test_overnight_worker_calendar_keeps_post_midnight_capacity() -> None:
    payload = _small_payload(
        [_project(targetFinishAt="2026-09-22T06:00:00+02:00")],
        [_task("overnight-task", effort=8, earliestStartAt="2026-09-21T22:00:00+02:00")],
        [
            _worker(
                "overnight-worker",
                workingWeekdays=[0, 1, 2, 3, 4, 5, 6],
                shiftStart="22:00",
                shiftEnd="06:00",
            )
        ],
        horizon_days=1,
    )
    payload["asOf"] = "2026-09-21T22:00:00+02:00"

    result = evaluate_portfolio(payload)

    assert result["baselineByProject"]["alpha"]["finishP50"] == (
        "2026-09-22T06:00:00+02:00"
    )
    assert result["baselineByProject"]["alpha"]["unfinishedCount"] == 0


def test_transfer_must_start_in_horizon_and_cannot_overlap_overtime() -> None:
    payload = _small_payload(
        [
            _project("alpha", targetFinishAt="2026-09-21T16:00:00+02:00"),
            _project("beta"),
        ],
        [
            _task("alpha-task", project_id="alpha", effort=4),
            _task("beta-task", project_id="beta", effort=8),
        ],
        [
            _worker(
                "transfer-worker",
                home_project_id="beta",
                canTransfer=True,
                permittedOvertimeSlots=[
                    {
                        "startsAt": "2026-09-21T10:00:00+02:00",
                        "endsAt": "2026-09-21T12:00:00+02:00",
                    }
                ],
            )
        ],
    )
    payload["transferAssumptions"] = [
        {
            "fromProjectId": "beta",
            "toProjectId": "alpha",
            "workerId": "transfer-worker",
            "outboundTravelHours": 0,
            "returnTravelHours": 0,
            "setupHours": 0,
        }
    ]
    payload["candidateActions"] = [
        {
            "id": "starts-before-analysis",
            "actions": [
                {
                    "type": "transfer",
                    "workerId": "transfer-worker",
                    "fromProjectId": "beta",
                    "toProjectId": "alpha",
                    "startsAt": "2026-09-21T07:00:00+02:00",
                    "endsAt": "2026-09-21T12:00:00+02:00",
                }
            ],
        },
        {
            "id": "transfer-and-overtime-overlap",
            "actions": [
                {
                    "type": "transfer",
                    "workerId": "transfer-worker",
                    "fromProjectId": "beta",
                    "toProjectId": "alpha",
                    "startsAt": "2026-09-21T08:00:00+02:00",
                    "endsAt": "2026-09-21T12:00:00+02:00",
                },
                {
                    "type": "overtime",
                    "workerId": "transfer-worker",
                    "startsAt": "2026-09-21T10:00:00+02:00",
                    "endsAt": "2026-09-21T11:00:00+02:00",
                },
            ],
        },
    ]

    result = evaluate_portfolio(payload)

    assert result["candidateEvaluations"][0]["feasible"] is False
    assert "transfer_outside_simulation_horizon:transfer-worker" in result[
        "candidateEvaluations"
    ][0]["rejectionReasons"]
    assert result["candidateEvaluations"][1]["feasible"] is False
    assert "overtime_overlaps_transfer:transfer-worker" in result["candidateEvaluations"][1][
        "rejectionReasons"
    ]


def test_invalid_explicit_action_is_not_silently_treated_as_noop() -> None:
    payload = _small_payload(
        [_project(targetFinishAt="2026-09-21T16:00:00+02:00")],
        [_task("only-task", effort=8)],
        [_worker("worker-a")],
    )
    payload["candidateActions"] = [
        {"id": "unsupported", "actions": [{"type": "teleport_worker"}]}
    ]

    result = evaluate_portfolio(payload)

    evaluation = result["candidateEvaluations"][0]
    assert evaluation["feasible"] is False
    assert "unsupported_action_type:teleport_worker" in evaluation["rejectionReasons"]
    assert [strategy["id"] for strategy in result["strategies"]] == ["baseline"]


def test_identical_invalid_actions_are_not_resimulated() -> None:
    payload = _small_payload(
        [_project(targetFinishAt="2026-09-21T16:00:00+02:00")],
        [_task("only-task", effort=8)],
        [_worker("worker-a")],
    )
    payload["candidateActions"] = [
        {"id": f"unsupported-{index}", "actions": [{"type": "teleport_worker"}]}
        for index in range(61)
    ]

    result = evaluate_portfolio(payload)

    assert result["candidatesEvaluated"] == 1
    assert len(result["candidateEvaluations"]) == 1


def test_candidate_evaluation_count_reports_the_bounded_work() -> None:
    payload = _small_payload(
        [_project(targetFinishAt="2026-09-21T16:00:00+02:00")],
        [_task("only-task", effort=8)],
        [_worker("worker-a")],
    )
    payload["candidateActions"] = [
        {"id": f"unsupported-{index}", "actions": [{"type": f"teleport_worker_{index}"}]}
        for index in range(61)
    ]

    result = evaluate_portfolio(payload)

    assert result["candidatesEvaluated"] == 60
    assert len(result["candidateEvaluations"]) == 60


def test_censored_control_project_is_not_selected_as_target_recovery() -> None:
    payload = _small_payload(
        [
            _project("alpha", priority=1, targetFinishAt="2026-09-21T12:00:00+02:00"),
            _project("beta", priority=1, targetFinishAt="2026-09-22T16:00:00+02:00"),
        ],
        [
            _task("alpha-task", project_id="alpha", effort=8, maxCrew=2),
            _task("beta-task", project_id="beta", effort=8),
        ],
        [
            _worker("alpha-worker", home_project_id="alpha"),
            _worker("beta-worker", home_project_id="beta", canTransfer=True),
        ],
        horizon_days=1,
    )
    payload["transferAssumptions"] = [
        {
            "fromProjectId": "beta",
            "toProjectId": "alpha",
            "workerId": "beta-worker",
            "outboundTravelHours": 0,
            "returnTravelHours": 0,
            "setupHours": 0,
        }
    ]
    payload["candidateActions"] = [
        {
            "id": "transfer-beta-worker",
            "actions": [
                {
                    "type": "transfer",
                    "workerId": "beta-worker",
                    "fromProjectId": "beta",
                    "toProjectId": "alpha",
                    "startsAt": "2026-09-21T08:00:00+02:00",
                    "endsAt": "2026-09-21T16:00:00+02:00",
                    "eligibleTargetTaskIds": ["alpha-task"],
                }
            ],
        }
    ]

    result = evaluate_portfolio(payload)

    evaluation = result["candidateEvaluations"][0]
    assert evaluation["feasible"] is True
    assert evaluation["outcomesByProject"]["alpha"]["finishP50"] == (
        "2026-09-21T12:00:00+02:00"
    )
    assert evaluation["outcomesByProject"]["beta"]["unknownCensoredCount"] == 1
    assert [strategy["id"] for strategy in result["strategies"]] == ["baseline"]
    assert "no_feasible_beneficial_recovery_candidate" in result["warnings"]
