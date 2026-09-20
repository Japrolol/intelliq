"""Focused mocked-provider tests for the IntelliQ context seam."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest

from src.app.integrations.context import (
    ContextCache,
    ContextProviderError,
    OpenRouteServiceMatrixAdapter,
    ORSMatrixAdapter,
    ProviderStatus,
    WeatherAPIAdapter,
    enrich_context,
)

NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
WEATHER_KEY = "weather-test-secret"
ROUTING_KEY = "ors-test-secret"


def _settings(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "weatherapi_api_key": WEATHER_KEY,
        "openrouteservice_api_key": ROUTING_KEY,
        "weather_forecast_days": 1,
        "routing_profile": "driving-car",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _weather_payload(*, wind_kph: float = 40.0, timezone: str = "Europe/Warsaw") -> dict:
    local_hour = datetime(2026, 9, 20, 8, 0, tzinfo=ZoneInfo(timezone))
    return {
        "location": {"tz_id": timezone},
        "forecast": {
            "forecastday": [
                {
                    "date": "2026-09-20",
                    "hour": [
                        {
                            "time_epoch": int(local_hour.timestamp()),
                            "time": "2026-09-20 08:00",
                            "temp_f": 68.0,
                            "precip_in": 0.1,
                            "chance_of_rain": 50,
                            "wind_mph": wind_kph / 1.609344,
                            "gust_mph": 31.068559 / 1.609344,
                            "vis_miles": 6.2137119,
                            "condition": {"text": "Rain", "code": 1183},
                        }
                    ],
                }
            ]
        },
    }


def test_weather_adapter_uses_https_query_key_and_normalizes_units() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.scheme == "https"
        assert request.url.path == "/v1/forecast.json"
        assert request.url.params["key"] == WEATHER_KEY
        assert request.url.params["q"] == "52.229700,21.012200"
        assert request.url.params["days"] == "1"
        return httpx.Response(200, json=_weather_payload())

    adapter = WeatherAPIAdapter(
        _settings(),
        transport=httpx.MockTransport(handler),
        base_url="https://weather.test/v1",
        cache=ContextCache(),
        sleep=lambda _: None,
    )

    result = adapter.fetch_hourly(52.2297, 21.0122, now=NOW)
    hour = result["hours"][0]

    assert len(requests) == 1
    assert hour["temperatureC"] == pytest.approx(20.0)
    assert hour["precipitationMm"] == pytest.approx(2.54)
    assert hour["rainProbability"] == pytest.approx(0.5)
    assert hour["rainProbabilityPercent"] == pytest.approx(50.0)
    assert hour["windKph"] == pytest.approx(40.0)
    assert hour["gustKph"] == pytest.approx(31.068559)
    assert hour["visibilityKm"] == pytest.approx(10.0)
    assert result["coverage"]["complete"] is True
    assert len(result["sourceHash"]) == 64
    assert result["cache"]["hit"] is False

    cached = adapter.fetch_hourly(52.2297, 21.0122, now=NOW)
    assert cached["cache"]["hit"] is True
    assert len(requests) == 1


def test_weather_http_errors_are_sanitized_and_retries_are_bounded() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(503, text=f"provider leaked {WEATHER_KEY}")

    adapter = WeatherAPIAdapter(
        _settings(),
        transport=httpx.MockTransport(handler),
        base_url="https://weather.test/v1",
        cache=ContextCache(),
        max_attempts=99,
        sleep=lambda _: None,
    )

    with pytest.raises(ContextProviderError) as caught:
        adapter.fetch_hourly(52.2297, 21.0122, now=NOW)

    assert len(requests) == 3
    assert caught.value.code == "http_503"
    assert caught.value.retryable is True
    assert WEATHER_KEY not in str(caught.value)


def test_ors_matrix_sends_lon_lat_and_keeps_directed_legs() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.scheme == "https"
        assert request.url.path == "/v2/matrix/driving-car"
        assert request.headers["Authorization"] == ROUTING_KEY
        body = request.read()
        assert b"21.0122" in body and b"52.2297" in body
        return httpx.Response(
            200,
            json={
                "durations": [[0, 3600], [1800, 0]],
                "distances": [[0, 12_000], [8_000, 0]],
            },
        )

    adapter = ORSMatrixAdapter(
        _settings(),
        transport=httpx.MockTransport(handler),
        base_url="https://routing.test",
        cache=ContextCache(),
        sleep=lambda _: None,
    )
    result = adapter.fetch_matrix(
        {
            "alpha": {"latitude": 52.2297, "longitude": 21.0122},
            "beta": {"latitude": 52.4064, "longitude": 16.9252},
        },
        now=NOW,
    )

    assert len(requests) == 1
    pairs = {(pair["fromProjectId"], pair["toProjectId"]): pair for pair in result["pairs"]}
    assert pairs["alpha", "beta"]["durationHours"] == pytest.approx(1.0)
    assert pairs["beta", "alpha"]["durationHours"] == pytest.approx(0.5)
    assert pairs["alpha", "beta"]["distanceKilometers"] == pytest.approx(12.0)
    assert result["coverage"]["complete"] is True
    assert result["trafficSpecific"] is False


def test_enrich_context_updates_confirmed_transfer_assumptions_and_only_rule_tasks() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/forecast.json"):
            return httpx.Response(200, json=_weather_payload(wind_kph=40.0))
        assert request.url.path == "/v2/matrix/driving-car"
        return httpx.Response(
            200,
            json={
                "durations": [[0, 3600], [1800, 0]],
                "distances": [[0, 12_000], [8_000, 0]],
            },
        )

    settings = _settings()
    cache = ContextCache()
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
    planning = {
        "asOf": NOW.isoformat(),
        "horizonDays": 2,
        "projects": [
            {
                "id": "alpha",
                "location": {"latitude": 52.2297, "longitude": 21.0122},
            },
            {
                "id": "beta",
                "location": {"latitude": 52.4064, "longitude": 16.9252},
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
            {"id": "indoor-task", "projectId": "alpha", "workabilityMode": "indoor"},
            {
                "id": "unconfirmed-task",
                "projectId": "alpha",
                "weatherRules": {"status": "proposed", "maxWindKph": 1},
            },
        ],
        "workers": [],
        "reservations": [],
        "transferAssumptions": [
            {
                "fromProjectId": "alpha",
                "toProjectId": "beta",
                "workerId": "worker-1",
                "startsAt": NOW.isoformat(),
                "endsAt": "2026-09-20T16:00:00+00:00",
                "setupHours": 1,
            }
        ],
    }

    result = enrich_context(
        planning,
        settings,
        weather_adapter=weather,
        routing_adapter=routing,
        cache=cache,
        now=NOW,
    )
    enriched = result["planning"]
    transfer = enriched["transferAssumptions"][0]

    assert "workability" in enriched["tasks"][0]
    assert enriched["tasks"][0]["workability"]["capacityBySlot"]
    assert list(enriched["tasks"][0]["workability"]["capacityBySlot"].values()) == [0.0]
    assert "workability" not in enriched["tasks"][1]
    assert "workability" not in enriched["tasks"][2]
    assert transfer["outboundTravelHours"] == pytest.approx(1.0)
    assert transfer["returnTravelHours"] == pytest.approx(0.5)
    assert "transfers" not in enriched
    assert result["status"]["weather"]["status"] == ProviderStatus.AVAILABLE.value
    assert result["status"]["routing"]["status"] == ProviderStatus.AVAILABLE.value
    assert result["status"]["weather"]["sourceHashes"]["alpha"]
    assert result["status"]["routing"]["sourceHash"]
    assert len(result["revision"]) == 64
    assert planning["tasks"][0].get("workability") is None
    assert "outboundTravelHours" not in planning["transferAssumptions"][0]


def test_missing_provider_or_coverage_never_adds_favorable_values() -> None:
    planning = {
        "projects": [{"id": "alpha", "location": {"latitude": 52.2, "longitude": 21.0}}],
        "tasks": [
            {
                "id": "weather-task",
                "projectId": "alpha",
                "weatherRules": {"confirmed": True, "maxWindKph": 20},
            }
        ],
        "transferAssumptions": [
            {"fromProjectId": "alpha", "toProjectId": "beta", "workerId": "w1"}
        ],
    }

    result = enrich_context(planning, _settings(weatherapi_api_key="", openrouteservice_api_key=""))
    task = result["planning"]["tasks"][0]
    transfer = result["planning"]["transferAssumptions"][0]

    assert result["status"]["weather"]["status"] == ProviderStatus.NOT_CONFIGURED.value
    assert task["workability"]["capacityBySlot"] == {}
    assert "outboundTravelHours" not in transfer
    assert "returnTravelHours" not in transfer
    assert any("weather_provider_not_configured" in warning for warning in result["warnings"])
    assert any("routing_coordinates_missing" in warning for warning in result["warnings"])


def test_weather_provider_error_does_not_leak_query_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text=f"bad key {WEATHER_KEY}")

    adapter = WeatherAPIAdapter(
        _settings(),
        transport=httpx.MockTransport(handler),
        base_url="https://weather.test/v1",
        cache=ContextCache(),
        sleep=lambda _: None,
    )

    with pytest.raises(ContextProviderError) as caught:
        adapter.fetch_hourly(52.2, 21.0, now=NOW)

    assert "weatherapi:http_401" == str(caught.value)
    assert WEATHER_KEY not in repr(caught.value)
