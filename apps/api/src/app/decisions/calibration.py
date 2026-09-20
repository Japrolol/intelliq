"""Calibrate transfer setup time from directly observed comparable durations.

This module owns arithmetic only. Authorization, tenant/source matching, review of
outliers, and storage of versions belong to the calling decision service.
"""

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True, slots=True)
class SetupCalibration:
    """A reproducible setup-hour update with evidence counts."""

    prior_mean_hours: float
    prior_strength: float
    previous_sample_count: int
    accepted_observation_hours: tuple[float, ...]
    updated_mean_hours: float

    @property
    def total_sample_count(self) -> int:
        return self.previous_sample_count + len(self.accepted_observation_hours)


def update_setup_hours(
    *,
    prior_mean_hours: float,
    prior_strength: float,
    previous_sample_count: int,
    accepted_observation_hours: tuple[float, ...],
) -> SetupCalibration:
    """Return a shrinkage update for eligible direct setup observations.

    `prior_strength` expresses equivalent observations backing the original
    assumption. `prior_mean_hours` is the current version's mean and
    `previous_sample_count` is the number already included in that mean.
    The caller must first establish that observations are directly measured,
    comparable, authorized for the same tenant, and from the same source mode.
    Zero observations leave the mean unchanged. This function does not infer a
    duration from a project finish date or worklog hours.
    """

    if not isfinite(prior_mean_hours) or prior_mean_hours < 0:
        raise ValueError("prior mean must be finite and nonnegative")
    if not isfinite(prior_strength) or prior_strength <= 0:
        raise ValueError("prior strength must be finite and positive")
    if previous_sample_count < 0:
        raise ValueError("previous sample count must be nonnegative")
    if any(not isfinite(value) or value < 0 for value in accepted_observation_hours):
        raise ValueError("setup observations must be finite and nonnegative")

    count = len(accepted_observation_hours)
    existing_weight = prior_strength + previous_sample_count
    updated = (existing_weight * prior_mean_hours + sum(accepted_observation_hours)) / (
        existing_weight + count
    )
    return SetupCalibration(
        prior_mean_hours=prior_mean_hours,
        prior_strength=prior_strength,
        previous_sample_count=previous_sample_count,
        accepted_observation_hours=accepted_observation_hours,
        updated_mean_hours=updated,
    )
