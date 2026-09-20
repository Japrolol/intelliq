from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, inspect, text

from src.app.persistence.migrations import migrate_database
from src.app.persistence.store import Base


def test_fresh_database_reaches_head_with_expected_tables_and_indexes(tmp_path) -> None:
    database = tmp_path / "fresh.db"
    engine = create_engine(f"sqlite:///{database}")

    try:
        migrate_database(engine)

        inspector = inspect(engine)
        assert {
            "alembic_version",
            "sessions",
            "planning_inputs",
            "snapshots",
            "analyses",
            "signals",
            "decision_contracts",
            "decision_observations",
            "checkpoint_evaluations",
            "calibration_versions",
        }.issubset(inspector.get_table_names())
        assert {
            index["name"] for index in inspector.get_indexes("signals")
        } >= {"ix_signals_org_project_updated_at"}
        with engine.connect() as connection:
            version = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            assert version == "0005_timecue_connections"
        assert {"workflow_records", "timecue_connections"}.issubset(inspector.get_table_names())
    finally:
        engine.dispose()


def test_existing_create_all_schema_is_adopted_without_losing_rows(tmp_path) -> None:
    database = tmp_path / "existing.db"
    engine = create_engine(f"sqlite:///{database}")

    try:
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE sessions ADD COLUMN upstream_access TEXT"))
            connection.execute(text("ALTER TABLE sessions ADD COLUMN upstream_refresh TEXT"))
            connection.execute(
                text(
                    "INSERT INTO sessions "
                    "(id, user_id, email, verified, organizations_json, permissions_json, "
                    "source_mode, csrf_token, created_at, upstream_access, upstream_refresh) "
                    "VALUES (:id, :user_id, :email, :verified, :organizations, :permissions, "
                    ":source_mode, :csrf, :created_at, :access, :refresh)"
                ),
                {
                    "id": "existing-session",
                    "user_id": "user-1",
                    "email": "manager@example.com",
                    "verified": 1,
                    "organizations": "[\"org-1\"]",
                    "permissions": "{\"org-1\": [\"portfolio.read\"]}",
                    "source_mode": "live",
                    "csrf": "csrf-token",
                    "created_at": datetime.now(UTC),
                    "access": "must-be-scrubbed",
                    "refresh": "must-be-scrubbed",
                },
            )

        migrate_database(engine)
        migrate_database(engine)

        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT email, upstream_access, upstream_refresh "
                    "FROM sessions WHERE id = :id"
                ),
                {"id": "existing-session"},
            ).one()
            assert row.email == "manager@example.com"
            assert row.upstream_access is None
            assert row.upstream_refresh is None
            assert connection.execute(text("SELECT COUNT(*) FROM sessions")).scalar_one() == 1
    finally:
        engine.dispose()


def test_partial_unversioned_schema_fails_before_adoption(tmp_path) -> None:
    database = tmp_path / "partial.db"
    engine = create_engine(f"sqlite:///{database}")

    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE sessions (id VARCHAR(128) PRIMARY KEY)"))

        with pytest.raises(RuntimeError, match="missing required columns"):
            migrate_database(engine)
    finally:
        engine.dispose()
