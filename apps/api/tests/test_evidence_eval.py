"""Corpus-only evaluation of the current local evidence extraction seam."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import TypedDict, cast

from src.app.integrations.evidence_bridge import extract_evidence
from src.app.intelligence.evidence import (
    Assertion,
    ExtractionContext,
    ReportedState,
    ReviewStatus,
    SignalKind,
    extract_signals,
)


class ExpectedObservation(TypedDict):
    observationCount: int
    kind: str | None
    assertion: str | None
    reportedState: str | None
    linkedTaskId: str | None
    relatedTaskCandidateIds: list[str]


class EvaluationNote(TypedDict):
    id: str
    label: str
    text: str
    sourceId: str
    sourceRevision: str
    sourceKind: str
    projectId: str
    taskId: str | None
    canonicalTaskIds: list[str]
    expected: ExpectedObservation


class EvaluationFixture(TypedDict):
    description: str
    notes: list[EvaluationNote]


EXPECTED_LABEL_COUNTS = {
    "ambiguous_unlinked": 4,
    "asserted_blocker": 5,
    "historical": 3,
    "hypothetical": 3,
    "negated": 3,
    "no_signal": 2,
    "resolved": 4,
}


def _load_fixture() -> EvaluationFixture:
    fixture_path = Path(__file__).parent / "fixtures" / "evidence-eval.json"
    with fixture_path.open(encoding="utf-8") as fixture_file:
        return cast(EvaluationFixture, json.load(fixture_file))


def _context(note: EvaluationNote) -> ExtractionContext:
    return ExtractionContext(
        source_id=note["sourceId"],
        source_revision=note["sourceRevision"],
        source_kind=note["sourceKind"],
        canonical_task_ids=tuple(note["canonicalTaskIds"]),
        project_id=note["projectId"],
        task_id=note["taskId"],
    )


def test_fixture_is_small_and_hand_label_distribution_is_explicit() -> None:
    fixture = _load_fixture()
    notes = fixture["notes"]

    assert 20 <= len(notes) <= 30
    assert Counter(note["label"] for note in notes) == EXPECTED_LABEL_COUNTS
    assert len({note["sourceRevision"] for note in notes}) == len(notes)
    assert all(note["expected"]["observationCount"] in {0, 1} for note in notes)


def test_local_extractor_matches_hand_labels_and_preserves_evidence() -> None:
    fixture = _load_fixture()
    notes = fixture["notes"]
    observed_labels: Counter[str] = Counter()
    exact_field_matches = 0
    mismatches: list[str] = []

    for note in notes:
        result = extract_signals(note["text"], _context(note))
        expected = note["expected"]
        observations = result.observations
        observed_labels[note["label"]] += 1

        if len(observations) != expected["observationCount"]:
            mismatches.append(
                f"{note['id']}: expected {expected['observationCount']} observations, "
                f"got {len(observations)}"
            )
            continue

        if not observations:
            if not result.no_signal:
                mismatches.append(f"{note['id']}: expected no_signal=True")
            else:
                exact_field_matches += 1
            continue

        observation = observations[0]
        checks = {
            "kind": observation.kind == SignalKind(expected["kind"]),
            "assertion": observation.assertion == Assertion(expected["assertion"]),
            "reportedState": observation.reported_state == ReportedState(expected["reportedState"]),
            "linkedTaskId": observation.linked_task_id == expected["linkedTaskId"],
            "relatedTaskCandidateIds": list(observation.related_task_candidate_ids)
            == expected["relatedTaskCandidateIds"],
            "sourceId": observation.source.source_id == note["sourceId"],
            "sourceRevision": observation.source.source_revision == note["sourceRevision"],
            "reviewStatus": observation.review_status == ReviewStatus.PROPOSED,
        }
        quote = observation.evidence_quotes[0]
        checks["exactQuote"] = note["text"][quote.start : quote.end] == quote.text == note["text"]
        if note["label"] == "ambiguous_unlinked":
            checks["ambiguity"] = bool(observation.ambiguity_reasons)

        failed_checks = [name for name, passed in checks.items() if not passed]
        if failed_checks:
            mismatches.append(f"{note['id']}: failed {', '.join(failed_checks)}")
        else:
            exact_field_matches += 1

    print(
        "corpus-only evidence eval: "
        f"notes={len(notes)}, labels={dict(sorted(observed_labels.items()))}, "
        f"exact_note_matches={exact_field_matches}/{len(notes)}; "
        "not a production accuracy claim"
    )
    assert not mismatches, "\n".join(mismatches)


def test_bridge_uses_local_fallback_when_external_provider_is_disabled(monkeypatch) -> None:
    note = _load_fixture()["notes"][0]
    monkeypatch.setenv("ALLOW_EXTERNAL_TEXT_PROCESSING", "false")
    monkeypatch.setenv("NLP_ENABLED", "true")

    result = extract_evidence(
        note["text"],
        set(note["canonicalTaskIds"]),
        {
            "sourceId": note["sourceId"],
            "sourceRevision": note["sourceRevision"],
            "sourceType": note["sourceKind"],
            "projectId": note["projectId"],
            "taskId": note["taskId"],
        },
    )

    assert len(result) == 1
    observation = result[0]
    assert observation["kind"] == note["expected"]["kind"]
    assert observation["assertion"] == note["expected"]["assertion"]
    assert observation["reportedState"] == note["expected"]["reportedState"]
    assert observation["source"]["sourceRevision"] == note["sourceRevision"]
    quote = observation["evidenceQuotes"][0]
    assert note["text"][quote["start"] : quote["end"]] == quote["text"] == note["text"]
