"""Small replaceable persistence layer for the single-process hackathon API.

The process default is PostgreSQL through the ``psycopg`` SQLAlchemy driver.
Callers may still pass SQLite explicitly for isolated tests, including the
in-memory ``StaticPool`` path.
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    delete,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class SessionRow(Base):
    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_user_id", "user_id"),)

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    verified: Mapped[int] = mapped_column(Integer, nullable=False)
    organizations_json: Mapped[str] = mapped_column(Text, nullable=False)
    permissions_json: Mapped[str] = mapped_column(Text, nullable=False)
    source_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class PlanningRow(Base):
    __tablename__ = "planning_inputs"

    organization_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class SnapshotRow(Base):
    __tablename__ = "snapshots"
    __table_args__ = (Index("ix_snapshots_org_fetched_at", "organization_id", "fetched_at"),)

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_revision: Mapped[str] = mapped_column(String(256), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class AnalysisRow(Base):
    __tablename__ = "analyses"
    __table_args__ = (Index("ix_analyses_org_created_at", "organization_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class SignalRow(Base):
    __tablename__ = "signals"
    __table_args__ = (
        Index("ix_signals_org_project_updated_at", "organization_id", "project_id", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class DecisionRow(Base):
    __tablename__ = "decision_contracts"
    __table_args__ = (
        UniqueConstraint("organization_id", "idempotency_key", name="uq_decision_idempotency"),
        Index("ix_decisions_org_created_at", "organization_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    analysis_id: Mapped[str] = mapped_column(String(128), nullable=False)
    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class DecisionObservationRow(Base):
    __tablename__ = "decision_observations"
    __table_args__ = (
        Index(
            "ix_observations_org_decision_created_at",
            "organization_id",
            "decision_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(128), nullable=False)
    decision_id: Mapped[str] = mapped_column(String(128), nullable=False)
    observation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class CheckpointEvaluationRow(Base):
    __tablename__ = "checkpoint_evaluations"
    __table_args__ = (
        Index(
            "ix_checkpoint_org_decision_evaluated_at",
            "organization_id",
            "decision_id",
            "evaluated_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(128), nullable=False)
    decision_id: Mapped[str] = mapped_column(String(128), nullable=False)
    checkpoint_id: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class CalibrationRow(Base):
    __tablename__ = "calibration_versions"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "source_mode",
            "version",
            name="uq_calibration_version",
        ),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class SessionRecord:
    """Serializable local session state; upstream tokens never leave this boundary."""

    def __init__(
        self,
        session_id: str,
        user_id: str,
        email: str,
        verified: bool,
        organization_ids: list[str],
        permissions: dict[str, list[str]],
        source_mode: str,
        csrf_token: str,
    ) -> None:
        self.session_id = session_id
        self.user_id = user_id
        self.email = email
        self.verified = verified
        self.organization_ids = organization_ids
        self.permissions = permissions
        self.source_mode = source_mode
        self.csrf_token = csrf_token


class ExtensionRow(Base):
    """Tenant-scoped durable workflow records for the v4 vertical slices."""

    __tablename__ = "workflow_records"
    organization_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Store:
    """Own all IntelliQ local records and enforce organization-keyed lookups."""

    def __init__(self, database_url: str) -> None:
        connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        engine_kwargs: dict[str, Any] = {
            "future": True,
            "pool_pre_ping": True,
            "connect_args": connect_args,
        }
        if database_url == "sqlite:///:memory:":
            engine_kwargs["poolclass"] = StaticPool
        self.engine = create_engine(database_url, **engine_kwargs)
        self._session_factory = sessionmaker(self.engine, expire_on_commit=False)

    def migrate_schema(self) -> None:
        """Apply committed Alembic upgrades without replacing existing tables.

        The migration runner is shared by startup and the explicit database
        command so a fresh database and an existing pre-Alembic database follow
        the same deterministic upgrade path.
        """

        from src.app.persistence.migrations import migrate_database

        migrate_database(self.engine)

    def create_schema(self) -> None:
        """Backward-compatible alias for the versioned schema bootstrap."""

        self.migrate_schema()

    def create_session(self, record: SessionRecord) -> None:
        with self._session_factory.begin() as db:
            db.add(
                SessionRow(
                    id=record.session_id,
                    user_id=record.user_id,
                    email=record.email,
                    verified=int(record.verified),
                    organizations_json=json.dumps(record.organization_ids),
                    permissions_json=json.dumps(record.permissions),
                    source_mode=record.source_mode,
                    csrf_token=record.csrf_token,
                )
            )

    def get_session(self, session_id: str) -> SessionRecord | None:
        with self._session_factory() as db:
            row = db.get(SessionRow, session_id)
            if row is None:
                return None
            return _session_record(row)

    def update_session_identity(
        self,
        session_id: str,
        user_id: str,
        email: str,
        verified: bool,
        organization_ids: list[str],
        permissions: dict[str, list[str]],
    ) -> SessionRecord | None:
        with self._session_factory.begin() as db:
            row = db.get(SessionRow, session_id)
            if row is None:
                return None
            row.user_id = user_id
            row.email = email
            row.verified = int(verified)
            row.organizations_json = json.dumps(organization_ids)
            row.permissions_json = json.dumps(permissions)
            return _session_record(row)

    def delete_session(self, session_id: str) -> None:
        with self._session_factory.begin() as db:
            row = db.get(SessionRow, session_id)
            if row is not None:
                db.delete(row)

    def save_snapshot(self, payload: dict[str, Any]) -> None:
        snapshot_id = str(payload["snapshotId"])
        with self._session_factory.begin() as db:
            existing = db.get(SnapshotRow, snapshot_id)
            if existing and existing.organization_id != str(payload["organizationId"]):
                raise ValueError("snapshot_tenant_conflict")
            db.merge(
                SnapshotRow(
                    id=snapshot_id,
                    organization_id=str(payload["organizationId"]),
                    source_revision=str(payload["sourceRevision"]),
                    payload_json=json.dumps(payload),
                    fetched_at=_now(),
                )
            )

    def get_planning(self, organization_id: str) -> dict[str, Any] | None:
        with self._session_factory() as db:
            row = db.get(PlanningRow, organization_id)
            return None if row is None else json.loads(row.payload_json)

    def acquire_lease(self, organization_id: str, name: str, seconds: int = 180) -> str | None:
        """Atomically acquire a bounded lease without a transaction over network IO."""
        token = secrets.token_urlsafe(24)
        now = _now()
        try:
            with self._session_factory.begin() as db:
                db.add(
                    ExtensionRow(
                        organization_id=organization_id,
                        kind="lease",
                        id=name,
                        payload_json=token,
                        updated_at=now,
                    )
                )
            return token
        except IntegrityError:
            with self._session_factory.begin() as db:
                result = db.execute(
                    update(ExtensionRow)
                    .where(
                        ExtensionRow.organization_id == organization_id,
                        ExtensionRow.kind == "lease",
                        ExtensionRow.id == name,
                        ExtensionRow.updated_at < now - timedelta(seconds=seconds),
                    )
                    .values(payload_json=token, updated_at=now)
                )
                return token if result.rowcount else None

    def renew_lease(self, organization_id: str, name: str, token: str) -> bool:
        with self._session_factory.begin() as db:
            result = db.execute(
                update(ExtensionRow)
                .where(
                    ExtensionRow.organization_id == organization_id,
                    ExtensionRow.kind == "lease",
                    ExtensionRow.id == name,
                    ExtensionRow.payload_json == token,
                )
                .values(updated_at=_now())
            )
            return bool(result.rowcount)

    def release_lease(self, organization_id: str, name: str, token: str) -> None:
        with self._session_factory.begin() as db:
            db.execute(
                delete(ExtensionRow).where(
                    ExtensionRow.organization_id == organization_id,
                    ExtensionRow.kind == "lease",
                    ExtensionRow.id == name,
                    ExtensionRow.payload_json == token,
                )
            )

    def get_record(self, organization_id: str, kind: str, record_id: str) -> dict[str, Any] | None:
        """Read an extension record using its complete tenant-scoped identity."""
        with self._session_factory() as db:
            row = db.get(ExtensionRow, (organization_id, kind, record_id))
            return json.loads(row.payload_json) if row else None

    def list_records(self, organization_id: str, kind: str) -> list[dict[str, Any]]:
        """List one tenant's extension records newest first."""
        with self._session_factory() as db:
            rows = db.scalars(
                select(ExtensionRow)
                .where(ExtensionRow.organization_id == organization_id, ExtensionRow.kind == kind)
                .order_by(ExtensionRow.updated_at.desc())
            ).all()
            return [json.loads(row.payload_json) for row in rows]

    def save_record(
        self, organization_id: str, kind: str, record_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Upsert an explicitly scoped workflow record while retaining creation time."""
        return self.save_records(organization_id, [(kind, record_id, payload)])[0]

    def save_records(
        self, organization_id: str, records: list[tuple[str, str, dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        """Commit related workflow and outbox records in one transaction."""
        values = []
        with self._session_factory.begin() as db:
            for kind, record_id, payload in records:
                row = db.get(ExtensionRow, (organization_id, kind, record_id))
                previous = json.loads(row.payload_json) if row else {}
                now = _now()
                value = {
                    **payload,
                    "id": record_id,
                    "organizationId": organization_id,
                    "createdAt": previous.get("createdAt", now.isoformat()),
                    "updatedAt": now.isoformat(),
                }
                if row is None:
                    row = ExtensionRow(organization_id=organization_id, kind=kind, id=record_id)
                    db.add(row)
                row.payload_json = json.dumps(value)
                row.updated_at = now
                values.append(value)
        return values

    def save_planning(
        self, organization_id: str, payload: dict[str, Any], expected_version: int | None
    ) -> dict[str, Any]:
        with self._session_factory.begin() as db:
            row = db.get(PlanningRow, organization_id)
            current_version = row.version if row else 0
            if expected_version is not None and current_version != expected_version:
                raise ValueError(f"planning_version_conflict:{current_version}")
            next_version = current_version + 1
            payload = {**payload, "version": next_version}
            if row is None:
                db.add(
                    PlanningRow(
                        organization_id=organization_id,
                        version=next_version,
                        payload_json=json.dumps(payload),
                        updated_at=_now(),
                    )
                )
            else:
                row.version = next_version
                row.payload_json = json.dumps(payload)
                row.updated_at = _now()
            return payload

    def create_analysis(
        self,
        organization_id: str,
        user_id: str,
        payload: dict[str, Any],
        status: str,
        frozen_input: dict[str, Any] | None = None,
    ) -> str:
        analysis_id = str(uuid4())
        payload = {**payload, "id": analysis_id, "status": status}
        with self._session_factory.begin() as db:
            if payload.get("runKey"):
                key = str(payload["runKey"])
                existing = db.get(ExtensionRow, (organization_id, "analysis_run", key))
                if existing:
                    return json.loads(existing.payload_json)["analysisId"]
                db.add(
                    ExtensionRow(
                        organization_id=organization_id,
                        kind="analysis_run",
                        id=key,
                        payload_json=json.dumps({"analysisId": analysis_id}),
                        updated_at=_now(),
                    )
                )
            if frozen_input is not None:
                db.add(
                    ExtensionRow(
                        organization_id=organization_id,
                        kind="analysis_input",
                        id=analysis_id,
                        payload_json=json.dumps(frozen_input),
                        updated_at=_now(),
                    )
                )
            db.add(
                AnalysisRow(
                    id=analysis_id,
                    organization_id=organization_id,
                    user_id=user_id,
                    status=status,
                    payload_json=json.dumps(payload),
                    created_at=_now(),
                )
            )
        return analysis_id

    def list_analyses(self, organization_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._session_factory() as db:
            rows = db.scalars(
                select(AnalysisRow)
                .where(AnalysisRow.organization_id == organization_id)
                .order_by(AnalysisRow.created_at.desc(), AnalysisRow.id.desc())
                .limit(limit)
            ).all()
            return [{**json.loads(row.payload_json), "createdAt": row.created_at} for row in rows]

    def get_analysis(self, organization_id: str, analysis_id: str) -> dict[str, Any] | None:
        with self._session_factory() as db:
            row = db.get(AnalysisRow, analysis_id)
            if row is None or row.organization_id != organization_id:
                return None
            return json.loads(row.payload_json)

    def save_signal(
        self,
        organization_id: str,
        signal_id: str,
        project_id: str | None,
        status: str,
        payload: dict[str, Any],
    ) -> None:
        with self._session_factory.begin() as db:
            current = db.get(SignalRow, signal_id)
            if current is not None and current.status in {
                "confirmed",
                "rejected",
                "superseded",
            }:
                return
            source_id = str(payload.get("sourceId", ""))
            source_revision = str(payload.get("sourceRevision", ""))
            if source_id and source_revision:
                for prior in db.scalars(
                    select(SignalRow).where(SignalRow.organization_id == organization_id)
                ).all():
                    if prior.id == signal_id:
                        continue
                    prior_payload = json.loads(prior.payload_json)
                    if (
                        str(prior_payload.get("sourceId", "")) == source_id
                        and str(prior_payload.get("sourceRevision", "")) != source_revision
                    ):
                        prior.status = "superseded"
                        prior_payload["status"] = "superseded"
                        prior_payload["supersededBy"] = signal_id
                        prior.payload_json = json.dumps(prior_payload)
                        prior.updated_at = _now()
            db.merge(
                SignalRow(
                    id=signal_id,
                    organization_id=organization_id,
                    project_id=project_id,
                    status=status,
                    payload_json=json.dumps(payload),
                    updated_at=_now(),
                )
            )

    def list_signals(
        self, organization_id: str, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        with self._session_factory() as db:
            statement = select(SignalRow).where(SignalRow.organization_id == organization_id)
            if project_id is not None:
                statement = statement.where(SignalRow.project_id == project_id)
            return [json.loads(row.payload_json) for row in db.scalars(statement).all()]

    def review_signal(
        self, organization_id: str, signal_id: str, status: str, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        with self._session_factory.begin() as db:
            row = db.get(SignalRow, signal_id)
            if row is None or row.organization_id != organization_id:
                return None
            row.status = status
            row.payload_json = json.dumps(payload)
            row.updated_at = _now()
            return payload

    def get_decision_by_idempotency(self, organization_id: str, key: str) -> dict[str, Any] | None:
        with self._session_factory() as db:
            row = db.scalars(
                select(DecisionRow).where(
                    DecisionRow.organization_id == organization_id,
                    DecisionRow.idempotency_key == key,
                )
            ).first()
            return None if row is None else json.loads(row.payload_json)

    def create_decision(
        self, organization_id: str, user_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        decision_id = str(uuid4())
        payload = {**payload, "id": decision_id}
        with self._session_factory.begin() as db:
            db.add(
                DecisionRow(
                    id=decision_id,
                    organization_id=organization_id,
                    user_id=user_id,
                    analysis_id=str(payload["analysisId"]),
                    strategy_id=str(payload["strategyId"]),
                    idempotency_key=str(payload["idempotencyKey"]),
                    payload_json=json.dumps(payload),
                    created_at=_now(),
                )
            )
        return payload

    def list_decisions(self, organization_id: str) -> list[dict[str, Any]]:
        with self._session_factory() as db:
            rows = db.scalars(
                select(DecisionRow)
                .where(DecisionRow.organization_id == organization_id)
                .order_by(DecisionRow.created_at.desc())
            ).all()
            return [json.loads(row.payload_json) for row in rows]

    def get_decision(self, organization_id: str, decision_id: str) -> dict[str, Any] | None:
        with self._session_factory() as db:
            row = db.get(DecisionRow, decision_id)
            if row is None or row.organization_id != organization_id:
                return None
            return json.loads(row.payload_json)

    def create_observation(
        self,
        organization_id: str,
        decision_id: str,
        observation_type: str,
        source_mode: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        observation_id = str(uuid4())
        record = {**payload, "id": observation_id, "decisionId": decision_id}
        with self._session_factory.begin() as db:
            db.add(
                DecisionObservationRow(
                    id=observation_id,
                    organization_id=organization_id,
                    decision_id=decision_id,
                    observation_type=observation_type,
                    source_mode=source_mode,
                    payload_json=json.dumps(record),
                    created_at=_now(),
                )
            )
        return record

    def list_observations(self, organization_id: str, decision_id: str) -> list[dict[str, Any]]:
        with self._session_factory() as db:
            rows = db.scalars(
                select(DecisionObservationRow)
                .where(
                    DecisionObservationRow.organization_id == organization_id,
                    DecisionObservationRow.decision_id == decision_id,
                )
                .order_by(DecisionObservationRow.created_at.asc())
            ).all()
            return [json.loads(row.payload_json) for row in rows]

    def create_checkpoint_evaluation(
        self,
        organization_id: str,
        decision_id: str,
        checkpoint_id: str,
        state: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        evaluation_id = str(uuid4())
        record = {
            **payload,
            "id": evaluation_id,
            "decisionId": decision_id,
            "checkpointId": checkpoint_id,
            "state": state,
        }
        with self._session_factory.begin() as db:
            db.add(
                CheckpointEvaluationRow(
                    id=evaluation_id,
                    organization_id=organization_id,
                    decision_id=decision_id,
                    checkpoint_id=checkpoint_id,
                    state=state,
                    payload_json=json.dumps(record),
                    evaluated_at=_now(),
                )
            )
        return record

    def list_checkpoint_evaluations(
        self, organization_id: str, decision_id: str
    ) -> list[dict[str, Any]]:
        with self._session_factory() as db:
            rows = db.scalars(
                select(CheckpointEvaluationRow)
                .where(
                    CheckpointEvaluationRow.organization_id == organization_id,
                    CheckpointEvaluationRow.decision_id == decision_id,
                )
                .order_by(CheckpointEvaluationRow.evaluated_at.asc())
            ).all()
            return [json.loads(row.payload_json) for row in rows]

    def get_calibration(self, organization_id: str, source_mode: str) -> dict[str, Any] | None:
        with self._session_factory() as db:
            row = db.scalars(
                select(CalibrationRow)
                .where(
                    CalibrationRow.organization_id == organization_id,
                    CalibrationRow.source_mode == source_mode,
                )
                .order_by(CalibrationRow.version.desc())
            ).first()
            return None if row is None else json.loads(row.payload_json)

    def list_calibration_history(
        self, organization_id: str, source_mode: str
    ) -> list[dict[str, Any]]:
        with self._session_factory() as db:
            rows = db.scalars(
                select(CalibrationRow)
                .where(
                    CalibrationRow.organization_id == organization_id,
                    CalibrationRow.source_mode == source_mode,
                )
                .order_by(CalibrationRow.version.asc())
            ).all()
            return [json.loads(row.payload_json) for row in rows]

    def create_calibration(
        self,
        organization_id: str,
        source_mode: str,
        version: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        calibration_id = str(uuid4())
        record = {**payload, "id": calibration_id, "version": version}
        with self._session_factory.begin() as db:
            db.add(
                CalibrationRow(
                    id=calibration_id,
                    organization_id=organization_id,
                    source_mode=source_mode,
                    version=version,
                    payload_json=json.dumps(record),
                    created_at=_now(),
                )
            )
        return record

    def record_calibrated_outcome(
        self,
        organization_id: str,
        source_mode: str,
        version: int,
        calibration: dict,
        observation_id: str,
        observation: dict,
    ) -> dict:
        """Commit the observation and its calibration exactly once together."""
        with self._session_factory.begin() as db:
            existing = db.get(ExtensionRow, (organization_id, "outcome", observation_id))
            if existing:
                return json.loads(existing.payload_json)
            calibration = {**calibration, "id": str(uuid4()), "version": version}
            record = {
                **observation,
                "id": observation_id,
                "calibration": calibration,
                "updatedAt": _now().isoformat(),
            }
            db.add(
                CalibrationRow(
                    id=calibration["id"],
                    organization_id=organization_id,
                    source_mode=source_mode,
                    version=version,
                    payload_json=json.dumps(calibration),
                    created_at=_now(),
                )
            )
            db.add(
                ExtensionRow(
                    organization_id=organization_id,
                    kind="outcome",
                    id=observation_id,
                    payload_json=json.dumps(record),
                    updated_at=_now(),
                )
            )
            return record


def _session_record(row: SessionRow) -> SessionRecord:
    return SessionRecord(
        session_id=row.id,
        user_id=row.user_id,
        email=row.email,
        verified=bool(row.verified),
        organization_ids=json.loads(row.organizations_json),
        permissions=json.loads(row.permissions_json),
        source_mode=row.source_mode,
        csrf_token=row.csrf_token,
    )


def new_session_id() -> str:
    return secrets.token_urlsafe(32)


__all__ = ["SessionRecord", "Store", "new_session_id"]
