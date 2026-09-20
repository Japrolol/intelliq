"""Remove sensitive values from the old local session schema."""

from alembic import op
import sqlalchemy as sa

revision = "0003_scrub_tokens"
down_revision = "0002_operational_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("sessions")}
    if {"upstream_access", "upstream_refresh"}.issubset(columns):
        op.execute(
            sa.text(
                "UPDATE sessions "
                "SET upstream_access = NULL, upstream_refresh = NULL"
            )
        )


def downgrade() -> None:
    # The old token values are intentionally not recoverable.
    pass
