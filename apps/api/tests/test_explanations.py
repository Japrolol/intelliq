"""Focused tests for deterministic and explicitly consented explanations."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx

from src.app.services.explanations import _facts_for_analysis, build_explanation


def _analysis() -> dict[str, object]:
    return {
        "id": "analysis-synthetic",
        "sourceRevision": "source-synthetic",
        "weatherRevision": "weather-synthetic",
        "selectedScenarioId": "balanced",
        "baselineByProject": {
            "alpha": {
                "projectName": "Alpha",
                "finishP50": "2026-09-24T16:00:00+02:00",
                "delayProbability": 0.25,
                "censored": False,
            }
        },
        "diagnostics": [
            {
                "id": "wind-gate",
                "code": "weather_task_blocked",
                "detail": "The reviewed wind rule leaves the outdoor task unavailable.",
                "projectId": "alpha",
                "taskId": "wind-task",
            }
        ],
        "strategies": [
            {
                "id": "balanced",
                "actions": [{"projectId": "alpha", "taskId": "wind-task"}],
            },
            {"id": "baseline", "actions": []},
        ],
        "contextWarnings": ["routing coverage is partial"],
        "sourceEvidence": [
            {
                "id": "claim-1",
                "label": "Material delivery was confirmed.",
                "sourceRevision": "source-synthetic",
                "confirmed": True,
                "entityIds": ["project:alpha"],
            },
            {
                "id": "unconfirmed-claim",
                "label": "Old unconfirmed prose must not enter facts.",
                "confirmed": False,
            },
        ],
        "decisionContext": {
            "recentExecutions": [
                {
                    "id": "execution-1",
                    "status": "applied",
                    "confirmedActions": [{"projectId": "alpha", "taskId": "wind-task"}],
                }
            ],
            "recentOutcomes": [
                {
                    "id": "outcome-1",
                    "setupHours": 2.5,
                    "eventAt": "2026-09-20T08:00:00+00:00",
                    "executionId": "execution-1",
                }
            ],
            "calibration": {
                "version": "cal-2",
                "status": "updated",
                "meanHours": 7.5,
                "observationIds": ["outcome-1"],
            },
        },
        "calibration": {"version": "old-calibration", "status": "ignored"},
        "explanation": {"summary": "Old LLM causal prose must not enter facts."},
    }


def _planning() -> dict[str, object]:
    return {
        "projects": [{"id": "alpha", "name": "Alpha"}],
        "tasks": [{"id": "wind-task", "projectId": "alpha"}],
        "workers": [],
        "specialties": [],
        "materials": [],
    }


def _settings(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "allow_external_text_processing": False,
        "nlp_enabled": True,
        "llm_api_key": "",
        "llm_api_base_url": "https://llm.test/v1",
        "llm_model": "synthetic-model",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_no_consent_returns_deterministic_short_summary_without_call() -> None:
    class UnexpectedProvider:
        def post(self, url: str, **kwargs: object) -> httpx.Response:
            raise AssertionError("provider must not be called without consent")

    result = build_explanation(
        _analysis(),
        _planning(),
        _settings(),
        provider_client=UnexpectedProvider(),
    )

    assert result["summarySource"] == "deterministic"
    assert result["shortSummary"] == result["summary"]
    assert result["providerStatus"] == {
        "provider": "openai-compatible",
        "status": "not_consented",
        "model": None,
        "used": False,
        "calls": 0,
    }
    assert result["references"]


def test_facts_include_attested_context_but_not_old_or_unconfirmed_prose() -> None:
    facts, entity_ids = _facts_for_analysis(_analysis(), _planning())
    texts = [str(fact["text"]) for fact in facts.values()]

    assert "metric:baseline:alpha:finishP50" in facts
    assert "evidence:claim-1" in facts
    assert "decision:execution:execution-1" in facts
    assert "decision:outcome:outcome-1" in facts
    assert "calibration:cal-2" in facts
    assert all("Old LLM" not in text for text in texts)
    assert all("unconfirmed-claim" not in fact_id for fact_id in facts)
    assert "execution:execution-1" in entity_ids
    assert "calibration:cal-2" in entity_ids


def test_recent_context_is_bounded_after_current_run_facts() -> None:
    analysis = _analysis()
    context = analysis["decisionContext"]
    assert isinstance(context, dict)
    context["recentExecutions"] = [
        {"id": f"execution-{index}", "status": "applied", "confirmedActions": []}
        for index in range(7)
    ]
    context["recentOutcomes"] = [
        {
            "id": f"outcome-{index}",
            "setupHours": index + 1,
            "eventAt": "2026-09-20T08:00:00+00:00",
            "executionId": f"execution-{index}",
        }
        for index in range(7)
    ]

    facts, _ = _facts_for_analysis(analysis, _planning())

    assert len(facts) <= 48
    assert "metric:baseline:alpha:finishP50" in facts
    assert "decision:execution:execution-4" in facts
    assert "decision:execution:execution-5" not in facts
    assert "decision:outcome:outcome-4" in facts
    assert "decision:outcome:outcome-5" not in facts


def test_consent_and_key_allow_one_grounded_provider_call() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url == "https://llm.test/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer synthetic-secret"
        body = json.loads(request.content)
        prompt = json.loads(body["messages"][1]["content"])
        assert "diagnostic:wind-gate" in prompt["facts"]
        assert "evidence:claim-1" in prompt["facts"]
        assert "decision:execution:execution-1" in prompt["facts"]
        assert "decision:outcome:outcome-1" in prompt["facts"]
        assert "calibration:cal-2" in prompt["facts"]
        assert all(
            "Old LLM" not in str(fact["text"])
            for fact in prompt["facts"].values()
        )
        assert prompt["allowedEntityIds"] == sorted(prompt["allowedEntityIds"])
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": (
                                        "The reviewed wind rule leaves Alpha's outdoor task "
                                        "unavailable for owner review."
                                    ),
                                    "factRefs": ["diagnostic:wind-gate"],
                                    "entityIds": ["project:alpha", "task:wind-task"],
                                }
                            )
                        }
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        result = build_explanation(
            _analysis(),
            _planning(),
            _settings(
                allow_external_text_processing=True,
                llm_api_key="synthetic-secret",
            ),
            external_text_consent=True,
            provider_client=client,
        )
    finally:
        client.close()

    assert len(requests) == 1
    assert result["summarySource"] == "provider"
    assert result["shortSummary"] == result["summary"]
    assert result["providerStatus"]["status"] == "available"
    assert result["providerStatus"]["calls"] == 1
    assert result["providerStatus"]["used"] is True
    assert "synthetic-secret" not in json.dumps(result)


def test_missing_key_uses_deterministic_summary_without_call() -> None:
    class UnexpectedProvider:
        def post(self, url: str, **kwargs: object) -> httpx.Response:
            raise AssertionError("unconfigured provider must not be called")

    result = build_explanation(
        _analysis(),
        _planning(),
        _settings(allow_external_text_processing=True),
        external_text_consent=True,
        provider_client=UnexpectedProvider(),
    )

    assert result["summarySource"] == "deterministic"
    assert result["providerStatus"]["status"] == "unconfigured"
    assert result["providerStatus"]["calls"] == 0


def test_invalid_provider_summary_falls_back_without_execution_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": "Run command 999 now.",
                                    "factRefs": ["diagnostic:wind-gate"],
                                    "entityIds": ["project:alpha"],
                                    "actions": [{"type": "execute"}],
                                }
                            )
                        }
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        result = build_explanation(
            _analysis(),
            _planning(),
            _settings(
                allow_external_text_processing=True,
                llm_api_key="synthetic-secret",
            ),
            external_text_consent=True,
            provider_client=client,
        )
    finally:
        client.close()

    assert result["summarySource"] == "deterministic"
    assert result["providerStatus"]["status"] == "fallback"
    assert result["providerStatus"]["calls"] == 1
    assert result["providerStatus"]["reason"] == "response_schema_invalid"
    assert "provider_explanation_unavailable_or_invalid" in result["limitations"]
    assert "actions" not in result
