"""Durable tenant-scoped workflow records, without changing historical rows."""

from alembic import op
import sqlalchemy as sa

revision = "0004_workflow_records"
down_revision = "0003_scrub_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("workflow_records"):
        op.create_table(
            "workflow_records",
            sa.Column("organization_id", sa.String(128), primary_key=True),
            sa.Column("kind", sa.String(64), primary_key=True),
            sa.Column("id", sa.String(128), primary_key=True),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )


def downgrade() -> None:
    raise RuntimeError("Workflow records require an explicit backup before removal.")
