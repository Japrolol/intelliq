"""Approval context freshness tests using mocked provider transports."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from src.app.integrations.context import (
    ContextCache,
    OpenRouteServiceMatrixAdapter,
    WeatherAPIAdapter,
    enrich_context,
)
from src.app.services.context_freshness import (
    ContextFreshnessError,
    enrich_frozen_context_for_approval,
    require_fresh_context_for_approval,
)

NOW = datetime(2026, 9, 21, 6, 0, tzinfo=UTC)
AS_OF = "2026-09-21T08:00:00+02:00"


def _settings(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "weatherapi_api_key": "weather-test-secret",
        "openrouteservice_api_key": "ors-test-secret",
        "weather_forecast_days": 1,
        "routing_profile": "driving-car",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _planning() -> dict[str, object]:
    return {
        "asOf": AS_OF,
        "horizonDays": 1,
        "projects": [
            {
                "id": "alpha",
                "name": "Alpha",
                "timezone": "Europe/Warsaw",
                "location": {"latitude": 52.2, "longitude": 21.0},
            },
        ],
        "tasks": [
            {
                "id": "weather-task",
                "projectId": "alpha",
                "weatherRules": {
                    "status": "confirmed",
                    "mode": "outdoor",
                    "maxWindKph": 30,
                },
            },
        ],
        "workers": [],
        "reservations": [],
        "transferAssumptions": [],
    }


def _weather_payload(wind_kph: float | None = 10.0) -> dict[str, object]:
    local_hour = datetime.fromisoformat("2026-09-21T08:00:00+02:00")
    hour: dict[str, object] = {
        "time_epoch": int(local_hour.timestamp()),
        "time": "2026-09-21 08:00",
        "temp_c": 20.0,
        "precip_mm": 0.0,
        "chance_of_rain": 0,
        "gust_kph": 20.0,
        "vis_km": 10.0,
    }
    if wind_kph is not None:
        hour["wind_kph"] = wind_kph
    return {
        "location": {"tz_id": "Europe/Warsaw"},
        "forecast": {
            "forecastday": [{"date": "2026-09-21", "hour": [hour]}],
        },
    }


def _adapters(
    settings: SimpleNamespace,
    cache: ContextCache,
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[WeatherAPIAdapter, OpenRouteServiceMatrixAdapter]:
    transport = httpx.MockTransport(handler)
    return (
        WeatherAPIAdapter(
            settings,
            transport=transport,
            base_url="https://weather.test/v1",
            cache=cache,
            sleep=lambda _: None,
        ),
        OpenRouteServiceMatrixAdapter(
            settings,
            transport=transport,
            base_url="https://routing.test",
            cache=cache,
            sleep=lambda _: None,
        ),
    )


def test_approval_prefers_saved_context_input_and_ignores_graph() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path == "/v1/forecast.json"
        return httpx.Response(200, json=_weather_payload())

    settings = _settings(openrouteservice_api_key="")
    cache = ContextCache()
    weather, routing = _adapters(settings, cache, handler)
    original = _planning()
    first = enrich_context(
        original,
        settings,
        weather_adapter=weather,
        routing_adapter=routing,
        cache=cache,
        now=NOW,
    )
    saved_input = {
        "contextInput": original,
        "planning": first["planning"],
        "graph": {"nodes": [{"id": "graph-only"}], "edges": []},
    }
    saved_input["planning"]["tasks"][0]["weatherRules"] = {
        "status": "proposed",
        "maxWindKph": 1,
    }

    result = enrich_frozen_context_for_approval(
        {"weatherRevision": first["revision"]},
        saved_input,
        settings,
        weather_adapter=weather,
        routing_adapter=routing,
        cache=cache,
        now=NOW,
    )

    assert len(requests) == 1
    assert result["inputKey"] == "contextInput"
    assert result["fresh"] is True
    assert result["mismatch"] is False
    assert result["degraded"] is False
    assert result["conflict"] is None
    assert result["planning"]["tasks"][0]["workability"]["capacityBySlot"]


def test_approval_falls_back_to_current_post_enrichment_planning() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/forecast.json"
        return httpx.Response(200, json=_weather_payload())

    settings = _settings(openrouteservice_api_key="")
    cache = ContextCache()
    weather, routing = _adapters(settings, cache, handler)
    first = enrich_context(
        _planning(),
        settings,
        weather_adapter=weather,
        routing_adapter=routing,
        cache=cache,
        now=NOW,
    )

    result = enrich_frozen_context_for_approval(
        {"weatherRevision": first["revision"]},
        {"planning": first["planning"], "graph": {"nodes": [], "edges": []}},
        settings,
        weather_adapter=weather,
        routing_adapter=routing,
        cache=cache,
        now=NOW,
    )

    assert result["inputKey"] == "planning"
    assert result["fresh"] is True
    assert result["revision"] == first["revision"]


def test_revision_change_returns_explicit_409_conflict() -> None:
    def old_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_weather_payload(wind_kph=10.0))

    settings = _settings(openrouteservice_api_key="")
    first_cache = ContextCache()
    first_weather, first_routing = _adapters(settings, first_cache, old_handler)
    first = enrich_context(
        _planning(),
        settings,
        weather_adapter=first_weather,
        routing_adapter=first_routing,
        cache=first_cache,
        now=NOW,
    )

    def changed_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_weather_payload(wind_kph=25.0))

    changed_cache = ContextCache()
    changed_weather, changed_routing = _adapters(settings, changed_cache, changed_handler)
    result = enrich_frozen_context_for_approval(
        {"weatherRevision": first["revision"]},
        {"contextInput": _planning(), "graph": {}},
        settings,
        weather_adapter=changed_weather,
        routing_adapter=changed_routing,
        cache=changed_cache,
        now=NOW,
    )

    assert result["fresh"] is False
    assert result["mismatch"] is True
    assert result["degraded"] is False
    assert result["conflict"]["statusCode"] == 409
    assert result["conflict"]["code"] == "analysis_context_revision_mismatch"
    assert result["conflict"]["expectedRevision"] == first["revision"]
    assert result["conflict"]["actualRevision"] != first["revision"]


def test_unchanged_declared_degradation_remains_approvable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_weather_payload(wind_kph=None))

    settings = _settings(openrouteservice_api_key="")
    cache = ContextCache()
    weather, routing = _adapters(settings, cache, handler)
    degraded = enrich_context(
        _planning(),
        settings,
        weather_adapter=weather,
        routing_adapter=routing,
        cache=cache,
        now=NOW,
    )
    result = enrich_frozen_context_for_approval(
        {
            "weatherRevision": degraded["revision"],
            "providerStatus": degraded["status"],
        },
        {"contextInput": _planning()},
        settings,
        weather_adapter=weather,
        routing_adapter=routing,
        cache=cache,
        now=NOW,
    )

    assert result["matched"] is True
    assert result["mismatch"] is False
    assert result["degraded"] is True
    assert result["newDegradation"] is False
    assert result["fresh"] is True
    assert result["conflict"] is None
    assert "weather" in result["degradedProviders"]


def test_unchanged_missing_ors_coverage_with_manual_estimate_is_approvable() -> None:
    planning = _planning()
    planning["projects"] = [
        planning["projects"][0],
        {
            "id": "beta",
            "name": "Beta",
            "timezone": "Europe/Warsaw",
            "location": None,
        },
    ]
    planning["transferAssumptions"] = [
        {
            "id": "transfer-1",
            "status": "confirmed",
            "fromProjectId": "alpha",
            "toProjectId": "beta",
            "manualTravelMinutes": 30,
        }
    ]

    settings = _settings(openrouteservice_api_key="")
    first = enrich_context(planning, settings, now=NOW)
    result = enrich_frozen_context_for_approval(
        {
            "weatherRevision": first["revision"],
            "providerStatus": first["status"],
        },
        {"contextInput": planning},
        settings,
        now=NOW,
    )

    assert result["revision"] == first["revision"]
    assert result["status"] == first["status"]
    assert result["degraded"] is True
    assert result["newDegradation"] is False
    assert result["fresh"] is True
    assert result["conflict"] is None
    assert result["status"]["routing"]["coverage"] == {
        "pairsRequested": 2,
        "pairsAvailable": 0,
        "complete": False,
    }


def test_require_helper_raises_sanitized_409_error() -> None:
    result = {
        "weatherRevision": "not-the-current-revision",
    }
    with pytest.raises(ContextFreshnessError) as caught:
        require_fresh_context_for_approval(
            result,
            {"planning": _planning()},
            _settings(weatherapi_api_key="", openrouteservice_api_key=""),
            now=NOW,
        )

    assert caught.value.status_code == 409
    assert caught.value.code == "analysis_context_stale_and_degraded"
    assert "weather-test-secret" not in str(caught.value)
