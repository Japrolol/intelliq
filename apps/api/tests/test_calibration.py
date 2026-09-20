"""Regression tests for the one calibrated organization parameter."""

import pytest

from src.app.decisions.calibration import update_setup_hours


def test_synthetic_replay_update_is_exact() -> None:
    result = update_setup_hours(
        prior_mean_hours=1,
        prior_strength=2,
        previous_sample_count=0,
        accepted_observation_hours=(2, 3),
    )

    assert result.updated_mean_hours == 1.75
    assert result.total_sample_count == 2


def test_empty_observations_leave_prior_unchanged() -> None:
    result = update_setup_hours(
        prior_mean_hours=1.5,
        prior_strength=2,
        previous_sample_count=4,
        accepted_observation_hours=(),
    )

    assert result.updated_mean_hours == 1.5
    assert result.total_sample_count == 4


def test_second_update_preserves_first_observations() -> None:
    result = update_setup_hours(
        prior_mean_hours=1.75,
        prior_strength=2,
        previous_sample_count=2,
        accepted_observation_hours=(4,),
    )

    assert result.updated_mean_hours == 2.2
    assert result.total_sample_count == 3


def test_invalid_observation_is_rejected() -> None:
    with pytest.raises(ValueError, match="finite and nonnegative"):
        update_setup_hours(
            prior_mean_hours=1,
            prior_strength=2,
            previous_sample_count=0,
            accepted_observation_hours=(float("nan"),),
        )
