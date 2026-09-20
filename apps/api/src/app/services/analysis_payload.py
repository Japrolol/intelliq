"""Pure analysis request/response adaptation shared by API and workers."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from src.app.domain.contracts import AnalysisRequest, PlanningInputs, PortfolioSnapshot


def _engine_payload(
    snapshot: PortfolioSnapshot,
    planning: PlanningInputs,
    body: AnalysisRequest,
    *,
    setup_hours: float | None = None,
    as_of: datetime | None = None,
    samples: int | None = None,
    candidate_actions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    transfers = [dict(transfer) for transfer in planning.transfers]
    if setup_hours is not None:
        for transfer in transfers:
            transfer["setupHours"] = setup_hours
    payload: dict[str, Any] = {
        "asOf": (as_of or snapshot.as_of).isoformat(),
        "horizonDays": int(
            snapshot.model_extra.get("horizonDays", 14) if snapshot.model_extra else 14
        ),
        "seed": body.seed
        if body.seed is not None
        else int(snapshot.model_extra.get("seed", 0) if snapshot.model_extra else 0),
        "samples": samples
        if samples is not None
        else int(snapshot.model_extra.get("samples", 100) if snapshot.model_extra else 100),
        "targetProjectId": body.project_id,
        "projects": planning.projects,
        "tasks": planning.tasks,
        "workers": planning.workers,
        "reservations": planning.reservations,
        "transferAssumptions": transfers,
    }
    if candidate_actions is not None:
        payload["candidateActions"] = candidate_actions
    return payload


def _frontend_engine_result(result: dict[str, Any], target_project_id: str) -> dict[str, Any]:
    """Keep engine output auditable while adding frontend comparison aliases."""

    adapted = dict(result)
    raw_strategies = result.get("strategies", [])
    if not isinstance(raw_strategies, list):
        return adapted
    strategies: list[dict[str, Any]] = []
    for raw_strategy in raw_strategies:
        if not isinstance(raw_strategy, Mapping):
            continue
        strategy = dict(raw_strategy)
        outcomes = strategy.get("outcomesByProject")
        if isinstance(outcomes, Mapping):
            strategy["target"] = outcomes.get(target_project_id)
            donor_project_id = _donor_project_id(strategy, target_project_id)
            if donor_project_id:
                strategy["donor"] = outcomes.get(donor_project_id)
        labels = strategy.get("labels", [])
        label = (
            labels[0]
            if isinstance(labels, list) and labels
            else strategy.get("id", "Recovery strategy")
        )
        strategy.setdefault("name", label)
        strategy.setdefault("label", label)
        strategy["transfer"] = _transfer_alias(strategy.get("actions", []))
        strategies.append(strategy)
    adapted["strategies"] = strategies
    return adapted


def _donor_project_id(strategy: Mapping[str, Any], target_project_id: str) -> str | None:
    actions = strategy.get("actions", [])
    if isinstance(actions, list):
        for action in actions:
            if isinstance(action, Mapping) and action.get("type") == "transfer":
                donor = action.get("fromProjectId")
                if donor and str(donor) != target_project_id:
                    return str(donor)
    affected = strategy.get("affectedProjectIds", [])
    if isinstance(affected, list):
        for project_id in affected:
            if str(project_id) != target_project_id:
                return str(project_id)
    return None

def _transfer_alias(actions: Any) -> dict[str, Any] | None:
    if not isinstance(actions, list):
        return None
    for action in actions:
        if not isinstance(action, Mapping) or action.get("type") != "transfer":
            continue
        travel_hours = sum(
            float(action.get(key) or 0)
            for key in ("outboundTravelHours", "returnTravelHours")
            if isinstance(action.get(key), (int, float))
        )
        return {
            "workerId": action.get("workerId"),
            "fromProjectId": action.get("fromProjectId"),
            "toProjectId": action.get("toProjectId"),
            "windowStartAt": action.get("startsAt"),
            "windowEndAt": action.get("endsAt"),
            "setupHours": action.get("setupHours"),
            "travelHours": travel_hours,
        }
    return None
