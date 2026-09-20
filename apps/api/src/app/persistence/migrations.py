"""Alembic bootstrap for IntelliQ-owned persistence.

The application starts only after the committed migration head has been
applied. A database created by the earlier ``create_all`` bootstrap is adopted
by the baseline migration: existing tables are left in place, missing tables
are created, and the revision is recorded. No SQLite data is copied and no
tables are dropped during an upgrade.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import Engine, inspect
from sqlalchemy.engine import Connection

from alembic import command

ALEMBIC_INI = Path(__file__).resolve().parents[3] / "alembic.ini"
ALEMBIC_SCRIPT_LOCATION = ALEMBIC_INI.parent / "alembic"


def migrate_database(engine: Engine) -> None:
    """Upgrade ``engine`` to the current Alembic head.

    If a pre-Alembic database already contains IntelliQ tables, validate that
    existing tables have the baseline columns before allowing Alembic to stamp
    and extend the schema. This turns an ambiguous partial schema into an
    actionable startup error instead of silently claiming it is compatible.
    """

    with engine.begin() as connection:
        _validate_unversioned_schema(connection)
        config = _alembic_config(connection)
        command.upgrade(config, "head")


def _alembic_config(connection: Connection) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_SCRIPT_LOCATION))
    config.attributes["connection"] = connection
    return config


def _validate_unversioned_schema(connection: Connection) -> None:
    """Reject known IntelliQ tables that are missing required baseline columns."""

    inspector = inspect(connection)
    if inspector.has_table("alembic_version"):
        return

    from src.app.persistence.store import Base

    existing_tables = set(inspector.get_table_names())
    model_tables = set(Base.metadata.tables)
    for table_name in sorted(existing_tables & model_tables):
        actual_columns = {column["name"] for column in inspector.get_columns(table_name)}
        required_columns = {column.name for column in Base.metadata.tables[table_name].columns}
        missing_columns = sorted(required_columns - actual_columns)
        if missing_columns:
            missing = ", ".join(missing_columns)
            raise RuntimeError(
                f"Unversioned table {table_name!r} is missing required columns: {missing}. "
                "Restore a compatible backup or write an explicit migration before startup."
            )


__all__ = ["migrate_database"]
