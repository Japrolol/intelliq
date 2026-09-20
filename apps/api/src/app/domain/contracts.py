"""Versioned Pydantic contracts at the Timecue and API boundaries."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field


class SourceMode(StrEnum):
    FIXTURE = "fixture"
    LIVE = "live"


class AnalysisMode(StrEnum):
    BASELINE = "baseline"
    RECOVERY = "recovery"


class ImplementationState(StrEnum):
    REPORTED = "reported"
    PARTIALLY_IMPLEMENTED = "partially_implemented"
    IMPLEMENTED = "implemented"
    NOT_IMPLEMENTED = "not_implemented"


class ObservationType(StrEnum):
    IMPLEMENTATION = "implementation"
    TRANSFER_SETUP = "transfer_setup"


class CheckpointComparator(StrEnum):
    AT_MOST = "at_most"
    AT_LEAST = "at_least"


class SignalReviewStatus(StrEnum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class SessionUser(BaseModel):
    """Identity and permission data revalidated from the upstream account."""

    model_config = ConfigDict(extra="ignore")

    id: str
    email: str
    email_verified_at: datetime | None = Field(default=None, alias="emailVerifiedAt")
    organization_ids: list[str] = Field(default_factory=list, alias="organizationIds")
    organization_permissions: dict[str, list[str]] = Field(
        default_factory=dict, alias="organizationPermissions"
    )
    app_permissions: list[str] = Field(default_factory=list, alias="appPermissions")
    app_admin: bool = Field(default=False, alias="appAdmin")
    source_mode: SourceMode = Field(alias="sourceMode")
    synthetic: bool = False

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class PortfolioSnapshot(BaseModel):
    """The internal, credential-free snapshot consumed by planning and simulation."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    schema_version: int = Field(default=1, alias="schemaVersion")
    snapshot_id: str = Field(alias="snapshotId")
    source_revision: str = Field(alias="sourceRevision")
    organization_id: str = Field(alias="organizationId")
    fetched_at: datetime = Field(alias="fetchedAt")
    as_of: datetime = Field(alias="asOf")
    source_mode: SourceMode = Field(alias="sourceMode")
    complete: bool = True
    warnings: list[str] = Field(default_factory=list)
    omitted_sections: list[str] = Field(default_factory=list, alias="omittedSections")
    projects: list[dict[str, Any]] = Field(default_factory=list)
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    workers: list[dict[str, Any]] = Field(default_factory=list)
    specialties: list[dict[str, Any]] = Field(default_factory=list)
    effective_assignments: list[dict[str, Any]] = Field(
        default_factory=list, alias="effectiveAssignments"
    )
    worklogs: list[dict[str, Any]] = Field(default_factory=list)
    worklog_totals: list[dict[str, Any]] = Field(default_factory=list, alias="worklogTotals")
    reservations: list[dict[str, Any]] = Field(default_factory=list, alias="externalReservations")
    transfer_assumptions: list[dict[str, Any]] = Field(
        default_factory=list, alias="transferAssumptions"
    )


class PlanningInputs(BaseModel):
    """Manager-confirmed overlay kept separate from upstream operational data."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    version: int = Field(default=0, ge=0)
    projects: list[dict[str, Any]] = Field(default_factory=list)
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    workers: list[dict[str, Any]] = Field(default_factory=list)
    transfers: list[dict[str, Any]] = Field(default_factory=list)
    reservations: list[dict[str, Any]] = Field(default_factory=list)


class AnalysisRequest(BaseModel):
    """Request for a reproducible baseline or recovery comparison."""

    project_id: str | None = Field(default=None, alias="projectId", min_length=1, max_length=128)
    mode: AnalysisMode = AnalysisMode.RECOVERY
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)
    expected_snapshot_revision: str | None = Field(default=None, alias="expectedSnapshotRevision")

    model_config = ConfigDict(populate_by_name=True)


class DecisionRequest(BaseModel):
    """Client-supplied decision metadata; metrics are always loaded server-side."""

    analysis_id: str = Field(alias="analysisId", min_length=1, max_length=128)
    strategy_id: str = Field(alias="strategyId", min_length=1, max_length=128)
    rationale: str | None = Field(default=None, max_length=4000)
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=200)
    guardrails: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        default_factory=list, max_length=8
    )
    checkpoints: list["CheckpointDefinition"] = Field(default_factory=list, max_length=8)

    model_config = ConfigDict(populate_by_name=True)


class SignalReviewRequest(BaseModel):
    status: SignalReviewStatus
    planning_change: dict[str, Any] | None = Field(default=None, alias="planningChange")

    model_config = ConfigDict(populate_by_name=True)


class CheckpointDefinition(BaseModel):
    """Immutable checkpoint definition frozen into an approved decision."""

    id: str = Field(min_length=1, max_length=128)
    due_at: datetime = Field(alias="dueAt")
    metric: str = Field(min_length=1, max_length=128)
    comparator: CheckpointComparator
    threshold: float
    evidence_requirement: str | None = Field(
        default=None, alias="evidenceRequirement", max_length=500
    )

    model_config = ConfigDict(populate_by_name=True)


class ImplementationObservationRequest(BaseModel):
    state: ImplementationState
    event_at: datetime | None = Field(default=None, alias="eventAt")
    known_at: datetime | None = Field(default=None, alias="knownAt")
    note: str | None = Field(default=None, max_length=2000)

    model_config = ConfigDict(populate_by_name=True)


class SetupDurationObservationRequest(BaseModel):
    type: ObservationType = ObservationType.TRANSFER_SETUP
    event_at: datetime = Field(alias="eventAt")
    known_at: datetime = Field(alias="knownAt")
    worker_id: str = Field(alias="workerId", min_length=1, max_length=128)
    from_project_id: str = Field(alias="fromProjectId", min_length=1, max_length=128)
    to_project_id: str = Field(alias="toProjectId", min_length=1, max_length=128)
    setup_hours: float = Field(alias="setupHours", ge=0, le=24)
    accepted_for_calibration: bool = Field(default=True, alias="acceptedForCalibration")
    note: str | None = Field(default=None, max_length=2000)

    model_config = ConfigDict(populate_by_name=True)


class CheckpointCheckRequest(BaseModel):
    checkpoint_id: str = Field(alias="checkpointId", min_length=1, max_length=128)
    as_of: datetime | None = Field(default=None, alias="asOf")
    observed_value: float | None = Field(default=None, alias="observedValue")
    evidence: dict[str, Any] | None = None

    model_config = ConfigDict(populate_by_name=True)


class ReplayRequest(BaseModel):
    """Fixture-only request with an explicit synthetic test clock."""

    as_of: datetime = Field(alias="asOf")
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)
    samples: int | None = Field(default=None, ge=1, le=500)

    model_config = ConfigDict(populate_by_name=True)


def jsonable(value: BaseModel | dict[str, Any] | list[Any]) -> Any:
    """Serialize validated models without leaking implementation objects."""

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True)
    return value


__all__ = [
    "AnalysisMode",
    "AnalysisRequest",
    "CheckpointCheckRequest",
    "CheckpointComparator",
    "CheckpointDefinition",
    "DecisionRequest",
    "ImplementationObservationRequest",
    "ImplementationState",
    "ObservationType",
    "PlanningInputs",
    "PortfolioSnapshot",
    "ReplayRequest",
    "SessionUser",
    "SetupDurationObservationRequest",
    "SignalReviewRequest",
    "SignalReviewStatus",
    "SourceMode",
    "jsonable",
]
