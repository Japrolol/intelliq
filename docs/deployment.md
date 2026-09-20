# IntelliQ deployment and operations

This runbook covers the reproducible API, worker, web, and reverse-proxy
containers. It does not authorize or perform a deployment. CI validates the
compose graph and builds both application images; live-provider, authenticated
E2E, and production behavior still require a separately recorded check.

## Environment files

Host development loads API settings from `apps/api/.env`. Copy
`apps/api/.env.example` there and keep credentials out of git. The root
`.env.example` is only for local Compose PostgreSQL/Redis defaults and points
developers to the API env file.

Production uses an untracked `.env.deploy` at the repository root, or the file
named by `INTELLIQ_DEPLOY_ENV`. At minimum, provide:

- `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD` for the database.
- `DATABASE_URL` using the Compose service name `postgres` and port `5432`.
- `REDIS_URL`, normally `redis://redis:6379/0` on the private Compose network.
- `DATA_MODE=live` and the existing `TIMECUE_API_URL`.
- `JOBS_ENABLED=true` and a valid `SESSION_ENCRYPTION_KEY`.
- `SIMULATION_PROCESS_ENABLED=true` to isolate the pure CPU engine in the
  bounded single-process executor, with `SIMULATION_TIMEOUT_SECONDS=180` (the
  maximum is 480 seconds).
- `ALLOWED_ORIGINS` and `APP_DOMAIN` for the public web origin.

Provider keys (`WEATHERAPI_API_KEY`, `OPENROUTESERVICE_API_KEY`, and the LLM
settings) are optional. Missing providers remain visible in analysis status;
they are not replaced by fixture data. Use `COOKIE_SECURE=true` when HTTPS is
enabled. Generate the session key using the backend environment and store the
result in the deployment secret store, never in a committed file.

## Local development

From the repository root:

    pnpm install --frozen-lockfile
    (cd apps/api && uv sync --extra test)
    pnpm db:up
    pnpm dev

`pnpm dev` preserves the existing pnpm/Turbo/Caddy workflow and starts the
local PostgreSQL and Redis services first. The worker package is available as
`pnpm worker`; it changes into `apps/api`, so it loads `apps/api/.env` and
uses `uv run python -m arq ...`. The API package similarly uses module-form
commands for Uvicorn, Alembic, ARQ, and pytest so a system executable cannot
silently win over the uv-managed Python environment.

The workspace Caddy process uses the installed system binary on port 8082 with
its admin listener disabled. It does not reload or replace the system-wide
Caddy daemon serving ports 80/443 or its admin endpoint. IntelliQ FastAPI
listens on port 8002 so a local Timecue Caddy can keep `/api` on port 8000.

Useful commands:

    pnpm db:current
    pnpm db:migrate
    pnpm worker:test
    pnpm lint
    pnpm build
    pnpm test

The local Redis service is bound to `127.0.0.1:6381` and PostgreSQL to
`127.0.0.1:5434`. Jobs remain disabled by default in the example API env;
enable them only with a durable session encryption key. The API defaults to the
bounded simulation process for live or queued analyses; set
`SIMULATION_PROCESS_ENABLED=false` only for fixture-style direct execution.
`SIMULATION_TIMEOUT_SECONDS` bounds the child calculation and tears down its
pool on timeout. The worker is bounded to one ARQ job and a 600-second job timeout because the shared analysis may
perform several provider calls. Each claimed job uses a 660-second durable lease
renewed every 120 seconds and releases it only after the terminal record write.

## Production compose workflow

Validate interpolation without starting anything:

    INTELLIQ_DEPLOY_ENV=/secure/path/intelliq.env scripts/deploy.sh --check

Apply the migration and start the stack:

    INTELLIQ_DEPLOY_ENV=/secure/path/intelliq.env scripts/deploy.sh

The script checks the complete production compose file, waits for PostgreSQL
and Redis readiness, runs the one-shot Alembic migration container, then starts
API, worker, web, and Caddy with dependency health gates. It never prints the
contents of the env file. Do not run it against a shared database without the
normal backup and change-approval process.

## Readiness and graceful operation

- API liveness: `/health/live` (also `/healthz`).
- API readiness: `/health/ready` (also `/readyz`), including the database and
  optional Redis check.
- Web readiness: `/healthz` on the nginx container.
- Public routing: Caddy serves the web app and proxies `/api/*` to the API.

The API and worker share the same application lifespan, migrations, durable
token vault, PostgreSQL records, and Redis queue settings. ARQ delivery is
at-least-once: durable job and outbox records, organization-local schedules,
portable database leases, and idempotency keys provide restart recovery. The
worker revalidates the session's organization membership and required read
permissions before executing a queued analysis.

Stop or restart services through Compose so the configured grace periods are
honored. Preserve the PostgreSQL and Redis volumes during routine restarts.
Before schema changes, take a PostgreSQL backup and retain the migration
revision and application image together; do not use volume deletion or an
unknown `alembic stamp` as a rollback strategy.

## CI evidence boundary

`.github/workflows/ci.yml` runs backend Ruff, backend pytest through
`python -m pytest`, frontend type/build checks, Compose interpolation checks,
and API/web image builds. These checks do not prove live Timecue access, live
weather/routes/LLM availability, authenticated browser E2E, or a production
deployment.

The focused local worker smoke separately exercised an isolated PostgreSQL
database and Redis DB 15 with the API enqueue, ARQ worker, and a completed
100-sample/two-strategy analysis using providers disabled. That evidence is
not a live Timecue acceptance test and must not be presented as one.
