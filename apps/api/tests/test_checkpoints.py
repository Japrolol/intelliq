from datetime import UTC, datetime

from src.app.decisions.checkpoints import (
    CheckpointState,
    Comparator,
    evaluate_checkpoint,
)


def test_missing_evidence_is_not_success() -> None:
    due = datetime(2026, 9, 23, tzinfo=UTC)
    assert (
        evaluate_checkpoint(
            due_at=due,
            as_of=due,
            comparator=Comparator.AT_MOST,
            threshold=2,
            observed_value=None,
        )
        is CheckpointState.NEEDS_DATA
    )


def test_due_checkpoint_uses_frozen_comparator() -> None:
    due = datetime(2026, 9, 23, tzinfo=UTC)
    assert (
        evaluate_checkpoint(
            due_at=due,
            as_of=due,
            comparator=Comparator.AT_MOST,
            threshold=2,
            observed_value=3,
        )
        is CheckpointState.BREACHED
    )
