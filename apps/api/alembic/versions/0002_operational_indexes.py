"""Indexes for current organization-scoped read paths."""

from alembic import op

revision = "0002_operational_indexes"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_sessions_user_id", "sessions", ["user_id"], unique=False, if_not_exists=True
    )
    op.create_index(
        "ix_snapshots_org_fetched_at",
        "snapshots",
        ["organization_id", "fetched_at"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_analyses_org_created_at",
        "analyses",
        ["organization_id", "created_at"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_signals_org_project_updated_at",
        "signals",
        ["organization_id", "project_id", "updated_at"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_decisions_org_created_at",
        "decision_contracts",
        ["organization_id", "created_at"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_observations_org_decision_created_at",
        "decision_observations",
        ["organization_id", "decision_id", "created_at"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_checkpoint_org_decision_evaluated_at",
        "checkpoint_evaluations",
        ["organization_id", "decision_id", "evaluated_at"],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    for index_name, table_name in (
        ("ix_checkpoint_org_decision_evaluated_at", "checkpoint_evaluations"),
        ("ix_observations_org_decision_created_at", "decision_observations"),
        ("ix_decisions_org_created_at", "decision_contracts"),
        ("ix_signals_org_project_updated_at", "signals"),
        ("ix_analyses_org_created_at", "analyses"),
        ("ix_snapshots_org_fetched_at", "snapshots"),
        ("ix_sessions_user_id", "sessions"),
    ):
        op.drop_index(index_name, table_name=table_name)
