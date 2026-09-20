import pytest
from pydantic import ValidationError
from sqlalchemy import inspect
from sqlalchemy.pool import StaticPool

from src.app.config import DEFAULT_DATABASE_URL, Settings
from src.app.persistence.store import Store


def test_settings_default_to_local_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    """The process default is an explicit psycopg URL on the reserved dev port."""

    monkeypatch.delenv("DATABASE_URL", raising=False)

    settings = Settings(_env_file=None)

    assert settings.database_url == DEFAULT_DATABASE_URL
    assert settings.database_url.startswith("postgresql+psycopg://")
    assert "@127.0.0.1:5434/" in settings.database_url


def test_empty_database_url_is_rejected_instead_of_falling_back() -> None:
    """An explicitly empty URL must fail configuration rather than select SQLite."""

    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="")


def test_postgres_store_enables_pre_ping_without_connecting() -> None:
    """The PostgreSQL engine carries connection-health checks at construction time."""

    store = Store(DEFAULT_DATABASE_URL)
    try:
        assert store.engine.url.drivername == "postgresql+psycopg"
        assert store.engine.pool._pre_ping is True
    finally:
        store.engine.dispose()


def test_explicit_sqlite_memory_store_keeps_isolated_test_support() -> None:
    """An explicit in-memory SQLite URL still uses one shared test connection."""

    store = Store("sqlite:///:memory:")
    try:
        assert isinstance(store.engine.pool, StaticPool)
        store.create_schema()
        assert inspect(store.engine).has_table("sessions")
    finally:
        store.engine.dispose()
