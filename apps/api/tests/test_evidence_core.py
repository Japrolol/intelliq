from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TypedDict, cast

import pytest

from src.app.intelligence.evidence import (
    LEXICAL_EXTRACTOR_VERSION,
    Assertion,
    EvidenceExtractionError,
    EvidenceQuote,
    ExtractionConfig,
    ExtractionContext,
    ExtractionResult,
    Observation,
    ReportedState,
    ReviewStatus,
    SignalKind,
    SourceReference,
    apply_review_status,
    extract_signals,
)


def context(*, task_id: str | None = None) -> ExtractionContext:
    return ExtractionContext(
        source_id="worklog-1",
        source_revision="rev-3",
        source_kind="worklog_description",
        canonical_task_ids=("A1", "A2"),
        project_id="alpha",
        task_id=task_id,
    )


class DemoTask(TypedDict):
    id: str


class DemoWorklog(TypedDict):
    id: str
    projectId: str
    taskId: str
    knownAt: str
    description: str


class DemoFixture(TypedDict):
    tasks: list[DemoTask]
    worklogs: list[DemoWorklog]


def load_demo_fixture() -> DemoFixture:
    fixture_path = Path(__file__).parents[3] / "fixtures" / "portfolio-demo.json"
    with fixture_path.open(encoding="utf-8") as fixture_file:
        return cast(DemoFixture, json.load(fixture_file))


def test_lexical_extraction_preserves_exact_quote_and_canonical_link() -> None:
    source = "We cannot close the walls because the electrical installation is unfinished."

    result = extract_signals(source, context(task_id="A2"))

    assert result.mode == "lexical"
    assert result.no_signal is False
    observation = result.observations[0]
    assert observation.kind == SignalKind.DEPENDENCY_BLOCKER
    assert observation.linked_task_id == "A2"
    quote = observation.evidence_quotes[0]
    assert source[quote.start : quote.end] == quote.text == source
    assert observation.review_status == ReviewStatus.PROPOSED


def test_negative_completion_precedes_resolution_and_preserves_delivery_resolution() -> None:
    source = (
        "18 cable trays delivered. "
        "The electrical installation is not finished, so the walls cannot be closed."
    )

    result = extract_signals(source, context(task_id="A2"))

    assert len(result.observations) == 2
    assert result.observations[0].kind == SignalKind.RESOLUTION
    assert result.observations[0].reported_state == ReportedState.RESOLVED
    assert result.observations[1].kind == SignalKind.DEPENDENCY_BLOCKER
    assert result.observations[1].reported_state == ReportedState.ACTIVE
    assert result.observations[1].summary == "Source reports dependency blocker."


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("The installation is not finished.", SignalKind.DEPENDENCY_BLOCKER),
        ("The installation is not completed.", SignalKind.DEPENDENCY_BLOCKER),
        ("The cable delivery has not arrived.", SignalKind.MATERIAL_WAIT),
    ],
)
def test_negative_completion_is_not_resolved(text: str, kind: SignalKind) -> None:
    result = extract_signals(text, context(task_id="A2"))

    assert len(result.observations) == 1
    assert result.observations[0].kind == kind
    assert result.observations[0].reported_state == ReportedState.ACTIVE


def test_explicit_resolved_negation_and_lexical_cache_version_remain_visible() -> None:
    resolved = extract_signals("The crew is no longer blocked.", context(task_id="A2"))

    assert resolved.observations[0].kind == SignalKind.RESOLUTION
    assert resolved.observations[0].reported_state == ReportedState.RESOLVED
    assert LEXICAL_EXTRACTOR_VERSION == "evidence-v2"


def test_section_16_fixture_worklog_uses_trusted_task_link() -> None:
    fixture = load_demo_fixture()
    worklogs = fixture["worklogs"]
    tasks = fixture["tasks"]
    worklog = worklogs[0]
    task_ids = tuple(task["id"] for task in tasks)
    extraction_context = ExtractionContext(
        source_id=worklog["id"],
        source_revision=worklog["knownAt"],
        source_kind="worklog_description",
        canonical_task_ids=task_ids,
        project_id=worklog["projectId"],
        task_id=worklog["taskId"],
        known_at=datetime.fromisoformat(worklog["knownAt"]),
    )

    result = extract_signals(worklog["description"], extraction_context)

    assert [item.kind for item in result.observations] == [
        SignalKind.DEPENDENCY_BLOCKER,
        SignalKind.RESOLUTION,
    ]
    assert all(item.linked_task_id == "a2" for item in result.observations)
    assert result.observations[1].reported_state == ReportedState.RESOLVED
    for item in result.observations:
        quote = item.evidence_quotes[0]
        assert worklog["description"][quote.start : quote.end] == quote.text


