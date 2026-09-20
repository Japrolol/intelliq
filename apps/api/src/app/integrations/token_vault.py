"""Encrypted storage for the upstream Timecue session bridge.

The browser-facing session id is persisted by :mod:`persistence.store`, while
this module owns the separate encrypted connection table used for upstream
access and refresh cookies. Database schema creation remains an Alembic
responsibility; the vault never calls ``create_all`` during application start.

``ProcessTokenVault`` remains useful for fixture-mode tests and backwards
compatibility. ``DurableTokenVault`` has the same ``put``/``get``/``clear``
operations, but encrypts cookie values before writing them to PostgreSQL.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from threading import RLock
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import DateTime, Index, Integer, String, Text, create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class TokenVaultBase(DeclarativeBase):
    """Declarative base kept separate so the migration owner controls its DDL."""


class TimecueConnectionRow(TokenVaultBase):
    """Encrypted per-session upstream connection material.

    Nullable binding fields allow the current auth client to write rotated
    cookies before identity revalidation has completed. AuthService supplies
    the binding whenever it knows it.
    """

    __tablename__ = "timecue_connections"
    __table_args__ = (
        Index("ix_timecue_connections_user_id", "user_id"),
        Index("ix_timecue_connections_organization_id", "organization_id"),
    )

    session_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    organization_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    encrypted_access: Mapped[str | None] = mapped_column(Text, nullable=True)
    encrypted_refresh: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    refresh_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    refresh_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


@dataclass(frozen=True)
class UpstreamTokens:
    """The current rotated Timecue cookies for one local session."""

    access: str
    refresh: str
    access_expires_at: datetime | None = None
    refresh_expires_at: datetime | None = None
    refresh_generation: int = 0


class ProcessTokenVault:
    """Thread-safe, non-persistent token storage keyed by local session id."""

    def __init__(self) -> None:
        self._tokens: dict[str, UpstreamTokens] = {}
        self._lock = RLock()

    def put(
        self,
        session_id: str,
        access: str,
        refresh: str,
        *,
        user_id: str | None = None,
        organization_id: str | None = None,
        access_expires_at: datetime | None = None,
        refresh_expires_at: datetime | None = None,
    ) -> None:
        """Replace both upstream cookies atomically after login or rotation.

        Optional durable metadata is accepted so AuthService can use one call
        shape for process and SQL-backed vaults.
        """

        _ = (user_id, organization_id)
        with self._lock:
            previous = self._tokens.get(session_id)
            generation = previous.refresh_generation + 1 if previous else 1
            self._tokens[session_id] = UpstreamTokens(
                access=access,
                refresh=refresh,
                access_expires_at=access_expires_at,
                refresh_expires_at=refresh_expires_at,
                refresh_generation=generation,
            )

    def get(self, session_id: str) -> UpstreamTokens | None:
        """Return a copy-like immutable token pair for one local session."""

        with self._lock:
            return self._tokens.get(session_id)

    def clear(self, session_id: str) -> None:
        """Forget all upstream cookies for a local session."""

        with self._lock:
            self._tokens.pop(session_id, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._tokens)


class TokenVaultError(RuntimeError):
    """The encrypted durable vault cannot safely read or write a connection."""


class DurableTokenVault(ProcessTokenVault):
    """Persist encrypted Timecue cookies in the migration-owned SQL table.

    Args:
        database_url: SQLAlchemy URL for IntelliQ's database.
        encryption_key: Fernet key (URL-safe base64 encoding of 32 bytes).
        engine: Optional shared Store engine. Sharing it is useful for SQLite
            tests and avoids an extra pool in the API process.

    The constructor validates the key but deliberately does not create tables.
    A missing/invalid key is a configuration error, not a reason to fall back
    to process memory in live mode.
    """

    def __init__(
        self,
        database_url: str,
        encryption_key: str,
        *,
        engine: Engine | None = None,
    ) -> None:
        # Keep compatibility base state; durable operations use the SQL store.
        super().__init__()
        try:
            self._fernet = Fernet(encryption_key.encode("ascii"))
        except (ValueError, TypeError, UnicodeEncodeError) as exc:
            raise ValueError("SESSION_ENCRYPTION_KEY must be a valid Fernet key") from exc

        if engine is None:
            connect_args: dict[str, Any] = {}
            if database_url.startswith("sqlite"):
                connect_args["check_same_thread"] = False
            self._engine = create_engine(
                database_url,
                future=True,
                pool_pre_ping=True,
                connect_args=connect_args,
            )
            self._owns_engine = True
        else:
            self._engine = engine
            self._owns_engine = False
        self._session_factory = sessionmaker(self._engine, expire_on_commit=False)

    @property
    def engine(self) -> Engine:
        """Expose the engine for isolated migration tests and diagnostics."""

        return self._engine

    def put(
        self,
        session_id: str,
        access: str,
        refresh: str,
        *,
        user_id: str | None = None,
        organization_id: str | None = None,
        access_expires_at: datetime | None = None,
        refresh_expires_at: datetime | None = None,
    ) -> None:
        """Encrypt and atomically replace both cookies for one session."""

        if not session_id or not access or not refresh:
            raise ValueError("session_id, access and refresh are required")
        now = datetime.now(UTC)
        with self._session_factory.begin() as db:
            row = db.get(TimecueConnectionRow, session_id)
            if row is None:
                row = TimecueConnectionRow(
                    session_id=session_id,
                    user_id=user_id,
                    organization_id=organization_id,
                    refresh_generation=0,
                )
                db.add(row)
            else:
                if user_id is not None:
                    row.user_id = user_id
                if organization_id is not None:
                    row.organization_id = organization_id

            row.encrypted_access = self._fernet.encrypt(access.encode("utf-8")).decode("ascii")
            row.encrypted_refresh = self._fernet.encrypt(refresh.encode("utf-8")).decode("ascii")
            row.access_expires_at = access_expires_at
            row.refresh_expires_at = refresh_expires_at
            row.refresh_generation = int(row.refresh_generation or 0) + 1
            row.revoked_at = None
            row.updated_at = now

    def get(self, session_id: str) -> UpstreamTokens | None:
        """Return decrypted active cookies, or ``None`` for revoked/expired rows."""

        with self._session_factory() as db:
            row = db.get(TimecueConnectionRow, session_id)
            if row is None or row.revoked_at is not None:
                return None
            if not row.encrypted_access or not row.encrypted_refresh:
                return None
            now = datetime.now(UTC)
            if row.refresh_expires_at is not None and _as_utc(row.refresh_expires_at) <= now:
                return None
            try:
                access = self._fernet.decrypt(row.encrypted_access.encode("ascii")).decode("utf-8")
                refresh = self._fernet.decrypt(row.encrypted_refresh.encode("ascii")).decode(
                    "utf-8"
                )
            except (InvalidToken, UnicodeError) as exc:
                raise TokenVaultError("upstream_connection_decryption_failed") from exc
            return UpstreamTokens(
                access=access,
                refresh=refresh,
                access_expires_at=row.access_expires_at,
                refresh_expires_at=row.refresh_expires_at,
                refresh_generation=int(row.refresh_generation or 0),
            )

    def clear(self, session_id: str) -> None:
        """Revoke a session and wipe ciphertext while retaining audit metadata."""

        with self._session_factory.begin() as db:
            row = db.get(TimecueConnectionRow, session_id)
            if row is None:
                return
            now = datetime.now(UTC)
            row.encrypted_access = None
            row.encrypted_refresh = None
            row.revoked_at = now
            row.updated_at = now

    def __len__(self) -> int:
        with self._session_factory() as db:
            return int(
                db.scalar(
                    select(func.count())
                    .select_from(TimecueConnectionRow)
                    .where(
                        TimecueConnectionRow.revoked_at.is_(None),
                        TimecueConnectionRow.encrypted_access.is_not(None),
                        TimecueConnectionRow.encrypted_refresh.is_not(None),
                    )
                )
                or 0
            )

    def close(self) -> None:
        """Dispose an internally created engine without closing a shared Store engine."""

        if self._owns_engine:
            self._engine.dispose()


def _as_utc(value: datetime) -> datetime:
    """Normalize SQLite's naive datetime values before expiry comparison."""

    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


__all__ = [
    "DurableTokenVault",
    "ProcessTokenVault",
    "TimecueConnectionRow",
    "TokenVaultBase",
    "TokenVaultError",
    "UpstreamTokens",
]
