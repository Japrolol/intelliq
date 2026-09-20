"""Focused tests for bounded pure-payload simulation execution."""

from __future__ import annotations

import json
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Any

import pytest

from src.app.domain.engine_bridge import evaluate_portfolio
from src.app.services import simulation_executor

FIXTURE_PATH = Path(__file__).resolve().parents[3] / "fixtures" / "portfolio-demo.json"


@pytest.fixture(autouse=True)
def clean_simulation_executor() -> None:
    """Keep the process singleton isolated between tests."""

    simulation_executor.shutdown_simulation_executor(terminate=True)
    yield
    simulation_executor.shutdown_simulation_executor(terminate=True)


def _fixture_payload() -> dict[str, Any]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    payload["samples"] = 1
    return payload


def test_fixture_default_uses_direct_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def direct(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(payload)
        return {"status": "direct"}

    monkeypatch.setattr(simulation_executor, "evaluate_portfolio", direct)

    result = simulation_executor.run_simulation({"samples": 1})

    assert result == {"status": "direct"}
    assert calls == [{"samples": 1}]
    assert simulation_executor._PROCESS_EXECUTOR is None


def test_process_path_sends_only_payload_and_matches_direct_engine() -> None:
    payload = _fixture_payload()
    expected = evaluate_portfolio(payload)

    result = simulation_executor.run_simulation(
        payload,
        process_enabled=True,
        timeout_seconds=60,
    )

    assert result == expected
    assert simulation_executor._PROCESS_EXECUTOR is not None


def test_timeout_cancels_future_and_terminates_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeFuture:
        canceled = False

        def result(self, timeout: float) -> dict[str, Any]:
            assert timeout == 0.5
            raise FutureTimeoutError

        def cancel(self) -> bool:
            self.canceled = True
            return True

    class FakeExecutor:
        def __init__(self) -> None:
            self.future = FakeFuture()
            self.terminated = False

        def submit(self, function: object, payload: dict[str, Any]) -> FakeFuture:
            assert function is simulation_executor._evaluate_payload
            assert payload == {"samples": 1}
            return self.future

        def terminate_workers(self) -> None:
            self.terminated = True

    executor = FakeExecutor()
    monkeypatch.setattr(simulation_executor, "_PROCESS_EXECUTOR", executor)

    with pytest.raises(simulation_executor.SimulationTimeoutError):
        simulation_executor.run_simulation(
            {"samples": 1},
            process_enabled=True,
            timeout_seconds=0.5,
        )

    assert executor.future.canceled is True
    assert executor.terminated is True
    assert simulation_executor._PROCESS_EXECUTOR is None


def test_shutdown_helper_waits_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeExecutor:
        def __init__(self) -> None:
            self.calls: list[tuple[bool, bool]] = []

        def shutdown(self, *, wait: bool, cancel_futures: bool) -> None:
            self.calls.append((wait, cancel_futures))

    executor = FakeExecutor()
    monkeypatch.setattr(simulation_executor, "_PROCESS_EXECUTOR", executor)

    simulation_executor.shutdown_simulation_executor()

    assert executor.calls == [(True, True)]
    assert simulation_executor._PROCESS_EXECUTOR is None


def test_timeout_must_be_positive() -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        simulation_executor.run_simulation({}, process_enabled=True, timeout_seconds=0)
