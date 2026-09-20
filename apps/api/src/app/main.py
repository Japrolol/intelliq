"""Runnable FastAPI application for the IntelliQ backend-for-frontend."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from time import perf_counter
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from src.app.api.auth_service import AuthService
from src.app.api.data_routes import router as data_router
from src.app.api.routes import router
from src.app.api.v4_routes import router as v4_router
from src.app.config import Settings, get_settings
from src.app.integrations.fixture import FixtureTimecueAdapter
from src.app.integrations.timecue import LiveTimecueAdapter, TimecueAuthClient
from src.app.integrations.token_vault import DurableTokenVault, ProcessTokenVault
from src.app.persistence.store import Store


def create_app(settings: Settings | None = None, *, transport: Any = None) -> FastAPI:
    """Construct an app with isolated settings and persistence for tests or a process."""

    app_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = Store(app_settings.database_url)
        store.migrate_schema()
        fixture = FixtureTimecueAdapter()
        if (
            app_settings.data_mode == "live"
            and app_settings.jobs_enabled
            and not app_settings.session_encryption_key
        ):
            raise RuntimeError("SESSION_ENCRYPTION_KEY is required for live background jobs")
        token_vault = (
            DurableTokenVault(
                app_settings.database_url, app_settings.session_encryption_key, engine=store.engine
            )
            if app_settings.session_encryption_key
            else ProcessTokenVault()
        )
        auth_client = TimecueAuthClient(app_settings, transport=transport, vault=token_vault)
        auth = AuthService(app_settings.data_mode, store, fixture, auth_client)
        adapter = (
            fixture
            if app_settings.data_mode == "fixture"
            else LiveTimecueAdapter(app_settings, auth_client, auth.refresh_for_upstream)
        )
        app.state.settings = app_settings
        app.state.store = store
        app.state.fixture = fixture
        app.state.auth = auth
        app.state.adapter = adapter
        app.state.token_vault = token_vault
        try:
            yield
        finally:
            from src.app.services.simulation_executor import shutdown_simulation_executor

            shutdown_simulation_executor(wait=False, terminate=True)
            store.engine.dispose()

    app = FastAPI(title="IntelliQ Backend", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def request_log(request: Request, call_next):
        started = perf_counter()
        request_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        logging.getLogger("intelliq.requests").info(
            json.dumps(
                {
                    "event": "http_request",
                    "requestId": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "durationMs": round((perf_counter() - started) * 1000),
                }
            )
        )
        return response

    @app.get("/health/live", include_in_schema=False)
    @app.get("/healthz", include_in_schema=False)
    def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", include_in_schema=False)
    @app.get("/readyz", include_in_schema=False)
    def readiness(request: Request, response: Response) -> dict[str, Any]:
        checks = {"database": False, "redis": not app_settings.jobs_enabled}
        try:
            with request.app.state.store.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            checks["database"] = True
            if app_settings.jobs_enabled:
                from redis import Redis

                with Redis.from_url(
                    app_settings.redis_url, socket_connect_timeout=1, socket_timeout=1
                ) as redis:
                    checks["redis"] = bool(redis.ping())
        except Exception:
            response.status_code = 503
        if not all(checks.values()):
            response.status_code = 503
        return {"status": "ready" if all(checks.values()) else "not_ready", "checks": checks}

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(app_settings.origin_allowlist),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
    )
    app.include_router(router)
    app.include_router(v4_router)
    app.include_router(data_router)
    return app


app = create_app()

__all__ = ["app", "create_app"]
