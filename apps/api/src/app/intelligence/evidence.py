"""Bounded, review-first extraction of operational evidence.

The lexical extractor deliberately reports what a source note says rather than
turning it into a scheduling input.  It has no network access and does not
infer task links from task titles.  A provider can be injected for a later
deployment, but it is only called when explicitly enabled in
``ExtractionConfig``; provider output is validated against the trusted source
context before it is returned.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import StrEnum
from typing import Protocol


class EvidenceExtractionError(ValueError):
    """Raised when source metadata or extracted evidence is not valid."""


class SignalKind(StrEnum):
    """Small, intentionally bounded operational signal taxonomy."""

    RESOURCE_CONSTRAINT = "resource_constraint"
    DEPENDENCY_BLOCKER = "dependency_blocker"
    MATERIAL_WAIT = "material_wait"
    WEATHER_DISRUPTION = "weather_disruption"
    REMAINING_WORK_REPORT = "remaining_work_report"
    REPORTED_SLOW_PROGRESS = "reported_slow_progress"
    RESOLUTION = "resolution"


class Assertion(StrEnum):
    """How the source expresses the signal."""

    REPORTED = "reported"
    HYPOTHETICAL = "hypothetical"
    NEGATED = "negated"
    UNCLEAR = "unclear"


class ReportedState(StrEnum):
    """Temporal or operational state reported by the source."""

    ACTIVE = "active"
    RESOLVED = "resolved"
    HISTORICAL = "historical"
    UNKNOWN = "unknown"


class ReviewStatus(StrEnum):
    """Review lifecycle for evidence shown to a manager."""

    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


@dataclass(frozen=True, slots=True)
class ExtractionContext:
    """Trusted metadata supplied by the authorized backend boundary.

    ``task_id`` is an existing canonical source link, not an extractor guess.
    ``canonical_task_ids`` is the complete allowed set for this extraction
    scope.  The lexical extractor may expose explicit IDs from source text as
    candidates, but it never promotes a title or a phrase into a task link.
    """

    source_id: str
    source_revision: str
    source_kind: str
    canonical_task_ids: tuple[str, ...] = ()
    project_id: str | None = None
    task_id: str | None = None
    work_date: date | None = None
    known_at: datetime | None = None
    timezone: str = "UTC"

    def __post_init__(self) -> None:
        object.__setattr__(self, "canonical_task_ids", tuple(self.canonical_task_ids))


@dataclass(frozen=True, slots=True)
class ExtractionConfig:
    """Bounds and explicit provider opt-in for one extraction call."""

    max_source_chars: int = 12_000
    max_observations: int = 32
    provider_enabled: bool = False
    provider: EvidenceProvider | None = None


@dataclass(frozen=True, slots=True)
class SourceReference:
    """Immutable source identity attached to every observation."""

    source_id: str
    source_revision: str
    source_kind: str
    project_id: str | None
    task_id: str | None
    work_date: date | None
    known_at: datetime | None
    timezone: str


@dataclass(frozen=True, slots=True)
class ReportedQuantity:
    """A quantity quoted by the source, never treated as a forecast input."""

    value: float
    unit: str
    crew_scope: str | None = None


@dataclass(frozen=True, slots=True)
class EvidenceQuote:
    """Exact source substring and its server-derived offsets."""

    text: str
    start: int = -1
    end: int = -1


@dataclass(frozen=True, slots=True)
class Observation:
    """One source-backed signal awaiting or having completed manager review."""

    signal_id: str
    kind: SignalKind
    assertion: Assertion
    reported_state: ReportedState
    summary: str
    evidence_quotes: tuple[EvidenceQuote, ...]
    source: SourceReference
    linked_task_id: str | None = None
    related_task_candidate_ids: tuple[str, ...] = ()
    specialty_candidate_id: str | None = None
    date_expression: str | None = None
    proposed_date: date | None = None
    reported_quantity: ReportedQuantity | None = None
    ambiguity_reasons: tuple[str, ...] = ()
    review_status: ReviewStatus = ReviewStatus.PROPOSED


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """Validated extractor output suitable for a review surface."""

    observations: tuple[Observation, ...]
    no_signal: bool
    mode: str
    limitations: tuple[str, ...] = ()


class EvidenceProvider(Protocol):
    """Optional provider adapter; implementations must not bypass validation."""

    def extract(self, source_text: str, context: ExtractionContext) -> ExtractionResult:
        """Return candidate observations for the supplied source and context."""
        ...


_DEFAULT_LIMITATION = (
    "Lexical fallback is bounded and cannot establish semantic truth; review is required."
)
_PROVIDER_LIMITATION = "Provider output is untrusted evidence and remains proposed until reviewed."
LEXICAL_EXTRACTOR_VERSION = "evidence-v2"
_IDENTIFIER_RE = re.compile(r"^[^\s]{1,200}$")
_SEGMENT_RE = re.compile(r"[^.!?\n]+(?:[.!?]+|$)", re.MULTILINE)
_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_RELATIVE_DATE_RE = re.compile(
    r"\b(?:today|yesterday|tomorrow|last\s+(?:week|month)|this\s+week|next\s+week)\b",
    re.IGNORECASE,
)
_QUANTITY_RE = re.compile(
    r"\b(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>hours?|hrs?|days?|workers?|people|crew)\b",
    re.IGNORECASE,
)

_WEATHER_RE = re.compile(
    r"\b(?:rain|raining|storm|storms|wind|windy|snow|snowing|frost|ice|weather|wet|muddy|"
    r"temperature|heat|hot|cold|lightning|workability)\b",
    re.IGNORECASE,
)
_MATERIAL_RE = re.compile(
    r"\b(?:material|materials|delivery|deliveries|shipment|shipments|cables?|concrete|"
    r"steel|windows?|doors?|supplies|parts?)\b",
    re.IGNORECASE,
)
_DEPENDENCY_RE = re.compile(
    r"(?:\b(?:blocked|blocker|blocking|waiting\s+for|awaiting|depends?\s+on|cannot\s+close|"
    r"can't\s+close|unable\s+to|unfinished|not\s+finished|not\s+complete|holds?\s+up)\b|"
    r"\bbecause\s+[^.!?]{0,100}\b(?:unfinished|incomplete|not\s+ready|missing)\b)",
    re.IGNORECASE,
)
_RESOURCE_RE = re.compile(
    r"\b(?:no\s+(?:electrician|carpenter|worker|crew|labou?r|staff)|short(?:age)?\s+of|"
    r"understaffed|not\s+enough\s+(?:workers?|crew|people)|need\s+(?:an?\s+)?(?:worker|crew)|"
    r"resource\s+(?:constraint|shortage)|capacity\s+(?:constraint|shortage))\b",
    re.IGNORECASE,
)
_REMAINING_RE = re.compile(
    r"\b(?:remaining|left\s+to|still\s+need|needs?\s+\d|more\s+work|work\s+left|"
    r"to\s+finish|unfinished\s+work)\b",
    re.IGNORECASE,
)
_SLOW_RE = re.compile(
    r"\b(?:slow(?:er|ly)?|behind|delayed|delay|taking\s+longer|progress\s+is\s+slow|"
    r"fell\s+behind|late)\b",
    re.IGNORECASE,
)
_RESOLUTION_RE = re.compile(
    r"(?:\b(?:resolved|fixed|cleared|completed|finished|complete|arrived|delivered)\b|"
    r"\b(?:no\s+longer|not\s+anymore)\b)",
    re.IGNORECASE,
)
_NEGATIVE_COMPLETION_RE = re.compile(
    r"\b(?:not|never)\s+(?:yet\s+)?(?:finished|complete(?:d)?|arrived|delivered|done)\b|"
    r"\b(?:unfinished|incomplete)\b",
    re.IGNORECASE,
)
_HYPOTHETICAL_RE = re.compile(
    r"\b(?:if|unless|would|could|might|may|assuming|in\s+case|should\s+we|"
    r"expected\s+to|expect(?:ed)?\s+that)\b",
    re.IGNORECASE,
)
_HISTORICAL_RE = re.compile(
    r"\b(?:yesterday|last\s+(?:week|month)|earlier|previously|was|were|had\s+been|"
    r"in\s+the\s+past|historically)\b",
    re.IGNORECASE,
)
_NEGATED_RE = re.compile(
    r"(?:\b(?:no|never|without)\s+(?:delay|delays|blocker|blockers|issue|issues|"
    r"problem|problems|shortage|constraint)\b|"
    r"\b(?:not|isn't|aren't|wasn't|weren't)\s+(?:blocked|delayed|waiting|late|behind|"
    r"an?\s+issue|a\s+problem)\b)",
    re.IGNORECASE,
)
_RESOLVED_NEGATION_RE = re.compile(
    r"\b(?:no\s+longer|not\s+anymore)\s+(?:blocked|waiting|delayed|late|missing)\b",
    re.IGNORECASE,
)


def extract_signals(
    source_text: str,
    context: ExtractionContext,
    *,
    config: ExtractionConfig | None = None,
) -> ExtractionResult:
    """Extract bounded, reviewable operational signals from authorized text.

    The default path performs only deterministic local lexical matching.  A
    provider is called only if ``config.provider_enabled`` is true and a
    provider adapter is present.  In either path every quote, enum, task ID,
    source reference and review status is validated before returning.
    """

    active_config = config or ExtractionConfig()
    _validate_context(context)
    _validate_config(active_config)
    _validate_source_text(source_text, active_config.max_source_chars)

    if active_config.provider_enabled:
        if active_config.provider is None:
            raise EvidenceExtractionError("provider_enabled requires an explicit provider adapter")
        provider_result = active_config.provider.extract(source_text, context)
        if not isinstance(provider_result, ExtractionResult):
            raise EvidenceExtractionError("provider must return ExtractionResult")
        trusted_result = _with_trusted_source(provider_result, context)
        return validate_extraction_result(
            trusted_result, source_text, context, max_observations=active_config.max_observations
        )

    observations = _extract_lexically(source_text, context, active_config.max_observations)
    result = ExtractionResult(
        observations=tuple(observations),
        no_signal=not observations,
        mode="lexical",
        limitations=(_DEFAULT_LIMITATION,),
    )
    return validate_extraction_result(
        result, source_text, context, max_observations=active_config.max_observations
    )


def validate_extraction_result(
    result: object,
    source_text: str,
    context: ExtractionContext,
    *,
    max_observations: int = 32,
) -> ExtractionResult:
    """Validate provider or lexical output without treating it as confirmed.

    Quote offsets are derived here when an adapter supplies only quote text.
    The trusted context replaces provider-controlled source metadata and is the
    authority for allowed task IDs.  This function does not validate semantic
    truth or apply planning changes.
    """

    _validate_context(context)
    _validate_source_text(source_text, max(len(source_text), 1_000_000))
    if not isinstance(result, ExtractionResult):
        raise EvidenceExtractionError("result must be ExtractionResult")
    if result.mode not in {"lexical", "provider"}:
        raise EvidenceExtractionError("mode must be 'lexical' or 'provider'")
    if max_observations <= 0 or max_observations > 10_000:
        raise EvidenceExtractionError("max_observations must be between 1 and 10000")
    if len(result.observations) > max_observations:
        raise EvidenceExtractionError("extraction exceeded the observation bound")
    if result.no_signal != (len(result.observations) == 0):
        raise EvidenceExtractionError("no_signal must match the observation list")

    normalized: list[Observation] = []
    for observation in result.observations:
        normalized.append(_validate_observation(observation, source_text, context))

    limitations = tuple(result.limitations)
    if result.mode == "provider" and _PROVIDER_LIMITATION not in limitations:
        limitations = limitations + (_PROVIDER_LIMITATION,)
    return replace(result, observations=tuple(normalized), limitations=limitations)


def apply_review_status(observation: Observation, status: ReviewStatus | str) -> Observation:
    """Return an observation with an explicit review status.

    Review is intentionally separate from extraction: confirming a report here
    records the manager's state, but it does not create or mutate planning
    inputs. A planning service must perform its own graph, scope and freshness
    checks before applying a confirmed change.
    """

    try:
        normalized_status = ReviewStatus(status)
    except ValueError as exc:
        raise EvidenceExtractionError(f"unsupported review status: {status!r}") from exc
    return replace(observation, review_status=normalized_status)


def _extract_lexically(
    source_text: str, context: ExtractionContext, max_observations: int
) -> list[Observation]:
    source = _source_reference(context)
    explicit_candidates = _explicit_task_candidates(source_text, context.canonical_task_ids)
    observations: list[Observation] = []

    for segment_start, segment_end, segment in _segments(source_text):
        kind = _classify(segment)
        if kind is None:
            continue
        if len(observations) >= max_observations:
            break

        assertion, reported_state = _assertion_and_state(segment)
        date_expression, proposed_date = _date_fields(segment)
        quantity = _reported_quantity(segment)
        ambiguity_reasons = _ambiguity_reasons(
            assertion,
            reported_state,
            context.task_id,
            explicit_candidates,
            quantity,
            date_expression,
        )
        quote = EvidenceQuote(text=segment, start=segment_start, end=segment_end)
        signal_id = _signal_id(context, kind, quote)
        observations.append(
            Observation(
                signal_id=signal_id,
                kind=kind,
                assertion=assertion,
                reported_state=reported_state,
                summary=f"Source reports {kind.value.replace('_', ' ')}.",
                evidence_quotes=(quote,),
                source=source,
                linked_task_id=context.task_id,
                related_task_candidate_ids=tuple(
                    candidate for candidate in explicit_candidates if candidate != context.task_id
                ),
                date_expression=date_expression,
                proposed_date=proposed_date,
                reported_quantity=quantity,
                ambiguity_reasons=tuple(ambiguity_reasons),
                review_status=ReviewStatus.PROPOSED,
            )
        )
    return observations


def _segments(source_text: str) -> list[tuple[int, int, str]]:
    segments: list[tuple[int, int, str]] = []
    for match in _SEGMENT_RE.finditer(source_text):
        raw = match.group(0)
        leading = len(raw) - len(raw.lstrip())
        trailing = len(raw.rstrip())
        start = match.start() + leading
        end = match.start() + trailing
        if start < end:
            segments.append((start, end, source_text[start:end]))
    return segments


def _classify(segment: str) -> SignalKind | None:
    has_negative_completion = bool(_NEGATIVE_COMPLETION_RE.search(segment))
    has_resolution = bool(_RESOLUTION_RE.search(segment)) and not has_negative_completion
    has_material = bool(_MATERIAL_RE.search(segment))
    has_issue_language = bool(
        _DEPENDENCY_RE.search(segment)
        or _SLOW_RE.search(segment)
        or _RESOURCE_RE.search(segment)
        or has_material
        or _WEATHER_RE.search(segment)
        or has_negative_completion
    )
    if has_negative_completion:
        return SignalKind.MATERIAL_WAIT if has_material else SignalKind.DEPENDENCY_BLOCKER
    if has_resolution and has_issue_language:
        return SignalKind.RESOLUTION
    if _WEATHER_RE.search(segment):
        return SignalKind.WEATHER_DISRUPTION
    if has_material and (
        re.search(
            r"\b(?:waiting|awaiting|missing|late|delayed|not\s+yet|won't\s+arrive)",
            segment,
            re.I,
        )
        or _DEPENDENCY_RE.search(segment)
    ):
        return SignalKind.MATERIAL_WAIT
    if _DEPENDENCY_RE.search(segment):
        return SignalKind.DEPENDENCY_BLOCKER
    if _RESOURCE_RE.search(segment):
        return SignalKind.RESOURCE_CONSTRAINT
    if _REMAINING_RE.search(segment):
        return SignalKind.REMAINING_WORK_REPORT
    if _SLOW_RE.search(segment):
        return SignalKind.REPORTED_SLOW_PROGRESS
    if has_resolution:
        return SignalKind.RESOLUTION
    return None


def _assertion_and_state(segment: str) -> tuple[Assertion, ReportedState]:
    hypothetical = bool(_HYPOTHETICAL_RE.search(segment))
    negative_completion = bool(_NEGATIVE_COMPLETION_RE.search(segment))
    resolved = (bool(_RESOLUTION_RE.search(segment)) and not negative_completion) or bool(
        _RESOLVED_NEGATION_RE.search(segment)
    )
    historical = bool(_HISTORICAL_RE.search(segment))
    negated = bool(_NEGATED_RE.search(segment))

    if hypothetical:
        return Assertion.HYPOTHETICAL, ReportedState.UNKNOWN
    if resolved:
        return Assertion.REPORTED, ReportedState.RESOLVED
    if negated:
        return Assertion.NEGATED, ReportedState.HISTORICAL if historical else ReportedState.UNKNOWN
    if historical:
        return Assertion.REPORTED, ReportedState.HISTORICAL
    return Assertion.REPORTED, ReportedState.ACTIVE


def _date_fields(segment: str) -> tuple[str | None, date | None]:
    iso_match = _ISO_DATE_RE.search(segment)
    if iso_match:
        value = iso_match.group(0)
        try:
            return value, date.fromisoformat(value)
        except ValueError:
            return value, None
    relative_match = _RELATIVE_DATE_RE.search(segment)
    if relative_match:
        return relative_match.group(0), None
    return None, None


def _reported_quantity(segment: str) -> ReportedQuantity | None:
    match = _QUANTITY_RE.search(segment)
    if match is None:
        return None
    unit = match.group("unit").casefold()
    if unit in {"hr", "hrs", "hour", "hours"}:
        normalized_unit = "hours"
    elif unit in {"day", "days"}:
        normalized_unit = "days"
    elif unit in {"worker", "workers", "people"}:
        normalized_unit = "workers"
    else:
        normalized_unit = "crew"
    crew_match = re.search(r"\b(?:for|with)\s+(\d+)\s+(?:workers?|people|crew)\b", segment, re.I)
    crew_scope = crew_match.group(0) if crew_match else None
    return ReportedQuantity(float(match.group("value")), normalized_unit, crew_scope)


def _ambiguity_reasons(
    assertion: Assertion,
    reported_state: ReportedState,
    linked_task_id: str | None,
    explicit_candidates: Sequence[str],
    quantity: ReportedQuantity | None,
    date_expression: str | None,
) -> list[str]:
    reasons: list[str] = []
    if assertion in {Assertion.HYPOTHETICAL, Assertion.NEGATED, Assertion.UNCLEAR}:
        reasons.append("Assertion requires review before it can affect planning.")
    if reported_state in {ReportedState.HISTORICAL, ReportedState.RESOLVED}:
        reasons.append("Reported state is not an active planning change by itself.")
    if linked_task_id is None and explicit_candidates:
        reasons.append("Explicit task candidates are not promoted without a trusted task link.")
    if quantity is not None and quantity.unit in {"hours", "days"}:
        reasons.append("Reported duration is not a confirmed person-hour estimate.")
    if date_expression is not None and not _ISO_DATE_RE.fullmatch(date_expression):
        reasons.append("Relative date was preserved and not resolved against today's date.")
    if linked_task_id is None and not explicit_candidates:
        reasons.append("No canonical task link was supplied; task linkage remains unresolved.")
    return reasons


def _explicit_task_candidates(
    source_text: str, canonical_task_ids: Sequence[str]
) -> tuple[str, ...]:
    candidates: list[str] = []
    for task_id in canonical_task_ids:
        pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(task_id)}(?![A-Za-z0-9])")
        if pattern.search(source_text):
            candidates.append(task_id)
    return tuple(candidates)


def _source_reference(context: ExtractionContext) -> SourceReference:
    return SourceReference(
        source_id=context.source_id,
        source_revision=context.source_revision,
        source_kind=context.source_kind,
        project_id=context.project_id,
        task_id=context.task_id,
        work_date=context.work_date,
        known_at=context.known_at,
        timezone=context.timezone,
    )


def _signal_id(context: ExtractionContext, kind: SignalKind, quote: EvidenceQuote) -> str:
    material = "|".join(
        (
            LEXICAL_EXTRACTOR_VERSION,
            context.source_id,
            context.source_revision,
            kind.value,
            str(quote.start),
            quote.text,
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _with_trusted_source(result: ExtractionResult, context: ExtractionContext) -> ExtractionResult:
    trusted_source = _source_reference(context)
    observations = tuple(
        replace(
            observation,
            source=trusted_source,
            linked_task_id=context.task_id
            if context.task_id is not None
            else observation.linked_task_id,
            review_status=ReviewStatus.PROPOSED,
        )
        for observation in result.observations
    )
    return replace(result, observations=observations, mode="provider")


def _validate_context(context: object) -> None:
    if not isinstance(context, ExtractionContext):
        raise EvidenceExtractionError("context must be ExtractionContext")
    for field_name, value in (
        ("source_id", context.source_id),
        ("source_revision", context.source_revision),
        ("source_kind", context.source_kind),
        ("timezone", context.timezone),
    ):
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise EvidenceExtractionError(f"invalid context field: {field_name}")
    ids = context.canonical_task_ids
    if len(ids) != len(set(ids)):
        raise EvidenceExtractionError("canonical_task_ids must be unique")
    for task_id in ids:
        if not isinstance(task_id, str) or not _IDENTIFIER_RE.fullmatch(task_id):
            raise EvidenceExtractionError("canonical task IDs must be non-empty identifiers")
    if context.task_id is not None and context.task_id not in ids:
        raise EvidenceExtractionError("task_id is not in canonical_task_ids")
    for field_name, value in (
        ("project_id", context.project_id),
        ("task_id", context.task_id),
    ):
        if value is not None and (
            not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value)
        ):
            raise EvidenceExtractionError(f"invalid context field: {field_name}")


def _validate_config(config: ExtractionConfig) -> None:
    if config.max_source_chars <= 0 or config.max_source_chars > 1_000_000:
        raise EvidenceExtractionError("max_source_chars must be between 1 and 1000000")
    if config.max_observations <= 0 or config.max_observations > 10_000:
        raise EvidenceExtractionError("max_observations must be between 1 and 10000")
    if config.provider_enabled and config.provider is None:
        raise EvidenceExtractionError("provider_enabled requires a provider")


def _validate_source_text(source_text: object, max_source_chars: int) -> None:
    if not isinstance(source_text, str):
        raise EvidenceExtractionError("source_text must be a string")
    if not source_text.strip():
        raise EvidenceExtractionError("source_text must not be empty")
    if len(source_text) > max_source_chars:
        raise EvidenceExtractionError("source_text exceeds the configured extraction bound")


def _validate_observation(
    observation: object, source_text: str, context: ExtractionContext
) -> Observation:
    if not isinstance(observation, Observation):
        raise EvidenceExtractionError("observation must be Observation")
    try:
        kind = SignalKind(observation.kind)
        assertion = Assertion(observation.assertion)
        reported_state = ReportedState(observation.reported_state)
        review_status = ReviewStatus(observation.review_status)
    except ValueError as exc:
        raise EvidenceExtractionError("observation contains an unsupported enum value") from exc

    if not observation.signal_id or len(observation.signal_id) > 200:
        raise EvidenceExtractionError("observation signal_id is required")
    if not observation.summary or len(observation.summary) > 500:
        raise EvidenceExtractionError("observation summary has invalid length")
    if observation.source != _source_reference(context):
        raise EvidenceExtractionError("observation source does not match trusted context")
    if (
        observation.linked_task_id is not None
        and observation.linked_task_id not in context.canonical_task_ids
    ):
        raise EvidenceExtractionError("linked_task_id is not an allowed canonical task")
    if len(set(observation.related_task_candidate_ids)) != len(
        observation.related_task_candidate_ids
    ):
        raise EvidenceExtractionError("related task candidates must be unique")
    if any(
        candidate not in context.canonical_task_ids
        for candidate in observation.related_task_candidate_ids
    ):
        raise EvidenceExtractionError("related task candidate is not allowed")
    if observation.specialty_candidate_id is not None:
        raise EvidenceExtractionError(
            "specialty candidates require a separately authorized specialty scope"
        )
    if len(observation.evidence_quotes) == 0:
        raise EvidenceExtractionError("every observation requires exact evidence")

    quotes: list[EvidenceQuote] = []
    for quote in observation.evidence_quotes:
        quotes.append(_validate_quote(quote, source_text))
    quantity = observation.reported_quantity
    if quantity is not None and (
        not math.isfinite(quantity.value) or quantity.value < 0 or len(quantity.unit) > 50
    ):
        raise EvidenceExtractionError("reported quantity is invalid")

    return replace(
        observation,
        kind=kind,
        assertion=assertion,
        reported_state=reported_state,
        review_status=review_status,
        evidence_quotes=tuple(quotes),
        related_task_candidate_ids=tuple(observation.related_task_candidate_ids),
        ambiguity_reasons=tuple(observation.ambiguity_reasons),
    )


def _validate_quote(quote: object, source_text: str) -> EvidenceQuote:
    if not isinstance(quote, EvidenceQuote) or not quote.text:
        raise EvidenceExtractionError("quote text is required")
    if len(quote.text) > 2_000:
        raise EvidenceExtractionError("quote exceeds the evidence bound")
    if quote.start >= 0 or quote.end >= 0:
        if (
            quote.start < 0
            or quote.end < quote.start
            or source_text[quote.start : quote.end] != quote.text
        ):
            raise EvidenceExtractionError("quote offsets do not match source text")
        return quote
    start = source_text.find(quote.text)
    if start < 0:
        raise EvidenceExtractionError("quote is not an exact substring of source text")
    return replace(quote, start=start, end=start + len(quote.text))
