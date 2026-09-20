"""Encrypted per-session Timecue credentials for API and worker processes."""

import sqlalchemy as sa

from alembic import op

revision = "0005_timecue_connections"
down_revision = "0004_workflow_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("timecue_connections"):
        return
    op.create_table(
        "timecue_connections",
        sa.Column("session_id", sa.String(128), primary_key=True),
        sa.Column("user_id", sa.String(128), nullable=True),
        sa.Column("organization_id", sa.String(128), nullable=True),
        sa.Column("encrypted_access", sa.Text(), nullable=True),
        sa.Column("encrypted_refresh", sa.Text(), nullable=True),
        sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refresh_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_timecue_connections_user_id", "timecue_connections", ["user_id"])
    op.create_index(
        "ix_timecue_connections_organization_id", "timecue_connections", ["organization_id"]
    )


def downgrade() -> None:
    raise RuntimeError("Credential removal requires an explicit backup and revocation procedure.")
