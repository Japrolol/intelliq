"""External weather and travel context for the bounded portfolio engine.

The adapters in this module are deliberately read-only.  They normalize provider
units and retain source/coverage metadata before the context is handed to the
simulation engine.  A forecast is allowed to affect a task only when that task
contains an explicitly confirmed ``weatherRules`` block; provider data alone is
never treated as a universal stop-work policy.

The public seam is :func:`enrich_context`.  It returns a deep-copied planning
payload so an integration failure cannot mutate the manager-confirmed source
overlay.  Provider failures and stale cache use remain visible in the returned
typed status rather than being replaced with favorable defaults.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from src.app.config import Settings

JsonObject = dict[str, Any]

WEATHER_API_BASE_URL = "https://api.weatherapi.com/v1"
OPENROUTESERVICE_BASE_URL = "https://api.openrouteservice.org"
DEFAULT_ROUTING_PROFILE = "driving-car"
DEFAULT_HTTP_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_WEATHER_CACHE_SECONDS = 3_600.0
DEFAULT_ROUTING_CACHE_SECONDS = 86_400.0
MAX_CACHE_SECONDS = 7 * 24 * 60 * 60

_TRANSIENT_HTTP_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})
_PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class ProviderStatus(StrEnum):
    """Stable status values rendered by consumers of the context seam."""

    NOT_REQUESTED = "not_requested"
    NOT_CONFIGURED = "not_configured"
    AVAILABLE = "available"
    PARTIAL = "partial"
    STALE = "stale"
    ERROR = "error"
    UNAVAILABLE = "unavailable"


class ContextProviderError(RuntimeError):
    """Sanitized provider failure that never contains credentials or response bodies."""

    def __init__(
        self,
        provider: str,
        code: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        attempts: int = 1,
    ) -> None:
        self.provider = provider
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
        self.attempts = attempts
        # Do not include the URL, query string, provider body, or exception text.
        super().__init__(f"{provider}:{code}")


@dataclass(frozen=True)
class ProjectCoordinates:
    """Validated WGS84 coordinates used by both providers."""

    latitude: float
    longitude: float

    def as_dict(self) -> JsonObject:
        return {"latitude": self.latitude, "longitude": self.longitude}


@dataclass(frozen=True)
class _CacheEntry:
    value: JsonObject
    stored_at: float
    expires_at: float


class ContextCache:
    """Small process-local TTL cache for normalized public provider responses.

    The cache is keyed by ordered coordinates/provider options.  It contains no
    user credentials and callers can inject a fresh instance in tests or a
    request-scoped integration.  Expired values remain available as explicitly
    stale last-known context when a provider is unavailable.
    """

    def __init__(self) -> None:
        self._entries: dict[str, _CacheEntry] = {}

    def get(
        self, key: str, *, now: float | None = None, allow_stale: bool = False
    ) -> tuple[JsonObject, float, bool] | None:
        current = time.time() if now is None else now
        entry = self._entries.get(key)
        if entry is None:
            return None
        stale = current >= entry.expires_at
        if stale and not allow_stale:
            return None
        return copy.deepcopy(entry.value), max(0.0, current - entry.stored_at), stale

    def set(self, key: str, value: JsonObject, *, now: float, ttl_seconds: float) -> None:
        bounded_ttl = max(1.0, min(float(ttl_seconds), MAX_CACHE_SECONDS))
        self._entries[key] = _CacheEntry(
            value=copy.deepcopy(value),
            stored_at=now,
            expires_at=now + bounded_ttl,
        )

    def clear(self) -> None:
        """Drop entries; useful for isolated application or focused tests."""

        self._entries.clear()


_DEFAULT_CONTEXT_CACHE = ContextCache()


def clear_context_cache() -> None:
    """Clear the default process-local context cache."""

    _DEFAULT_CONTEXT_CACHE.clear()


class WeatherAPIAdapter:
    """Fetch and normalize WeatherAPI hourly forecasts over HTTPS."""

    provider = "weatherapi"

    def __init__(
        self,
        settings: object,
        *,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
        cache: ContextCache | None = None,
        base_url: str | None = None,
        max_attempts: int | None = None,
        timeout_seconds: float | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if client is not None and transport is not None:
            raise ValueError("provide either client or transport, not both")
        self.settings = settings
        self.transport = transport
        self.client = client
        self.cache = cache or _DEFAULT_CONTEXT_CACHE
        configured_base = _setting(settings, "weatherapi_base_url", "weather_api_base_url")
        self.base_url = base_url or _string_setting(configured_base, WEATHER_API_BASE_URL)
        self.max_attempts = _bounded_attempts(
            max_attempts
            if max_attempts is not None
            else _setting(settings, "context_max_attempts", "weather_max_attempts")
        )
        self.timeout_seconds = _bounded_timeout(
            timeout_seconds
            if timeout_seconds is not None
            else _setting(settings, "context_timeout_seconds", "weather_timeout_seconds")
        )
        self.sleep = sleep

    def fetch_hourly(
        self,
        latitude: float,
        longitude: float,
        *,
        days: int | None = None,
        now: datetime | None = None,
    ) -> JsonObject:
        """Return normalized hourly observations and explicit coverage metadata."""

        coordinates = _validate_coordinates(latitude, longitude)
        requested_days = _bounded_days(
            days
            if days is not None
            else _setting(self.settings, "weather_forecast_days", "weatherForecastDays")
        )
        cache_key = _cache_key(
            "weather",
            {
                "latitude": coordinates.latitude,
                "longitude": coordinates.longitude,
                "days": requested_days,
                "baseUrl": self.base_url,
            },
        )
        now_dt = _utc_datetime(now)
        now_epoch = now_dt.timestamp()
        cached = self.cache.get(cache_key, now=now_epoch)
        if cached is not None:
            return _with_cache_metadata(cached, now_epoch)

        api_key = _secret_setting(
            self.settings, "weatherapi_api_key", "weather_api_key", "weatherapi_key"
        )
        if not api_key:
            stale = self.cache.get(cache_key, now=now_epoch, allow_stale=True)
            if stale is not None:
                return _with_stale_metadata(stale, "not_configured", now_epoch)
            raise ContextProviderError(self.provider, "not_configured")

        endpoint = _weather_endpoint(self.base_url, self.provider)
        params = {
            "key": api_key,
            "q": f"{coordinates.latitude:.6f},{coordinates.longitude:.6f}",
            "days": requested_days,
            "aqi": "no",
            "alerts": "no",
        }
        try:
            payload, attempts = self._request_json("GET", endpoint, params=params)
            result = _normalize_weather_response(
                payload,
                coordinates,
                requested_days=requested_days,
                fetched_at=now_dt,
                attempts=attempts,
            )
        except ContextProviderError as error:
            stale = self.cache.get(cache_key, now=now_epoch, allow_stale=True)
            if stale is not None:
                return _with_stale_metadata(stale, error.code, now_epoch)
            raise
        self.cache.set(
            cache_key,
            result,
            now=now_epoch,
            ttl_seconds=_cache_ttl(
                self.settings,
                "weather_cache_seconds",
                DEFAULT_WEATHER_CACHE_SECONDS,
            ),
        )
        result["cache"] = {"hit": False, "stale": False, "ageSeconds": 0.0}
        return result

    # Compatibility aliases keep the adapter easy to use from small services.
    fetch = fetch_hourly
    forecast = fetch_hourly

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, object] | None = None,
    ) -> tuple[object, int]:
        return _request_json(
            self.provider,
            self._request,
            method,
            url,
            params=params,
            max_attempts=self.max_attempts,
            timeout_seconds=self.timeout_seconds,
            sleep=self.sleep,
        )

    def _request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        if self.client is not None:
            return self.client.request(method, url, **kwargs)
        with httpx.Client(
            transport=self.transport,
            timeout=self.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            return client.request(method, url, **kwargs)


class OpenRouteServiceMatrixAdapter:
    """Fetch and normalize a directed openrouteservice duration matrix."""

    provider = "openrouteservice"

    def __init__(
        self,
        settings: object,
        *,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
        cache: ContextCache | None = None,
        base_url: str | None = None,
        profile: str | None = None,
        max_attempts: int | None = None,
        timeout_seconds: float | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if client is not None and transport is not None:
            raise ValueError("provide either client or transport, not both")
        self.settings = settings
        self.transport = transport
        self.client = client
        self.cache = cache or _DEFAULT_CONTEXT_CACHE
        configured_base = _setting(
            settings, "openrouteservice_base_url", "routing_base_url", "route_base_url"
        )
        self.base_url = base_url or _string_setting(configured_base, OPENROUTESERVICE_BASE_URL)
        configured_profile = profile or _setting(
            settings, "routing_profile", "routingProfile", "ors_profile"
        )
        self.profile = _string_setting(configured_profile, DEFAULT_ROUTING_PROFILE).lower()
        self.max_attempts = _bounded_attempts(
            max_attempts
            if max_attempts is not None
            else _setting(settings, "context_max_attempts", "routing_max_attempts")
        )
        self.timeout_seconds = _bounded_timeout(
            timeout_seconds
            if timeout_seconds is not None
            else _setting(settings, "context_timeout_seconds", "routing_timeout_seconds")
        )
        self.sleep = sleep

    def fetch_matrix(
        self,
        locations: Mapping[str, ProjectCoordinates | Mapping[str, object] | Sequence[object]],
        *,
        now: datetime | None = None,
    ) -> JsonObject:
        """Return independent directed legs for the supplied project locations."""

        normalized_locations = _normalize_locations(locations)
        now_dt = _utc_datetime(now)
        now_epoch = now_dt.timestamp()
        cache_key = _cache_key(
            "routing",
            {
                "profile": self.profile,
                "baseUrl": self.base_url,
                "locations": normalized_locations,
            },
        )
        cached = self.cache.get(cache_key, now=now_epoch)
        if cached is not None:
            return _with_cache_metadata(cached, now_epoch)

        base_result = _empty_matrix_result(self.profile, normalized_locations, now_dt)
        if len(normalized_locations) < 2:
            base_result["sourceHash"] = _hash_json(base_result["pairs"])
            self.cache.set(
                cache_key,
                base_result,
                now=now_epoch,
                ttl_seconds=_cache_ttl(
                    self.settings,
                    "routing_cache_seconds",
                    DEFAULT_ROUTING_CACHE_SECONDS,
                ),
            )
            base_result["cache"] = {"hit": False, "stale": False, "ageSeconds": 0.0}
            return base_result

        api_key = _secret_setting(
            self.settings,
            "openrouteservice_api_key",
            "routing_api_key",
            "openrouteservice_key",
        )
        if not api_key:
            stale = self.cache.get(cache_key, now=now_epoch, allow_stale=True)
            if stale is not None:
                return _with_stale_metadata(stale, "not_configured", now_epoch)
            raise ContextProviderError(self.provider, "not_configured")

        if not _PROFILE_RE.fullmatch(self.profile):
            raise ContextProviderError(self.provider, "invalid_profile")
        endpoint = f"{_https_url(self.base_url, self.provider)}/v2/matrix/{self.profile}"
        body = {
            "locations": [
                [location["longitude"], location["latitude"]]
                for location in normalized_locations.values()
            ],
            "metrics": ["duration", "distance"],
        }
        headers = {"Authorization": api_key, "Content-Type": "application/json"}
        try:
            payload, attempts = self._request_json(
                "POST", endpoint, json_body=body, headers=headers
            )
            result = _normalize_matrix_response(
                payload,
                self.profile,
                normalized_locations,
                fetched_at=now_dt,
                attempts=attempts,
            )
        except ContextProviderError as error:
            stale = self.cache.get(cache_key, now=now_epoch, allow_stale=True)
            if stale is not None:
                return _with_stale_metadata(stale, error.code, now_epoch)
            raise
        self.cache.set(
            cache_key,
            result,
            now=now_epoch,
            ttl_seconds=_cache_ttl(
                self.settings,
                "routing_cache_seconds",
                DEFAULT_ROUTING_CACHE_SECONDS,
            ),
        )
        result["cache"] = {"hit": False, "stale": False, "ageSeconds": 0.0}
        return result

    matrix = fetch_matrix
    fetch = fetch_matrix

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        json_body: JsonObject | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[object, int]:
        return _request_json(
            self.provider,
            self._request,
            method,
            url,
            json_body=json_body,
            headers=headers,
            max_attempts=self.max_attempts,
            timeout_seconds=self.timeout_seconds,
            sleep=self.sleep,
        )

    def _request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        if self.client is not None:
            return self.client.request(method, url, **kwargs)
        with httpx.Client(
            transport=self.transport,
            timeout=self.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            return client.request(method, url, **kwargs)


# Short names are convenient in services and keep the provider boundary swappable.
ORSMatrixAdapter = OpenRouteServiceMatrixAdapter
OpenRouteServiceAdapter = OpenRouteServiceMatrixAdapter


def enrich_context(
    planning: dict[str, Any],
    settings: Settings | object,
    *,
    weather_adapter: WeatherAPIAdapter | None = None,
    routing_adapter: OpenRouteServiceMatrixAdapter | None = None,
    cache: ContextCache | None = None,
    now: datetime | None = None,
) -> JsonObject:
    """Enrich an independent planning copy with confirmed weather and directed routes.

    Weather calls are made for located projects; only confirmed task rules
    may turn observations into numerical workability restrictions.
    Route calls are made only for confirmed transfer overlays.  A missing key,
    coordinate, field, route entry, or forecast hour remains represented as a
    typed limitation; no zero travel or favorable weather value is synthesized.
    """

    if not isinstance(planning, dict):
        raise TypeError("planning must be a dictionary")
    enriched = copy.deepcopy(planning)
    active_cache = cache or _setting(settings, "context_cache") or _DEFAULT_CONTEXT_CACHE
    if not isinstance(active_cache, ContextCache):
        raise TypeError("context cache must be ContextCache")
    weather = weather_adapter or WeatherAPIAdapter(settings, cache=active_cache)
    routing = routing_adapter or OpenRouteServiceMatrixAdapter(settings, cache=active_cache)
    now_dt = _utc_datetime(now)

    projects = _mapping_rows(enriched.get("projects"))
    tasks = _mapping_rows(enriched.get("tasks"))
    transfer_key = "transferAssumptions" if "transferAssumptions" in enriched else "transfers"
    transfers = _mapping_rows(enriched.get(transfer_key))
    project_by_id = {
        str(project["id"]): project for project in projects if project.get("id") is not None
    }
    locations: dict[str, ProjectCoordinates] = {}
    location_status: dict[str, JsonObject] = {}
    for project_id, project in project_by_id.items():
        coordinates = _project_coordinates(project)
        if coordinates is None:
            location_status[project_id] = {
                "status": ProviderStatus.UNAVAILABLE.value,
                "reason": "coordinates_missing_or_invalid",
            }
        else:
            locations[project_id] = coordinates
            location_status[project_id] = {
                "status": ProviderStatus.AVAILABLE.value,
                "coordinates": coordinates.as_dict(),
            }

    warnings: list[str] = []
    external_context: JsonObject = {
        "locations": {
            project_id: {
                **coordinates.as_dict(),
                "source": "planning",
            }
            for project_id, coordinates in locations.items()
        },
        "weather": {},
        "travel": {"provider": routing.provider, "profile": routing.profile, "pairs": []},
        "sourceHashes": {"weather": {}, "routing": None},
    }
    weather_status = _base_provider_status(weather.provider)
    routing_status = _base_provider_status(routing.provider)

    confirmed_weather_tasks: dict[str, list[tuple[int, JsonObject]]] = {}
    for index, task in enumerate(tasks):
        raw_rules = task.get("weatherRules", task.get("weather_rules"))
        if raw_rules is None:
            continue
        rule_set, rule_state = _confirmed_weather_rules(raw_rules)
        if rule_set is None:
            warnings.append(f"weather_rules_{rule_state}:{_resource_id(task, index)}")
            continue
        project_id = _resource_id(task, index, key="projectId")
        confirmed_weather_tasks.setdefault(project_id, []).append((index, rule_set))

    # Forecast context is useful even for indoor work. Only explicit task rules
    # below may turn it into workability restrictions; fetching is not approval.
    for project_id in locations:
        confirmed_weather_tasks.setdefault(project_id, [])

    if confirmed_weather_tasks:
        weather_status["status"] = ProviderStatus.PARTIAL.value
        weather_status["state"] = ProviderStatus.PARTIAL.value
        weather_status["coverage"] = {"projectsRequested": len(confirmed_weather_tasks)}
        for project_id, task_rules in confirmed_weather_tasks.items():
            coordinates = locations.get(project_id)
            project_status: JsonObject
            if coordinates is None:
                project_status = {
                    "status": ProviderStatus.UNAVAILABLE.value,
                    "reason": "coordinates_missing_or_invalid",
                }
                warnings.append(f"weather_coordinates_missing:{project_id}")
                for task_index, rule_set in task_rules:
                    _apply_unavailable_weather(tasks[task_index], rule_set, task_index, warnings)
            else:
                try:
                    forecast = weather.fetch_hourly(
                        coordinates.latitude,
                        coordinates.longitude,
                        now=now_dt,
                    )
                except ContextProviderError as error:
                    project_status = _error_status(error)
                    warnings.append(f"weather_provider_{error.code}:{project_id}")
                    for task_index, rule_set in task_rules:
                        _apply_unavailable_weather(
                            tasks[task_index], rule_set, task_index, warnings
                        )
                else:
                    external_context["weather"][project_id] = forecast
                    source_hash = forecast.get("sourceHash")
                    if isinstance(source_hash, str):
                        external_context["sourceHashes"]["weather"][project_id] = source_hash
                        weather_status["sourceHashes"][project_id] = source_hash
                    project_status = _weather_project_status(forecast)
                    coverage = forecast.get("coverage")
                    if isinstance(coverage, Mapping):
                        weather_status.setdefault("coverageByProject", {})[project_id] = dict(
                            coverage
                        )
                    if project_status["status"] in {
                        ProviderStatus.PARTIAL.value,
                        ProviderStatus.STALE.value,
                    }:
                        warnings.append(f"weather_coverage_limited:{project_id}")
                    for task_index, rule_set in task_rules:
                        _apply_weather_rules(
                            tasks[task_index],
                            rule_set,
                            forecast,
                            task_index,
                            warnings,
                        )
            weather_status.setdefault("projects", {})[project_id] = project_status
        weather_status["status"] = _aggregate_project_status(weather_status["projects"])
        weather_status["state"] = weather_status["status"]
    else:
        weather_status["status"] = ProviderStatus.NOT_REQUESTED.value
        weather_status["state"] = ProviderStatus.NOT_REQUESTED.value
        weather_status["coverage"] = {"projectsRequested": 0, "complete": True}

    confirmed_transfers: list[tuple[int, JsonObject]] = []
    routing_location_ids: list[str] = []
    routing_locations: dict[str, ProjectCoordinates] = {}
    for index, transfer in enumerate(transfers):
        if not _is_confirmed_transfer(transfer):
            continue
        from_id = _optional_id(transfer.get("fromProjectId", transfer.get("from_project_id")))
        to_id = _optional_id(transfer.get("toProjectId", transfer.get("to_project_id")))
        if from_id is None or to_id is None:
            warnings.append(f"routing_transfer_projects_missing:{_resource_id(transfer, index)}")
            continue
        confirmed_transfers.append((index, transfer))
        for project_id in (from_id, to_id):
            if project_id not in routing_location_ids:
                routing_location_ids.append(project_id)
            if project_id in locations:
                routing_locations[project_id] = locations[project_id]

    if not confirmed_transfers:
        routing_status["status"] = ProviderStatus.NOT_REQUESTED.value
        routing_status["state"] = ProviderStatus.NOT_REQUESTED.value
        routing_status["coverage"] = {"pairsRequested": 0, "complete": True}
    else:
        missing_location_ids = [
            project_id for project_id in routing_location_ids if project_id not in routing_locations
        ]
        for project_id in missing_location_ids:
            warnings.append(f"routing_coordinates_missing:{project_id}")
        if len(routing_locations) < 2:
            routing_status["status"] = ProviderStatus.UNAVAILABLE.value
            routing_status["state"] = ProviderStatus.UNAVAILABLE.value
            routing_status["coverage"] = {
                "pairsRequested": len(confirmed_transfers) * 2,
                "pairsAvailable": 0,
                "complete": False,
            }
            for _, transfer in confirmed_transfers:
                _mark_missing_transfer_route(transfer)
        else:
            try:
                matrix = routing.fetch_matrix(routing_locations, now=now_dt)
            except ContextProviderError as error:
                routing_status = _error_status(error)
                warnings.append(f"routing_provider_{error.code}")
                for _, transfer in confirmed_transfers:
                    _mark_missing_transfer_route(transfer)
            else:
                external_context["travel"] = matrix
                source_hash = matrix.get("sourceHash")
                if isinstance(source_hash, str):
                    external_context["sourceHashes"]["routing"] = source_hash
                    routing_status["sourceHash"] = source_hash
                routing_status["coverage"] = matrix.get("coverage", {})
                routing_status["status"] = _matrix_status(matrix)
                routing_status["state"] = routing_status["status"]
                cache_metadata = matrix.get("cache")
                if isinstance(cache_metadata, Mapping):
                    routing_status["cache"] = dict(cache_metadata)
                    fallback_error = cache_metadata.get("fallbackErrorCode")
                    if isinstance(fallback_error, str):
                        routing_status["error"] = {
                            "code": fallback_error,
                            "lastKnown": True,
                        }
                pairs = matrix.get("pairs", [])
                pair_index = {
                    (str(pair.get("fromProjectId")), str(pair.get("toProjectId"))): pair
                    for pair in pairs
                    if isinstance(pair, Mapping)
                }
                for _, transfer in confirmed_transfers:
                    _apply_transfer_routes(transfer, pair_index, warnings)
                external_context["travel"]["pairs"] = pairs

    enriched["projects"] = projects
    enriched["tasks"] = tasks
    # The engine payload uses transferAssumptions.  Accepting the older
    # transfers spelling is useful for callers, but never leave route updates
    # stranded under a field the engine does not parse.
    enriched["transferAssumptions"] = transfers
    if transfer_key == "transfers":
        enriched["transfers"] = transfers
    enriched["externalContext"] = external_context
    enriched["contextRevision"] = None
    status = {
        "overall": _overall_status(weather_status, routing_status),
        "weather": weather_status,
        "routing": routing_status,
        "locations": location_status,
    }
    revision = _context_revision(enriched, status)
    enriched["contextRevision"] = revision
    return {
        "planning": enriched,
        "status": status,
        "warnings": _dedupe(warnings),
        "revision": revision,
    }


def _request_json(
    provider: str,
    request: Callable[..., httpx.Response],
    method: str,
    url: str,
    *,
    params: Mapping[str, object] | None = None,
    json_body: JsonObject | None = None,
    headers: Mapping[str, str] | None = None,
    max_attempts: int,
    timeout_seconds: float,
    sleep: Callable[[float], None],
) -> tuple[object, int]:
    """Perform one bounded, sanitized read with transient retry handling."""

    for attempt in range(1, max_attempts + 1):
        try:
            response = request(
                method,
                url,
                params=params,
                json=json_body,
                headers=headers,
                timeout=timeout_seconds,
            )
        except httpx.TimeoutException:
            if attempt < max_attempts:
                _retry_sleep(sleep, attempt)
                continue
            raise ContextProviderError(
                provider, "timeout", retryable=True, attempts=attempt
            ) from None
        except httpx.NetworkError:
            if attempt < max_attempts:
                _retry_sleep(sleep, attempt)
                continue
            raise ContextProviderError(
                provider, "network_error", retryable=True, attempts=attempt
            ) from None
        except httpx.HTTPError:
            raise ContextProviderError(
                provider, "http_error", retryable=False, attempts=attempt
            ) from None

        status_code = response.status_code
        if status_code in _TRANSIENT_HTTP_STATUS_CODES and attempt < max_attempts:
            _retry_sleep(sleep, attempt)
            continue
        if status_code >= 400:
            raise ContextProviderError(
                provider,
                f"http_{status_code}",
                status_code=status_code,
                retryable=status_code in _TRANSIENT_HTTP_STATUS_CODES,
                attempts=attempt,
            )
        try:
            return response.json(), attempt
        except (ValueError, UnicodeError):
            raise ContextProviderError(
                provider, "response_json_invalid", attempts=attempt
            ) from None
    raise AssertionError("bounded request loop exhausted")


def _normalize_weather_response(
    payload: object,
    coordinates: ProjectCoordinates,
    *,
    requested_days: int,
    fetched_at: datetime,
    attempts: int,
) -> JsonObject:
    if not isinstance(payload, Mapping):
        raise ContextProviderError("weatherapi", "response_shape_invalid", attempts=attempts)
    location = payload.get("location")
    forecast = payload.get("forecast")
    if not isinstance(location, Mapping) or not isinstance(forecast, Mapping):
        raise ContextProviderError("weatherapi", "response_shape_invalid", attempts=attempts)
    forecast_days = forecast.get("forecastday")
    if not isinstance(forecast_days, list):
        raise ContextProviderError("weatherapi", "forecast_days_missing", attempts=attempts)

    timezone_name = location.get("tz_id")
    timezone: ZoneInfo | None = None
    if isinstance(timezone_name, str) and timezone_name.strip():
        try:
            timezone = ZoneInfo(timezone_name.strip())
        except ZoneInfoNotFoundError:
            timezone_name = None
    hours: list[JsonObject] = []
    missing_fields: set[str] = set()
    expected_fields = {
        "temperatureC",
        "precipitationMm",
        "rainProbability",
        "windKph",
        "gustKph",
    }
    for day in forecast_days:
        if not isinstance(day, Mapping):
            continue
        raw_hours = day.get("hour")
        if not isinstance(raw_hours, list):
            continue
        for raw_hour in raw_hours:
            if not isinstance(raw_hour, Mapping):
                continue
            normalized = _normalize_weather_hour(raw_hour, timezone)
            if normalized is None:
                continue
            hours.append(normalized)
            missing_fields.update(expected_fields.difference(normalized))

    hours.sort(key=lambda item: int(item["timestampEpoch"]))
    start_at = hours[0].get("timestamp") if hours else None
    end_at = hours[-1].get("timestamp") if hours else None
    available_days = len(forecast_days)
    complete = (
        bool(hours)
        and available_days >= requested_days
        and not missing_fields
        and timezone is not None
    )
    result: JsonObject = {
        "provider": "weatherapi",
        "location": {
            "latitude": coordinates.latitude,
            "longitude": coordinates.longitude,
            "timezone": timezone_name if isinstance(timezone_name, str) else None,
        },
        "hours": hours,
        "fetchedAt": fetched_at.isoformat(),
        "attempts": attempts,
        # Hash the consumed forecast, not current observations or response metadata.
        "sourceHash": _hash_json({"hours": hours, "timezone": timezone_name}),
        "coverage": {
            "requestedDays": requested_days,
            "availableDays": available_days,
            "hourCount": len(hours),
            "startAt": start_at,
            "endAt": end_at,
            "timezoneAvailable": timezone is not None,
            "missingFields": sorted(missing_fields),
            "complete": complete,
        },
    }
    if not hours:
        raise ContextProviderError("weatherapi", "forecast_hours_missing", attempts=attempts)
    return result


def _normalize_weather_hour(
    raw_hour: Mapping[str, object], timezone: ZoneInfo | None
) -> JsonObject | None:
    timestamp = _weather_timestamp(raw_hour, timezone)
    if timestamp is None:
        return None
    timestamp_epoch, timestamp_text = timestamp
    result: JsonObject = {
        "timestamp": timestamp_text,
        "timestampEpoch": timestamp_epoch,
    }
    _copy_normalized_number(result, raw_hour, "temperatureC", "temp_c", "temp_f", _f_to_c)
    _copy_normalized_number(
        result, raw_hour, "precipitationMm", "precip_mm", "precip_in", _in_to_mm
    )
    rain_probability = _number_value(raw_hour.get("chance_of_rain"))
    if rain_probability is not None:
        result["rainProbability"] = (
            rain_probability / 100.0 if rain_probability > 1.0 else rain_probability
        )
        result["rainProbabilityPercent"] = result["rainProbability"] * 100.0
    _copy_normalized_number(result, raw_hour, "windKph", "wind_kph", "wind_mph", _mph_to_kph)
    _copy_normalized_number(result, raw_hour, "gustKph", "gust_kph", "gust_mph", _mph_to_kph)
    _copy_normalized_number(result, raw_hour, "visibilityKm", "vis_km", "vis_miles", _miles_to_km)
    for source, target in (
        ("humidity", "humidityPercent"),
        ("cloud", "cloudPercent"),
        ("uv", "uvIndex"),
        ("snow_cm", "snowCm"),
    ):
        value = _number_value(raw_hour.get(source))
        if value is not None:
            result[target] = value
    condition = raw_hour.get("condition")
    if isinstance(condition, Mapping):
        text = condition.get("text")
        code = _number_value(condition.get("code"))
        if isinstance(text, str):
            result["conditionText"] = text
        if code is not None:
            result["conditionCode"] = int(code)
    return result


def _normalize_matrix_response(
    payload: object,
    profile: str,
    locations: dict[str, JsonObject],
    *,
    fetched_at: datetime,
    attempts: int,
) -> JsonObject:
    if not isinstance(payload, Mapping):
        raise ContextProviderError("openrouteservice", "response_shape_invalid", attempts=attempts)
    durations = payload.get("durations")
    distances = payload.get("distances")
    if not isinstance(durations, list):
        raise ContextProviderError("openrouteservice", "durations_missing", attempts=attempts)
    location_ids = list(locations)
    pairs: list[JsonObject] = []
    missing_pairs: list[str] = []
    requested_pairs = max(0, len(location_ids) * (len(location_ids) - 1))
    for origin_index, origin_id in enumerate(location_ids):
        duration_row = durations[origin_index] if origin_index < len(durations) else None
        distance_row = (
            distances[origin_index]
            if isinstance(distances, list) and origin_index < len(distances)
            else None
        )
        for destination_index, destination_id in enumerate(location_ids):
            if origin_index == destination_index:
                continue
            duration = _matrix_value(duration_row, destination_index)
            distance = _matrix_value(distance_row, destination_index)
            pair_key = f"{origin_id}->{destination_id}"
            if duration is None or duration < 0:
                missing_pairs.append(pair_key)
                continue
            pair: JsonObject = {
                "fromProjectId": origin_id,
                "toProjectId": destination_id,
                "durationSeconds": duration,
                "durationHours": duration / 3_600.0,
                "distanceMeters": distance,
            }
            if distance is not None:
                pair["distanceKilometers"] = distance / 1_000.0
            pairs.append(pair)
    return {
        "provider": "openrouteservice",
        "profile": profile,
        "locations": locations,
        "pairs": pairs,
        "fetchedAt": fetched_at.isoformat(),
        "attempts": attempts,
        # Provider response timestamps change even when travel inputs do not.
        "sourceHash": _hash_json({"profile": profile, "locations": locations, "pairs": pairs}),
        "coverage": {
            "requestedPairs": requested_pairs,
            "availablePairs": len(pairs),
            "missingPairs": missing_pairs,
            "complete": not missing_pairs and len(pairs) == requested_pairs,
        },
        "trafficSpecific": False,
    }


def _empty_matrix_result(
    profile: str, locations: dict[str, JsonObject], fetched_at: datetime
) -> JsonObject:
    requested_pairs = max(0, len(locations) * (len(locations) - 1))
    return {
        "provider": "openrouteservice",
        "profile": profile,
        "locations": locations,
        "pairs": [],
        "fetchedAt": fetched_at.isoformat(),
        "attempts": 0,
        "coverage": {
            "requestedPairs": requested_pairs,
            "availablePairs": 0,
            "missingPairs": [],
            "complete": requested_pairs == 0,
        },
        "trafficSpecific": False,
    }


def _apply_weather_rules(
    task: JsonObject,
    rule_set: JsonObject,
    forecast: JsonObject,
    task_index: int,
    warnings: list[str],
) -> None:
    hours = forecast.get("hours")
    if not isinstance(hours, list):
        _apply_unavailable_weather(task, rule_set, task_index, warnings)
        return
    conditions, base_capacity, mode = _weather_conditions(rule_set)
    if not conditions and base_capacity is None:
        _apply_unavailable_weather(task, rule_set, task_index, warnings)
        warnings.append(f"weather_rules_invalid:{_resource_id(task, task_index)}")
        return
    capacities: dict[str, float] = {}
    missing_required = False
    for raw_hour in hours:
        if not isinstance(raw_hour, Mapping):
            continue
        value = _evaluate_weather_conditions(raw_hour, conditions, base_capacity)
        if value is None:
            missing_required = True
            continue
        timestamp = raw_hour.get("timestamp")
        if isinstance(timestamp, str):
            capacities[timestamp] = value
    profile: JsonObject = {
        "capacityBySlot": capacities,
        "source": {
            "provider": forecast.get("provider", "weatherapi"),
            "sourceHash": forecast.get("sourceHash"),
            "coverage": forecast.get("coverage", {}),
            "ruleStatus": "confirmed",
        },
    }
    if isinstance(mode, str) and mode.strip():
        profile["mode"] = mode.strip().lower()
        if not task.get("workabilityMode") and not task.get("workability_mode"):
            task["workabilityMode"] = profile["mode"]
    task["workability"] = _merge_workability(task.get("workability"), profile)
    if missing_required:
        warnings.append(f"weather_fields_unavailable:{_resource_id(task, task_index)}")
    if not capacities:
        warnings.append(f"weather_task_out_of_coverage:{_resource_id(task, task_index)}")


def _apply_unavailable_weather(
    task: JsonObject, rule_set: JsonObject, task_index: int, warnings: list[str]
) -> None:
    # An explicit weather-dependent task with no usable forecast must not fall
    # back to ordinary capacity.  The engine treats empty capacityBySlot as
    # unavailable for every uncovered slot, while status/warnings explain why.
    profile: JsonObject = {
        "capacityBySlot": {},
        "source": {"provider": "weatherapi", "ruleStatus": "confirmed", "coverage": "unavailable"},
    }
    mode = rule_set.get("mode")
    if isinstance(mode, str) and mode.strip():
        profile["mode"] = mode.strip().lower()
    task["workability"] = _merge_workability(task.get("workability"), profile)
    warnings.append(f"weather_effect_unavailable:{_resource_id(task, task_index)}")


def _apply_transfer_routes(
    transfer: JsonObject,
    pairs: Mapping[tuple[str, str], object],
    warnings: list[str],
) -> None:
    from_id = _optional_id(transfer.get("fromProjectId", transfer.get("from_project_id")))
    to_id = _optional_id(transfer.get("toProjectId", transfer.get("to_project_id")))
    if from_id is None or to_id is None:
        return
    outbound = pairs.get((from_id, to_id))
    returning = pairs.get((to_id, from_id))
    updated = False
    if (
        isinstance(outbound, Mapping)
        and _nonnegative_number(outbound.get("durationHours")) is not None
    ):
        transfer["outboundTravelHours"] = float(outbound["durationHours"])
        updated = True
    else:
        warnings.append(f"routing_leg_unavailable:{from_id}->{to_id}")
    if (
        isinstance(returning, Mapping)
        and _nonnegative_number(returning.get("durationHours")) is not None
    ):
        transfer["returnTravelHours"] = float(returning["durationHours"])
        updated = True
    else:
        warnings.append(f"routing_leg_unavailable:{to_id}->{from_id}")
    if updated:
        transfer["travelSource"] = {
            "provider": "openrouteservice",
            "directed": True,
            "trafficSpecific": False,
        }


def _mark_missing_transfer_route(transfer: JsonObject) -> None:
    # Deliberately do not add zero-valued friction.  Existing manager-confirmed
    # assumptions remain untouched and the engine will reject a missing value.
    transfer.setdefault("travelSource", {"provider": "openrouteservice", "available": False})


def _merge_workability(existing: object, weather_profile: JsonObject) -> JsonObject:
    if not isinstance(existing, Mapping):
        if isinstance(existing, (int, float)) and not isinstance(existing, bool):
            base = max(0.0, min(1.0, float(existing)))
            capacities = weather_profile.get("capacityBySlot")
            if isinstance(capacities, Mapping):
                weather_profile["capacityBySlot"] = {
                    str(key): base * float(value) for key, value in capacities.items()
                }
        return weather_profile
    merged = copy.deepcopy(dict(existing))
    weather_values = weather_profile.get("capacityBySlot")
    if isinstance(weather_values, Mapping):
        existing_values = merged.get("capacityBySlot")
        if isinstance(existing_values, Mapping):
            merged["capacityBySlot"] = {
                str(key): min(float(value), float(existing_values[key]))
                if key in existing_values
                else float(value)
                for key, value in weather_values.items()
            }
        else:
            merged["capacityBySlot"] = dict(weather_values)
    for key in ("mode", "source"):
        if key in weather_profile:
            merged[key] = weather_profile[key]
    return merged


@dataclass(frozen=True)
class _WeatherCondition:
    metric: str
    operator: str
    threshold: float
    violation_capacity: float | None
    stop_work: bool


def _confirmed_weather_rules(raw: object) -> tuple[JsonObject | None, str]:
    if not isinstance(raw, Mapping):
        return None, "rules_invalid"
    marker = raw.get("status", raw.get("confirmationStatus", raw.get("reviewStatus")))
    confirmed = raw.get("confirmed") is True or (
        isinstance(marker, str) and marker.strip().lower() == "confirmed"
    )
    if not confirmed:
        return None, "rules_unconfirmed"
    result = dict(raw)
    nested = raw.get("rules", raw.get("conditions"))
    if isinstance(nested, (Mapping, list)):
        result["rules"] = nested
    return result, "confirmed"


def _weather_conditions(
    rule_set: JsonObject,
) -> tuple[tuple[_WeatherCondition, ...], float | None, str | None]:
    base_capacity = _factor(
        rule_set.get(
            "capacity",
            rule_set.get(
                "workability",
                rule_set.get("capacityFactor", rule_set.get("defaultCapacity")),
            ),
        )
    )
    mode_value = rule_set.get("mode", rule_set.get("workabilityMode"))
    mode = mode_value if isinstance(mode_value, str) else None
    conditions: list[_WeatherCondition] = []
    nested = rule_set.get("rules", rule_set.get("conditions"))
    if isinstance(nested, list):
        for item in nested:
            if isinstance(item, Mapping):
                condition = _condition_from_mapping(item)
                if condition is not None:
                    conditions.append(condition)
    elif isinstance(nested, Mapping):
        for metric, specification in nested.items():
            condition = _condition_from_metric(metric, specification)
            if condition is not None:
                conditions.append(condition)
    flat_rules = dict(rule_set)
    if isinstance(nested, Mapping):
        for key, value in nested.items():
            flat_rules.setdefault(str(key), value)
    for key, metric, operator, use_percent in (
        ("maxTemperatureC", "temperatureC", "gt", False),
        ("minTemperatureC", "temperatureC", "lt", False),
        ("maxPrecipitationMm", "precipitationMm", "gt", False),
        ("maxRainProbability", "rainProbability", "gt", True),
        ("maxRainProbabilityPercent", "rainProbabilityPercent", "gt", False),
        ("maxWindKph", "windKph", "gt", False),
        ("maxGustKph", "gustKph", "gt", False),
        ("minVisibilityKm", "visibilityKm", "lt", False),
    ):
        value = _number_value(flat_rules.get(key))
        if value is None:
            continue
        if use_percent and value > 1.0:
            value /= 100.0
        effect = _factor(flat_rules.get(f"{key}Capacity", flat_rules.get(f"{key}Workability")))
        stop = flat_rules.get(f"{key}StopWork", flat_rules.get("stopWork", True)) is not False
        conditions.append(_WeatherCondition(metric, operator, value, effect, stop))
    if not conditions and rule_set.get("stopWork") is True:
        base_capacity = 0.0
    return tuple(conditions), base_capacity, mode


def _condition_from_mapping(item: Mapping[str, object]) -> _WeatherCondition | None:
    metric_value = item.get("metric", item.get("field", item.get("condition")))
    if not isinstance(metric_value, str):
        return None
    metric = _weather_metric(metric_value)
    if metric is None:
        return None
    operator = str(item.get("operator", "gt")).strip().lower()
    if operator == "greater_than":
        operator = "gt"
    if operator == "less_than":
        operator = "lt"
    threshold = _number_value(item.get("threshold", item.get("value")))
    if threshold is None:
        return None
    if metric == "rainProbability" and threshold > 1.0:
        threshold /= 100.0
    effect = item.get("effect")
    effect_mapping = effect if isinstance(effect, Mapping) else {}
    factor = _factor(
        item.get(
            "capacity",
            item.get(
                "workability",
                effect_mapping.get("capacity", effect_mapping.get("workability")),
            ),
        )
    )
    stop = item.get("stopWork", item.get("stop", effect_mapping.get("stopWork", True))) is not False
    return _WeatherCondition(metric, operator, threshold, factor, stop)


def _condition_from_metric(metric_value: object, specification: object) -> _WeatherCondition | None:
    if not isinstance(metric_value, str):
        return None
    metric = _weather_metric(metric_value)
    if metric is None:
        return None
    if isinstance(specification, Mapping):
        item = dict(specification)
        item.setdefault("metric", metric)
        return _condition_from_mapping(item)
    threshold = _number_value(specification)
    if threshold is None:
        return None
    if metric == "rainProbability" and threshold > 1.0:
        threshold /= 100.0
    return _WeatherCondition(metric, "gt", threshold, None, True)


def _evaluate_weather_conditions(
    hour: Mapping[str, object],
    conditions: Sequence[_WeatherCondition],
    base_capacity: float | None,
) -> float | None:
    if not conditions and base_capacity is not None:
        return base_capacity
    capacity = 1.0 if base_capacity is None else base_capacity
    for condition in conditions:
        actual = _number_value(hour.get(condition.metric))
        if actual is None:
            return None
        violated = _compare(actual, condition.operator, condition.threshold)
        if violated:
            if condition.violation_capacity is not None:
                capacity = min(capacity, condition.violation_capacity)
            elif condition.stop_work:
                capacity = 0.0
    return max(0.0, min(1.0, capacity))


def _weather_metric(value: str) -> str | None:
    normalized = value.strip()
    aliases = {
        "temperature": "temperatureC",
        "temperature_c": "temperatureC",
        "tempC": "temperatureC",
        "precipitation": "precipitationMm",
        "precipitation_mm": "precipitationMm",
        "rainMm": "precipitationMm",
        "rain_probability": "rainProbability",
        "rainProbabilityPercent": "rainProbabilityPercent",
        "wind": "windKph",
        "wind_kph": "windKph",
        "gust": "gustKph",
        "gust_kph": "gustKph",
        "visibility": "visibilityKm",
    }
    return aliases.get(
        normalized,
        normalized
        if normalized
        in {
            "temperatureC",
            "precipitationMm",
            "rainProbability",
            "rainProbabilityPercent",
            "windKph",
            "gustKph",
            "visibilityKm",
            "humidityPercent",
            "cloudPercent",
            "uvIndex",
        }
        else None,
    )


def _compare(actual: float, operator: str, threshold: float) -> bool:
    if operator in {"gt", ">"}:
        return actual > threshold
    if operator in {"gte", ">="}:
        return actual >= threshold
    if operator in {"lt", "<"}:
        return actual < threshold
    if operator in {"lte", "<="}:
        return actual <= threshold
    if operator in {"eq", "=="}:
        return math.isclose(actual, threshold)
    if operator in {"neq", "!="}:
        return not math.isclose(actual, threshold)
    return False


def _project_coordinates(project: Mapping[str, object]) -> ProjectCoordinates | None:
    candidates: list[object] = [project]
    for key in ("defaultLocation", "default_location", "location", "coordinates"):
        value = project.get(key)
        if value is not None:
            candidates.append(value)
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            latitude = _number_value(candidate.get("latitude", candidate.get("lat")))
            longitude = _number_value(
                candidate.get("longitude", candidate.get("lon", candidate.get("lng")))
            )
            if latitude is not None and longitude is not None:
                try:
                    return _validate_coordinates(latitude, longitude)
                except ValueError:
                    continue
        elif isinstance(candidate, Sequence) and not isinstance(candidate, (str, bytes, bytearray)):
            values = list(candidate)
            if len(values) == 2:
                # GeoJSON-style coordinate arrays are [longitude, latitude].
                longitude = _number_value(values[0])
                latitude = _number_value(values[1])
                if latitude is not None and longitude is not None:
                    try:
                        return _validate_coordinates(latitude, longitude)
                    except ValueError:
                        continue
    return None


def _normalize_locations(
    locations: Mapping[str, ProjectCoordinates | Mapping[str, object] | Sequence[object]],
) -> dict[str, JsonObject]:
    normalized: dict[str, JsonObject] = {}
    for identifier, value in locations.items():
        if isinstance(value, ProjectCoordinates):
            coordinates = value
        elif isinstance(value, Mapping):
            latitude = _number_value(value.get("latitude", value.get("lat")))
            longitude = _number_value(value.get("longitude", value.get("lon", value.get("lng"))))
            if latitude is None or longitude is None:
                raise ValueError(f"invalid coordinates for {identifier}")
            coordinates = _validate_coordinates(latitude, longitude)
        else:
            values = list(value)
            if len(values) != 2:
                raise ValueError(f"invalid coordinates for {identifier}")
            longitude = _number_value(values[0])
            latitude = _number_value(values[1])
            if latitude is None or longitude is None:
                raise ValueError(f"invalid coordinates for {identifier}")
            coordinates = _validate_coordinates(latitude, longitude)
        normalized[str(identifier)] = coordinates.as_dict()
    return normalized


def _weather_project_status(forecast: Mapping[str, object]) -> JsonObject:
    cache = forecast.get("cache")
    if isinstance(cache, Mapping) and cache.get("stale") is True:
        state = ProviderStatus.STALE.value
    else:
        coverage = forecast.get("coverage")
        complete = isinstance(coverage, Mapping) and coverage.get("complete") is True
        state = ProviderStatus.AVAILABLE.value if complete else ProviderStatus.PARTIAL.value
    result: JsonObject = {
        "status": state,
        "state": state,
        "coverage": forecast.get("coverage", {}),
    }
    if isinstance(cache, Mapping):
        result["cache"] = dict(cache)
        fallback_error = cache.get("fallbackErrorCode")
        if isinstance(fallback_error, str):
            result["error"] = {"code": fallback_error, "lastKnown": True}
    return result


def _matrix_status(matrix: Mapping[str, object]) -> str:
    cache = matrix.get("cache")
    if isinstance(cache, Mapping) and cache.get("stale") is True:
        return ProviderStatus.STALE.value
    coverage = matrix.get("coverage")
    return (
        ProviderStatus.AVAILABLE.value
        if isinstance(coverage, Mapping) and coverage.get("complete") is True
        else ProviderStatus.PARTIAL.value
    )


def _base_provider_status(provider: str) -> JsonObject:
    return {
        "provider": provider,
        "status": ProviderStatus.NOT_REQUESTED.value,
        "state": ProviderStatus.NOT_REQUESTED.value,
        "sourceHashes": {},
        "projects": {},
    }


def _error_status(error: ContextProviderError) -> JsonObject:
    state = (
        ProviderStatus.NOT_CONFIGURED.value
        if error.code == "not_configured"
        else ProviderStatus.ERROR.value
    )
    result: JsonObject = {
        "provider": error.provider,
        "status": state,
        "state": state,
        "error": {
            "code": error.code,
            "retryable": error.retryable,
            "attempts": error.attempts,
        },
    }
    if error.status_code is not None:
        result["error"]["statusCode"] = error.status_code
    return result


def _aggregate_project_status(projects: Mapping[str, object]) -> str:
    states = {str(value.get("status")) for value in projects.values() if isinstance(value, Mapping)}
    if not states:
        return ProviderStatus.NOT_REQUESTED.value
    if states == {ProviderStatus.AVAILABLE.value}:
        return ProviderStatus.AVAILABLE.value
    if states == {ProviderStatus.NOT_CONFIGURED.value}:
        return ProviderStatus.NOT_CONFIGURED.value
    if states == {ProviderStatus.UNAVAILABLE.value}:
        return ProviderStatus.UNAVAILABLE.value
    if ProviderStatus.STALE.value in states and states.issubset(
        {ProviderStatus.STALE.value, ProviderStatus.AVAILABLE.value}
    ):
        return ProviderStatus.STALE.value
    return ProviderStatus.PARTIAL.value


def _overall_status(weather: Mapping[str, object], routing: Mapping[str, object]) -> str:
    active = [
        str(value.get("status"))
        for value in (weather, routing)
        if str(value.get("status")) != ProviderStatus.NOT_REQUESTED.value
    ]
    if not active:
        return ProviderStatus.NOT_REQUESTED.value
    if all(value == ProviderStatus.AVAILABLE.value for value in active):
        return ProviderStatus.AVAILABLE.value
    if all(value == ProviderStatus.NOT_CONFIGURED.value for value in active):
        return ProviderStatus.NOT_CONFIGURED.value
    if all(value == ProviderStatus.UNAVAILABLE.value for value in active):
        return ProviderStatus.UNAVAILABLE.value
    return ProviderStatus.PARTIAL.value


def _resource_id(item: Mapping[str, object], index: int, *, key: str = "id") -> str:
    value = item.get(key)
    return str(value) if value not in (None, "") else f"index-{index}"


def _mapping_rows(value: object) -> list[JsonObject]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _is_confirmed_transfer(transfer: Mapping[str, object]) -> bool:
    marker = transfer.get(
        "status", transfer.get("confirmationStatus", transfer.get("reviewStatus"))
    )
    if isinstance(marker, str):
        return marker.strip().lower() == "confirmed"
    if transfer.get("confirmed") is False:
        return False
    # PlanningInputs.transfers are already manager-confirmed overlays.  An
    # absent marker therefore means confirmed, while an explicit proposal does not.
    return True


def _setting(settings: object, *names: str, default: object = None) -> object:
    for name in names:
        value = getattr(settings, name, None)
        if value is not None:
            return value
    return default


def _secret_setting(settings: object, *names: str) -> str:
    value = _setting(settings, *names)
    return value.strip() if isinstance(value, str) else ""


def _string_setting(value: object, default: str) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else default


def _bounded_attempts(value: object) -> int:
    number = _number_value(value)
    if number is None:
        return DEFAULT_MAX_ATTEMPTS
    return max(1, min(DEFAULT_MAX_ATTEMPTS, int(number)))


def _bounded_timeout(value: object) -> float:
    number = _number_value(value)
    if number is None:
        return DEFAULT_HTTP_TIMEOUT_SECONDS
    return max(0.1, min(60.0, number))


def _bounded_days(value: object) -> int:
    number = _number_value(value)
    if number is None:
        return 7
    return max(1, min(14, int(number)))


def _cache_ttl(settings: object, name: str, default: float) -> float:
    value = _number_value(_setting(settings, name))
    return default if value is None else max(1.0, min(MAX_CACHE_SECONDS, value))


def _utc_datetime(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _https_url(value: str, provider: str) -> str:
    normalized = value.rstrip("/")
    if not normalized.lower().startswith("https://"):
        raise ContextProviderError(provider, "https_required")
    return normalized


def _weather_endpoint(value: str, provider: str) -> str:
    base = _https_url(value, provider)
    return base if base.endswith("/forecast.json") else f"{base}/forecast.json"


def _retry_sleep(sleep: Callable[[float], None], attempt: int) -> None:
    # Keep backoff bounded and injectable; tests pass a no-op sleep.
    sleep(min(0.25, 0.05 * (2 ** (attempt - 1))))


def _validate_coordinates(latitude: float, longitude: float) -> ProjectCoordinates:
    if not math.isfinite(latitude) or not -90.0 <= latitude <= 90.0:
        raise ValueError("latitude must be between -90 and 90")
    if not math.isfinite(longitude) or not -180.0 <= longitude <= 180.0:
        raise ValueError("longitude must be between -180 and 180")
    return ProjectCoordinates(float(latitude), float(longitude))


def _weather_timestamp(
    raw_hour: Mapping[str, object], timezone: ZoneInfo | None
) -> tuple[int, str] | None:
    epoch = _number_value(raw_hour.get("time_epoch"))
    if epoch is not None:
        utc_value = datetime.fromtimestamp(epoch, UTC)
        local_value = utc_value.astimezone(timezone) if timezone is not None else utc_value
        return int(epoch), local_value.isoformat()
    raw_time = raw_hour.get("time")
    if not isinstance(raw_time, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone or UTC)
    return int(parsed.timestamp()), parsed.isoformat()


def _copy_normalized_number(
    target: JsonObject,
    source: Mapping[str, object],
    target_key: str,
    primary_key: str,
    alternate_key: str,
    conversion: Callable[[float], float],
) -> None:
    value = _number_value(source.get(primary_key))
    if value is None:
        alternate = _number_value(source.get(alternate_key))
        value = conversion(alternate) if alternate is not None else None
    if value is not None and math.isfinite(value):
        target[target_key] = float(value)


def _matrix_value(row: object, index: int) -> float | None:
    if not isinstance(row, list) or index >= len(row):
        return None
    return _nonnegative_number(row[index])


def _number_value(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def _nonnegative_number(value: object) -> float | None:
    number = _number_value(value)
    return number if number is not None and number >= 0.0 else None


def _factor(value: object) -> float | None:
    number = _number_value(value)
    if number is None:
        return None
    if number > 1.0 and number <= 100.0:
        number /= 100.0
    return max(0.0, min(1.0, number))


def _optional_id(value: object) -> str | None:
    return str(value).strip() if value not in (None, "") else None


def _cache_key(provider: str, value: object) -> str:
    return f"{provider}:{_hash_json(value)}"


def _hash_json(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _with_cache_metadata(cached: tuple[JsonObject, float, bool], now_epoch: float) -> JsonObject:
    value, age_seconds, stale = cached
    value["cache"] = {
        "hit": True,
        "stale": stale,
        "ageSeconds": round(age_seconds, 3),
    }
    return value


def _with_stale_metadata(
    cached: tuple[JsonObject, float, bool], error_code: str, now_epoch: float
) -> JsonObject:
    value = _with_cache_metadata(cached, now_epoch)
    value["cache"]["stale"] = True
    value["cache"]["fallbackErrorCode"] = error_code
    return value


def _context_revision(planning: Mapping[str, object], status: Mapping[str, object]) -> str:
    revision_planning = copy.deepcopy(dict(planning))
    external_context = revision_planning.get("externalContext")
    if isinstance(external_context, Mapping):
        revision_external = dict(external_context)
        weather = revision_external.get("weather")
        if isinstance(weather, Mapping):
            revision_external["weather"] = {
                str(project_id): _stable_context_value(forecast)
                for project_id, forecast in weather.items()
            }
        travel = revision_external.get("travel")
        if isinstance(travel, Mapping):
            revision_external["travel"] = _stable_context_value(travel)
        revision_planning["externalContext"] = revision_external
    revision_planning.pop("contextRevision", None)
    material = {
        "planning": revision_planning,
        "status": _revision_status(status),
    }
    return _hash_json(material)


def _stable_context_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _stable_context_value(item)
            for key, item in value.items()
            if key not in {"fetchedAt", "attempts", "cache"}
        }
    if isinstance(value, list):
        return [_stable_context_value(item) for item in value]
    return value


def _revision_status(status: Mapping[str, object]) -> JsonObject:
    result: JsonObject = {}
    for provider in ("weather", "routing"):
        value = status.get(provider)
        if not isinstance(value, Mapping):
            continue
        result[provider] = {
            "status": value.get("status"),
            "sourceHash": value.get("sourceHash"),
            "sourceHashes": value.get("sourceHashes"),
            "coverage": value.get("coverage"),
            "coverageByProject": value.get("coverageByProject"),
        }
    return result


def _dedupe(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _f_to_c(value: float) -> float:
    return (value - 32.0) * 5.0 / 9.0


def _in_to_mm(value: float) -> float:
    return value * 25.4


def _mph_to_kph(value: float) -> float:
    return value * 1.609344


def _miles_to_km(value: float) -> float:
    return value * 1.609344


__all__ = [
    "ContextCache",
    "ContextProviderError",
    "ORSMatrixAdapter",
    "OpenRouteServiceAdapter",
    "OpenRouteServiceMatrixAdapter",
    "ProjectCoordinates",
    "ProviderStatus",
    "WeatherAPIAdapter",
    "clear_context_cache",
    "enrich_context",
]
