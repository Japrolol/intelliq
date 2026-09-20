"""Import boundary for the separately owned portfolio simulation engine."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any


class SimulationEngineUnavailable(RuntimeError):
    """Raised while the engine agent's module is not present in the checkout."""


class SimulationInputError(ValueError):
    """Stable adapter error for invalid engine inputs, even before the engine lands."""


def evaluate_portfolio(
    payload: dict[str, Any], progress: Callable[[dict], None] | None = None
) -> dict[str, Any]:
    """Delegate to the required `src.app.simulation.engine` seam."""

    try:
        module = importlib.import_module("src.app.simulation.engine")
    except ModuleNotFoundError as exc:
        raise SimulationEngineUnavailable("src.app.simulation.engine is not available yet") from exc
    evaluator: Callable[[dict[str, Any]], dict[str, Any]] | None = getattr(
        module, "evaluate_portfolio", None
    )
    if evaluator is None:
        raise SimulationEngineUnavailable(
            "evaluate_portfolio is not exported by the simulation engine"
        )
    try:
        result = (
            evaluator(payload, progress=progress) if progress is not None else evaluator(payload)
        )
    except ValueError as exc:
        engine_error = getattr(module, "SimulationInputError", None)
        if engine_error is not None and isinstance(exc, engine_error):
            raise SimulationInputError(str(exc)) from exc
        raise
    if not isinstance(result, dict):
        raise SimulationEngineUnavailable("simulation engine returned a non-object result")
    return result


__all__ = ["SimulationEngineUnavailable", "SimulationInputError", "evaluate_portfolio"]
