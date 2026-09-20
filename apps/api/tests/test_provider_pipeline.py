"""Real-engine provider pipeline tests with mocked HTTP transports."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from src.app.integrations.context import (
    ContextCache,
    OpenRouteServiceMatrixAdapter,
    WeatherAPIAdapter,
    enrich_context,
)
from src.app.simulation.engine import evaluate_portfolio

NOW = datetime(2026, 9, 21, 6, 0, tzinfo=UTC)
AS_OF = "2026-09-21T08:00:00+02:00"
FIXTURE_PATH = Path(__file__).resolve().parents[3] / "fixtures" / "portfolio-demo.json"


def _settings(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "weatherapi_api_key": "weather-test-secret",
        "openrouteservice_api_key": "ors-test-secret",
        "weather_forecast_days": 1,
        "routing_profile": "driving-car",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _weather_response(
    *,
    wind_kph: float | None = 40.0,
    date: str = "2026-09-21",
) -> dict[str, object]:
    local_hour = datetime.fromisoformat(f"{date}T08:00:00+02:00")
    raw_hour: dict[str, object] = {
        "time_epoch": int(local_hour.timestamp()),
        "time": f"{date} 08:00",
        "temp_c": 20.0,
        "precip_mm": 0.0,
        "chance_of_rain": 0,
        "gust_kph": 45.0,
        "vis_km": 10.0,
        "condition": {"text": "Clear", "code": 1000},
    }
    if wind_kph is not None:
        raw_hour["wind_kph"] = wind_kph
    return {
        "location": {"tz_id": "Europe/Warsaw"},
        "forecast": {
            "forecastday": [{"date": date, "hour": [raw_hour]}],
        },
    }


def _adapters(
    settings: SimpleNamespace,
    cache: ContextCache,
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[WeatherAPIAdapter, OpenRouteServiceMatrixAdapter]:
    transport = httpx.MockTransport(handler)
    weather = WeatherAPIAdapter(
        settings,
        transport=transport,
        base_url="https://weather.test/v1",
        cache=cache,
        sleep=lambda _: None,
    )
    routing = OpenRouteServiceMatrixAdapter(
        settings,
        transport=transport,
        base_url="https://routing.test",
        cache=cache,
        sleep=lambda _: None,
    )
    return weather, routing


def _project(
    project_id: str,
    *,
    latitude: float,
    longitude: float,
    target_finish: str = "2026-09-21T16:00:00+02:00",
) -> dict[str, object]:
    return {
        "id": project_id,
        "name": project_id.title(),
        "targetFinishAt": target_finish,
        "priority": 1,
        "timezone": "Europe/Warsaw",
        "location": {"latitude": latitude, "longitude": longitude},
    }


def _task(
    task_id: str,
    project_id: str,
    specialty: str,
    *,
    weather_rules: dict[str, object] | None = None,
) -> dict[str, object]:
    task: dict[str, object] = {
        "id": task_id,
        "projectId": project_id,
        "status": "planned",
        "predecessorIds": [],
        "requiredSpecialtyId": specialty,
        "remainingPersonHours": {
            "optimistic": 8,
            "mostLikely": 8,
            "pessimistic": 8,
        },
        "minCrew": 1,
        "maxCrew": 1,
        "earliestStartAt": AS_OF,
    }
    if weather_rules is not None:
        task["weatherRules"] = weather_rules
    return task


def _weather_engine_payload() -> dict[str, object]:
    return {
        "asOf": AS_OF,
        "horizonDays": 1,
        "seed": 17,
        "samples": 1,
        "targetProjectId": "alpha",
        "projects": [
            _project("alpha", latitude=52.2, longitude=21.0),
            _project("beta", latitude=52.3, longitude=21.1),
        ],
        "tasks": [
            _task(
                "wind-task",
                "alpha",
                "electrical",
                weather_rules={
                    "status": "confirmed",
                    "mode": "outdoor",
                    "maxWindKph": 30,
                },
            ),
            _task("plain-task", "beta", "carpentry"),
        ],
        "workers": [
            {
                "id": "wa",
                "specialtyIds": ["electrical"],
                "homeProjectId": "alpha",
                "workingWeekdays": [0, 1, 2, 3, 4],
                "shiftStart": "08:00",
                "shiftEnd": "16:00",
                "canTransfer": False,
            },
            {
                "id": "wb",
                "specialtyIds": ["carpentry"],
                "homeProjectId": "beta",
                "workingWeekdays": [0, 1, 2, 3, 4],
                "shiftStart": "08:00",
                "shiftEnd": "16:00",
                "canTransfer": False,
            },
        ],
        "reservations": [],
        "transferAssumptions": [],
    }


def _fixture_with_locations() -> dict[str, object]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    payload["samples"] = 1
    locations = {
        "alpha": {"latitude": 52.2, "longitude": 21.0},
        "beta": {"latitude": 52.3, "longitude": 21.1},
    }
    for project in payload["projects"]:
        if project["id"] in locations:
            project["location"] = locations[project["id"]]
    return payload


def _transfer_candidate(result: dict[str, object]) -> dict[str, object]:
    candidates = result["candidateEvaluations"]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        actions = candidate.get("actions", [])
        if any(isinstance(action, dict) and action.get("type") == "transfer" for action in actions):
            return candidate
    raise AssertionError("real engine did not evaluate a transfer candidate")


def _transfer_day(candidate: dict[str, object]) -> dict[str, object]:
    outcomes = candidate["outcomesByProject"]
    alpha = outcomes["alpha"]
    for row in alpha["capacityByDay"]:
        if row["date"] == "2026-09-21":
            return row
    raise AssertionError("transfer candidate did not expose the first project day")


def test_confirmed_wind_reaches_real_engine_only_for_the_rule_task() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path == "/v1/forecast.json"
        return httpx.Response(200, json=_weather_response(wind_kph=40.0))

    settings = _settings(openrouteservice_api_key="")
    cache = ContextCache()
    weather, routing = _adapters(settings, cache, handler)
    enriched = enrich_context(
        _weather_engine_payload(),
        settings,
        weather_adapter=weather,
        routing_adapter=routing,
        cache=cache,
        now=NOW,
    )

    result = evaluate_portfolio(enriched["planning"])
    alpha = result["baselineByProject"]["alpha"]
    beta = result["baselineByProject"]["beta"]

    assert len(requests) == 1
    assert enriched["planning"]["tasks"][0]["workability"]["capacityBySlot"]
    assert enriched["planning"]["tasks"][1].get("workability") is None
    assert alpha["finishP50"] is None
    assert alpha["censored"] is True
    assert beta["finishP50"] == "2026-09-21T16:00:00+02:00"
    assert beta["censored"] is False
    assert any(
        item["code"] == "task_stop_work_gate" and item["taskId"] == "wind-task"
        for item in result["diagnostics"]
    )
    assert not any(item.get("taskId") == "plain-task" for item in result["diagnostics"])


@pytest.mark.parametrize(
    ("outbound_hours", "return_hours", "expected_productive", "expected_friction"),
    [(1.0, 0.5, 6.5, 1.5), (0.25, 0.25, 7.5, 0.5)],
)
def test_directed_route_friction_reaches_real_engine(
    outbound_hours: float,
    return_hours: float,
    expected_productive: float,
    expected_friction: float,
) -> None:
    payload = _fixture_with_locations()
    settings = _settings(weatherapi_api_key="")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/matrix/driving-car"
        return httpx.Response(
            200,
            json={
                # enrich_context orders these locations beta -> alpha for the
                # confirmed beta -> alpha transfer.
                "durations": [
                    [0, outbound_hours * 3_600],
                    [return_hours * 3_600, 0],
                ],
                "distances": [[0, 1_000], [900, 0]],
            },
        )

    cache = ContextCache()
    weather, routing = _adapters(settings, cache, handler)
    enriched = enrich_context(
        payload,
        settings,
        weather_adapter=weather,
        routing_adapter=routing,
        cache=cache,
        now=NOW,
    )
    result = evaluate_portfolio(enriched["planning"])
    candidate = _transfer_candidate(result)
    action = next(action for action in candidate["actions"] if action["type"] == "transfer")
    day = _transfer_day(candidate)

    transfer = enriched["planning"]["transferAssumptions"][0]
    assert transfer["outboundTravelHours"] == pytest.approx(outbound_hours)
    assert transfer["returnTravelHours"] == pytest.approx(return_hours)
    assert candidate["transferNonProductiveHours"] == pytest.approx(expected_friction)
    assert action["nonProductiveHours"] == pytest.approx(expected_friction)
    assert day["transferTravelHours"] == pytest.approx(expected_friction)
    assert day["transferProductiveHours"] == pytest.approx(expected_productive)


def test_missing_weather_field_stays_censored_in_real_engine() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/forecast.json"
        return httpx.Response(200, json=_weather_response(wind_kph=None))

    settings = _settings(openrouteservice_api_key="")
    cache = ContextCache()
    weather, routing = _adapters(settings, cache, handler)
    enriched = enrich_context(
        _weather_engine_payload(),
        settings,
        weather_adapter=weather,
        routing_adapter=routing,
        cache=cache,
        now=NOW,
    )
    result = evaluate_portfolio(enriched["planning"])

    task = enriched["planning"]["tasks"][0]
    alpha = result["baselineByProject"]["alpha"]
    coverage = enriched["status"]["weather"]["coverageByProject"]["alpha"]
    assert coverage["complete"] is False
    assert coverage["missingFields"] == ["windKph"]
    assert task["workability"]["capacityBySlot"] == {}
    assert alpha["finishP50"] is None
    assert alpha["censored"] is True
    assert any(
        "weather_fields_unavailable:wind-task" == warning for warning in enriched["warnings"]
    )
    assert any(
        item["code"] == "unfinished_at_horizon" and item["taskId"] == "wind-task"
        for item in result["diagnostics"]
    )


def test_context_revision_is_stable_across_provider_cache_hit_and_real_engine_run() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/forecast.json":
            return httpx.Response(200, json=_weather_response(wind_kph=10.0))
        assert request.url.path == "/v2/matrix/driving-car"
        return httpx.Response(
            200,
            json={
                "durations": [[0, 3_600], [1_800, 0]],
                "distances": [[0, 1_000], [900, 0]],
            },
        )

    payload = _fixture_with_locations()
    payload["tasks"][0]["weatherRules"] = {
        "status": "confirmed",
        "mode": "outdoor",
        "maxWindKph": 30,
    }
    settings = _settings()
    cache = ContextCache()
    weather, routing = _adapters(settings, cache, handler)

    first = enrich_context(
        payload,
        settings,
        weather_adapter=weather,
        routing_adapter=routing,
        cache=cache,
        now=NOW,
    )
    first_engine = evaluate_portfolio(first["planning"])
    second = enrich_context(
        payload,
        settings,
        weather_adapter=weather,
        routing_adapter=routing,
        cache=cache,
        now=NOW,
    )
    second_engine = evaluate_portfolio(second["planning"])

    assert len(requests) == 2
    assert first["revision"] == second["revision"]
    assert first["planning"]["contextRevision"] == second["planning"]["contextRevision"]
    assert second["status"]["weather"]["projects"]["alpha"]["cache"]["hit"] is True
    assert second["status"]["routing"]["cache"]["hit"] is True
    assert (
        first["status"]["weather"]["sourceHashes"]["alpha"]
        == second["status"]["weather"]["sourceHashes"]["alpha"]
    )
    assert first["status"]["routing"]["sourceHash"] == second["status"]["routing"]["sourceHash"]
    assert first_engine == second_engine