def test_negated_hypothetical_resolved_and_historical_states_are_distinct() -> None:
    source = (
        "There is no delay on A1. "
        "If rain continues, the crew could be delayed. "
        "The delivery was resolved yesterday. "
        "The team was waiting for steel last week."
    )

    result = extract_signals(source, context())

    assert [item.assertion for item in result.observations] == [
        Assertion.NEGATED,
        Assertion.HYPOTHETICAL,
        Assertion.REPORTED,
        Assertion.REPORTED,
    ]
    assert [item.reported_state for item in result.observations] == [
        ReportedState.UNKNOWN,
        ReportedState.UNKNOWN,
        ReportedState.RESOLVED,
        ReportedState.HISTORICAL,
    ]
    assert result.observations[1].kind == SignalKind.WEATHER_DISRUPTION
    assert result.observations[2].kind == SignalKind.RESOLUTION
    assert result.observations[3].kind == SignalKind.MATERIAL_WAIT


def test_arrived_material_does_not_create_active_material_wait() -> None:
    result = extract_signals(
        "The cables have already arrived, so the material wait is resolved.", context()
    )

    assert len(result.observations) == 1
    assert result.observations[0].kind == SignalKind.RESOLUTION
    assert result.observations[0].reported_state == ReportedState.RESOLVED


def test_plain_language_never_guesses_a_task_link() -> None:
    result = extract_signals("Waiting for the electrician before wall closure.", context())

    observation = result.observations[0]
    assert observation.linked_task_id is None
    assert observation.related_task_candidate_ids == ()
    assert any("task link" in reason for reason in observation.ambiguity_reasons)


def test_explicit_unknown_task_id_is_not_accepted_by_context() -> None:
    with pytest.raises(EvidenceExtractionError, match="not in canonical_task_ids"):
        extract_signals("A9 is blocked.", context(task_id="A9"))


def test_provider_is_not_called_without_explicit_opt_in() -> None:
    class Provider:
        called = False

        def extract(self, source_text: str, context: ExtractionContext) -> ExtractionResult:
            self.called = True
            raise AssertionError("provider should not be called")

    provider = Provider()
    result = extract_signals(
        "The crew is behind.",
        context(),
        config=ExtractionConfig(provider=provider),
    )

    assert provider.called is False
    assert result.mode == "lexical"


def test_provider_requires_explicit_opt_in_and_quotes_are_revalidated() -> None:
    class Provider:
        def extract(self, source_text: str, context: ExtractionContext) -> ExtractionResult:
            quote = EvidenceQuote("The crew is behind.")
            observation = Observation(
                signal_id="provider-1",
                kind=SignalKind.REPORTED_SLOW_PROGRESS,
                assertion=Assertion.REPORTED,
                reported_state=ReportedState.ACTIVE,
                summary="Provider report",
                evidence_quotes=(quote,),
                source=SourceReference(
                    source_id="provider-controlled",
                    source_revision="provider-controlled",
                    source_kind="provider",
                    project_id=None,
                    task_id=None,
                    work_date=None,
                    known_at=None,
                    timezone="UTC",
                ),
            )
            return ExtractionResult((observation,), False, "provider")

    disabled_result = extract_signals(
        "The crew is behind.", context(), config=ExtractionConfig(provider=Provider())
    )
    assert disabled_result.mode == "lexical"

    result = extract_signals(
        "The crew is behind.",
        context(),
        config=ExtractionConfig(provider_enabled=True, provider=Provider()),
    )
    assert result.mode == "provider"
    assert result.observations[0].evidence_quotes[0].start == 0


def test_review_status_is_separate_from_extraction() -> None:
    result = extract_signals("The crew is behind.", context())
    confirmed = apply_review_status(result.observations[0], "confirmed")

    assert result.observations[0].review_status == ReviewStatus.PROPOSED
    assert confirmed.review_status == ReviewStatus.CONFIRMED
