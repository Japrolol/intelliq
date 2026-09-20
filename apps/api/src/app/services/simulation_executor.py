"""Bounded process isolation for the pure portfolio simulation engine.

The portfolio pipeline performs provider and persistence work in its caller,
then passes only the JSON-like engine payload through this module. Fixture
tests and local fixture mode use the direct path by default. Live or queued
execution can opt into one spawned CPU process with a finite timeout.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from concurrent.futures.process import BrokenProcessPool
from multiprocessing import get_context
from threading import Lock
from typing import Any, Final

from src.app.domain.engine_bridge import evaluate_portfolio

DEFAULT_SIMULATION_TIMEOUT_SECONDS: Final[float] = 180.0
SIMULATION_PROCESS_WORKERS: Final[int] = 1

logger = logging.getLogger(__name__)
_EXECUTOR_LOCK = Lock()
_PROCESS_EXECUTOR: ProcessPoolExecutor | None = None


class SimulationExecutionError(RuntimeError):
    """The isolated simulation process could not produce a result."""


class SimulationTimeoutError(SimulationExecutionError):
    """The isolated simulation exceeded its caller-provided time budget."""


def run_simulation(
    payload: dict[str, Any],
    *,
    process_enabled: bool = False,
    timeout_seconds: float = DEFAULT_SIMULATION_TIMEOUT_SECONDS,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Evaluate one pure payload directly or in the bounded child process.

    The payload is the only value sent to the child. The direct default keeps
    fixture unit tests deterministic and avoids spawning a process for local
    demonstrations. A timeout tears down the pool because canceling a future
    cannot stop a function already executing in a child process.
    """

    if not process_enabled:
        return evaluate_portfolio(payload, progress=progress)
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero")

    executor = _get_process_executor()
    receiver, sender = get_context("spawn").Pipe(duplex=False)
    future = executor.submit(_evaluate_payload, payload, sender)
    deadline = time.monotonic() + timeout_seconds
    try:
        while not future.done():
            if time.monotonic() >= deadline:
                raise FutureTimeoutError
            if receiver.poll(0.1):
                event = receiver.recv()
                if progress is not None:
                    progress(event)
        while receiver.poll():
            event = receiver.recv()
            if progress is not None:
                progress(event)
        result = future.result()
    except FutureTimeoutError as exc:
        future.cancel()
        _replace_timed_out_executor(executor)
        raise SimulationTimeoutError(
            f"portfolio simulation exceeded {timeout_seconds:g}s",
        ) from exc
    except BrokenProcessPool as exc:
        _discard_executor(executor)
        raise SimulationExecutionError("portfolio simulation process failed") from exc
    except BaseException:
        if not future.done():
            _replace_timed_out_executor(executor)
        raise
    finally:
        receiver.close()
        sender.close()
    if not isinstance(result, dict):
        raise SimulationExecutionError("portfolio simulation returned a non-object result")
    return result


def shutdown_simulation_executor(*, wait: bool = True, terminate: bool = False) -> None:
    """Release the singleton process pool during application lifespan teardown.

    Normal shutdown waits for a current calculation. terminate=True is
    reserved for timeout recovery and forcibly stops the child after queued
    work has been canceled.
    """

    executor = _take_process_executor()
    if executor is None:
        return
    if terminate:
        _terminate_executor(executor)
        return
    executor.shutdown(wait=wait, cancel_futures=True)


def _evaluate_payload(payload: dict[str, Any], sender: Any = None) -> dict[str, Any]:
    """Top-level child target so spawn receives no application state."""

    try:
        return evaluate_portfolio(payload, progress=sender.send if sender else None)
    finally:
        if sender:
            sender.close()


def _get_process_executor() -> ProcessPoolExecutor:
    global _PROCESS_EXECUTOR

    with _EXECUTOR_LOCK:
        if _PROCESS_EXECUTOR is None:
            _PROCESS_EXECUTOR = ProcessPoolExecutor(
                max_workers=SIMULATION_PROCESS_WORKERS,
                mp_context=get_context("spawn"),
            )
        return _PROCESS_EXECUTOR


def _take_process_executor() -> ProcessPoolExecutor | None:
    global _PROCESS_EXECUTOR

    with _EXECUTOR_LOCK:
        executor = _PROCESS_EXECUTOR
        _PROCESS_EXECUTOR = None
        return executor


def _discard_executor(executor: ProcessPoolExecutor) -> None:
    global _PROCESS_EXECUTOR

    with _EXECUTOR_LOCK:
        if _PROCESS_EXECUTOR is executor:
            _PROCESS_EXECUTOR = None
    executor.shutdown(wait=False, cancel_futures=True)


def _replace_timed_out_executor(executor: ProcessPoolExecutor) -> None:
    global _PROCESS_EXECUTOR

    with _EXECUTOR_LOCK:
        if _PROCESS_EXECUTOR is executor:
            _PROCESS_EXECUTOR = None
    _terminate_executor(executor)


def _terminate_executor(executor: ProcessPoolExecutor) -> None:
    """Terminate a timed-out pool across supported Python runtimes."""

    terminate_workers = getattr(executor, "terminate_workers", None)
    if callable(terminate_workers):
        terminate_workers()
        return

    processes = getattr(executor, "_processes", {}) or {}
    for process in processes.values():
        process.terminate()
    try:
        executor.shutdown(wait=False, cancel_futures=True)
    except Exception:
        logger.exception("timed-out simulation executor shutdown failed")


__all__ = [
    "DEFAULT_SIMULATION_TIMEOUT_SECONDS",
    "SIMULATION_PROCESS_WORKERS",
    "SimulationExecutionError",
    "SimulationTimeoutError",
    "run_simulation",
    "shutdown_simulation_executor",
]
