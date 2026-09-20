"""Evaluate frozen decision checkpoints from explicit observations."""

from datetime import datetime
from enum import StrEnum
from math import isfinite


class CheckpointState(StrEnum):
    NOT_DUE = "not_due"
    NEEDS_DATA = "needs_data"
    MET = "met"
    BREACHED = "breached"


class Comparator(StrEnum):
    AT_MOST = "at_most"
    AT_LEAST = "at_least"


def evaluate_checkpoint(
    *,
    due_at: datetime,
    as_of: datetime,
    comparator: Comparator,
    threshold: float,
    observed_value: float | None,
) -> CheckpointState:
    """Evaluate one numeric checkpoint without treating missing evidence as success.

    `observed_value` must be a verified observation or separately labelled
    refreshed forecast supplied by the calling service, according to the
    checkpoint's frozen metric definition.
    """

    if due_at.tzinfo is None or as_of.tzinfo is None:
        raise ValueError("checkpoint timestamps must be timezone-aware")
    if not isfinite(threshold):
        raise ValueError("checkpoint threshold must be finite")
    if as_of < due_at:
        return CheckpointState.NOT_DUE
    if observed_value is None:
        return CheckpointState.NEEDS_DATA
    if not isfinite(observed_value):
        raise ValueError("observed value must be finite")

    met = (
        observed_value <= threshold
        if comparator is Comparator.AT_MOST
        else observed_value >= threshold
    )
    return CheckpointState.MET if met else CheckpointState.BREACHED
