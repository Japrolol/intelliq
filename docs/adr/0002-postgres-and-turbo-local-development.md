# ADR 0002: Turbo workspace and local PostgreSQL

Status: accepted for the hackathon build.

IntelliQ keeps the React app in `apps/frontend/` and the FastAPI app in
`apps/api/` with its Python package under `apps/api/src/app/`.
The repository root uses pnpm workspaces and Turborepo to run their existing
build, test, lint and development commands together. Turbo is task
orchestration; Python packages remain managed by `uv`.

The local application database is PostgreSQL, run as the only service in
Docker Compose. Both app processes run on the host. SQLAlchemy remains the
persistence layer, with a Psycopg driver for PostgreSQL. In-memory SQLite is
retained only for small, isolated tests; it is not an automatic fallback when
PostgreSQL is unavailable. Existing local SQLite demo files are preserved but
not silently imported into PostgreSQL.

For this single-instance hackathon app, synchronous, bounded analysis and
evidence extraction do not require Redis or ARQ. A durable queue becomes
worthwhile when inference must survive process restarts, run across replicas,
or be retried independently of HTTP requests. PostgreSQL also needs a real
schema migration workflow before evolving a deployed database; the initial
demo schema bootstrap is not a substitute for migrations.
