# IntelliQ

IntelliQ is an independent construction decision-support platform integrated
with Timecue. It helps an owner answer **what should we do today across the
portfolio?** by combining authorized operational data, explicit planning
assumptions, source evidence, and bounded what-if simulation.

It is a hackathon prototype, not an autonomous scheduler. A manager confirms
missing inputs, compares the unchanged plan with a few feasible combined
actions, reviews the exact proposed changes, and then applies only the part
that the current Timecue integration can represent. The rest remains a tracked
manual step.

IntelliQ owns the simulation engine, planning overlay, knowledge, decisions,
execution records, and outcome learning. Timecue is the current operational
connector and source for the data it exposes. CSV/XLSX imports enter through
IntelliQ and resolve supported rows against the authorized Timecue
organization. No other operational connector is implemented.

The governing scope is [`docs/implementation-plan-v4.md`](docs/implementation-plan-v4.md).
The short system description is [`docs/system-flow.md`](docs/system-flow.md).

## The decision loop

```text
Timecue / CSV / XLSX
  -> authorized snapshot + IntelliQ planning overlay
  -> evidence and readiness checks
  -> baseline + bounded action bundles
  -> owner review of exact changes
  -> supported Timecue API writes + manual steps
  -> readback and outcome observation
  -> narrow transfer-setup calibration
```

1. IntelliQ reads the signed-in user's authorized Timecue organization through
   existing routes: projects, tasks, effective assignments, workers,
   specialties, reservations, calendar context, and worklogs. Target dates,
   remaining effort ranges, dependencies, calendars, locations, and transfer
   friction are IntelliQ planning data when Timecue does not expose them.
2. Missing or unconfirmed critical inputs produce `needs_inputs`. That is a
   readiness result, not an ARQ or Redis queue failure. A live organization can
   also contain legacy projects with no IntelliQ overlay. Those projects must
   be prepared or confirmed before they can contribute a forecast.
3. Notes, photos, and documents become source-linked evidence with quotes,
   timestamps, and limitations. Evidence or knowledge never silently changes
   numerical planning inputs or approves an operational write.
4. The simulator runs the unchanged plan and candidate action bundles over the
   whole relevant portfolio. It samples remaining effort from declared
   triangular ranges, uses common paired samples for baseline and alternatives,
   and preserves unfinished results as censored rather than pretending they
   finished at the horizon.
5. The owner selects an evaluated option, reviews the frozen before and after
   changes, and chooses Apply. Supported Timecue operations are read back and
   reported as verified, partial, or failed. Temporary transfers, travel and
   setup assumptions, delivery requests, weather responses, and other
   unsupported semantics remain manual. A timed transfer is never disguised as
   a permanent reassignment.
6. Later observations are kept separate from the original forecast. The
   current learning path updates only a matching worker/from-site/to-site
   transfer `setupHours` prior from confirmed, comparable observed records. It
   does not train on generated prose, infer general worker performance, or prove
   that a decision was globally optimal.

## Architecture

| Layer | Responsibility |
| --- | --- |
| React DOM and Vite | Decision home, setup, analysis details, evidence and knowledge, review and apply, imports, and execution history. |
| FastAPI backend | Per-user session bridge, Timecue connector, tenant-scoped planning overlay, readiness, evidence, analysis, execution, and outcome APIs. |
| PostgreSQL and Alembic | IntelliQ snapshots, planning assumptions, knowledge metadata, decisions, execution receipts, jobs, and observations. This is separate from Timecue's database. |
| Redis and ARQ | Durable dispatch, retries, daily analysis scheduling, and progress when `JOBS_ENABLED=true`. PostgreSQL remains workflow truth. |
| RustFS | Local private S3-compatible storage for uploaded knowledge files shared by API and worker. |
| Pure Python simulation engine | Fixed-seed hourly scheduling and bounded Monte Carlo comparison, isolated from HTTP, providers, and persistence. |
| Host Caddy | Local development proxy for the frontend and `/api/*`. It does not modify a system Caddy daemon. |

Timecue remains the operational source for the current connector. IntelliQ uses
the existing authorized API surface and does not modify Timecue source code, add
upstream routes, write directly to the Timecue database, or use a shared API
key. Live sessions are bridged server-side and scoped per user and
organization. A live upstream failure stays visible instead of falling back to
fixtures.

## Local requirements

- Docker Desktop or Docker Compose.
- Node.js 22-compatible tooling and `pnpm@10.12.4`, the workspace package-manager version.
- Python 3.12+ and `uv` for the FastAPI app and ARQ worker.
- The `caddy` binary on `PATH` for the host-run local proxy used by `pnpm dev`.

Keep local configuration in ignored environment files. From the repository
root, create the two files if they do not exist:

```sh
cp .env.example .env
cp apps/api/.env.example apps/api/.env
```

On a first run, install the locked JavaScript and Python dependencies before
starting the app:

```sh
pnpm install --frozen-lockfile
(cd apps/api && uv sync)
```

The example API environment starts in `DATA_MODE=fixture` with jobs disabled.
For live Timecue, set `DATA_MODE=live` and point `TIMECUE_API_URL` at the
separate local Timecue API, normally `http://127.0.0.1:8000`. Keep IntelliQ's
`DATABASE_URL` pointed at IntelliQ PostgreSQL. Put provider keys and session
secrets only in the environment, including `LLM_API_KEY` or `OPENAI_API_KEY`,
`WEATHERAPI_API_KEY`, `OPENROUTESERVICE_API_KEY`, `SESSION_ENCRYPTION_KEY`, and
S3 credentials as applicable. Never paste passwords, API keys, cookies, or
tokens into this README, commits, or chat.

## Run locally

The all-in-one command starts PostgreSQL, Redis, RustFS, the FastAPI API, the
ARQ worker, the Vite frontend, and the workspace Caddy process:

```sh
pnpm dev
```

If infrastructure should be managed separately, these are the repository's
actual scripts:

```sh
pnpm db:up
pnpm db:status
pnpm storage:up
pnpm storage:status
pnpm db:current
pnpm db:migrate
pnpm dev:apps
```

`pnpm dev` runs the Alembic bootstrap during API startup. `pnpm db:up` starts
only PostgreSQL and Redis. `pnpm storage:up` starts RustFS. `pnpm db:down`
stops PostgreSQL and Redis without removing their named volumes. Exiting
`pnpm dev` stops all three local Compose services started by that command.

Open the proxied workspace at `http://intelliq.localhost:8082`. Direct local
endpoints are:

| Service | Address |
| --- | --- |
| Caddy workspace | `http://intelliq.localhost:8082` |
| Vite frontend | `http://127.0.0.1:5173` |
| FastAPI | `http://127.0.0.1:8002` |
| FastAPI readiness | `http://127.0.0.1:8002/health/ready` |
| Local Timecue API for live mode | `http://127.0.0.1:8000` |
| PostgreSQL | `127.0.0.1:5434` |
| Redis | `127.0.0.1:6381` |
| RustFS S3 API | `http://127.0.0.1:19000` |
| RustFS console | `http://127.0.0.1:19001` |

The readiness check above is the direct FastAPI `GET /health/ready` route. The
same handler is also available at `/readyz`; neither route is under the
frontend proxy's `/api/*` prefix.

The proxy sends `/api/*` to port 8002 and the remaining workspace traffic to
Vite. RustFS uses the local credentials from the environment examples only;
they are not deployment credentials.

## Seed and demo data

Fixture mode is the safest repeatable rehearsal: it uses the explicit
synthetic portfolio in [`fixtures/portfolio-demo.json`](fixtures/portfolio-demo.json)
and accepts any non-empty fixture login values. It is not live Timecue data and
does not prove provider, authenticated browser, customer, or production
behavior.

The optional Timecue seed runs through existing local Timecue API routes and
writes model-only assumptions to IntelliQ's PostgreSQL overlay:

```sh
pnpm seed:demo:reset
```

That wipes local IntelliQ and Timecue Compose volumes, migrates both databases,
loads Timecue's light fixture (`owner@example.com` / Northstar Renovations),
then seeds the Warsaw demo through existing Timecue APIs. Set `TIMECUE_REPO` if
the Timecue checkout is not `~/RustroverProjects/blueprint`. The script does
not start IntelliQ; run `pnpm dev` afterwards and keep Timecue's API on
`127.0.0.1:8000`.

A narrower reseed against an already-running Timecue API:

```sh
pnpm seed:timecue --dry-run
pnpm seed:timecue
```

The dry run does not log in or write. A real run requires a running local
Timecue API, an authorized account, and a password supplied through the hidden
prompt or `TIMECUE_SEED_PASSWORD`; `TIMECUE_SEED_EMAIL` may provide the email.
Use `--organization-id <uuid>` to target a known organization, and use
`--use-active-session` only with a matching live IntelliQ session. The seed is
local-first and non-destructive, but it does not create accounts or invite
members. Do not assume that a one-user organization is a fully staffed demo:
the useful multi-project scenario needs enough existing members, worker
profiles, skills, assignments, and valid location and overlay data. A small or
partly prepared organization may seed only a reduced setup or remain
`needs_inputs`.

The current flags and source behavior are in
[`apps/api/scripts/seed_timecue_demo.py`](apps/api/scripts/seed_timecue_demo.py);
the rehearsal guide is [`docs/demo-runbook.md`](docs/demo-runbook.md).

## Simulation, evidence, and guardrails

The engine compares whole-plan scenarios, not independent action benefits. The
committed API example uses `SIMULATION_DEFAULT_SAMPLES=100` and
`SIMULATION_MAX_SAMPLES=500`. Both are configurable through those
`SIMULATION_*` environment variables. This checkout's ignored local API
environment currently uses 20 samples (and a 50-sample maximum), so 100/500
are not guaranteed at runtime. Candidate generation is bounded to at most 24
elementary actions and at most three compatible actions per bundle; the
baseline is always included.
Results expose sample counts, ranking inputs, project impacts, capacity, and
censoring instead of hiding uncertainty behind a single score.

Feasibility is checked before ranking. The scheduler honors declared required
skills, effective assignments and project eligibility, active workers, working
days and shifts, crew minimums and maximums, hard reservations, prerequisites
and dependency cycles, material gates, workability constraints, transfer
eligibility and friction, permitted overtime, and configured donor and
protected-project guardrails. These are declared planning constraints, not
proof of site safety or causal attribution.

Optional external context is bounded too. WeatherAPI provides hourly forecasts,
but they affect modeled workability only for tasks with explicit confirmed
`weatherRules`; a forecast alone does not create a weather constraint.
OpenRouteService provides a directed duration matrix for requested project
pairs. IntelliQ uses those legs with configured travel, setup, and return
assumptions. They are not live traffic predictions.

Evidence and LLM features are optional:

- Local lexical extraction is the default. It returns bounded, source-linked
  reported signals; it does not invent probabilities or task links.
- An OpenAI-compatible provider can be enabled through environment settings for
  document and image evidence extraction, up to three sequencing hypotheses,
  and grounded explanation prose. Provider output is schema-validated and can
  only reference supplied facts and entity IDs. Deterministic explanations
  remain the fallback when consent, configuration, or the provider is
  unavailable.
- Analysis retrieval currently ranks accepted knowledge by linked entities and
  topic keyword overlap. The separate knowledge graph can use stored embeddings
  for bounded semantic links and search when explicitly configured. That does
  not make the analysis retrieval path a vector database or establish
  causation.
- Validated uploaded knowledge is accepted for retrieval automatically, but
  automatic acceptance is not human verification. It never authorizes a
  Timecue write or silently changes numerical planning.

## Execution boundary

Selecting an option is read-only. Review freezes the scenario, assumptions,
before and after forecast, and exact requested changes. Apply then rechecks
access, freshness, and preconditions, calls only supported existing Timecue
routes, and reads the affected records back. The UI distinguishes verified,
partial, failed, stale, and reconciliation-required states.

Operations that Timecue cannot represent faithfully, especially timed worker
transfers, travel and setup, delivery requests, weather responses, and some
re-sequencing, remain explicit manual steps. IntelliQ never replaces them with
a misleading permanent assignment or claims a physical action happened.
Fixture receipts explicitly state that no upstream write was attempted.

## Limitations and production roadmap

This build is synthetic-first and bounded. It has no monetary optimizer, no
claim of globally optimal schedules, no production deployment, no guaranteed
live provider coverage, and no atomic multi-resource transaction with Timecue.
Forecasts depend on confirmed or visibly estimated inputs. Incomplete data can
remain censored or return `needs_inputs`. A passing local fixture run is not
evidence of live account access, provider correctness, deployment health, or
customer outcomes.

The next production work is to rehearse and harden the live import and readback
path with a prepared organization, durable secret and session operations,
observability and backups; validate forecast quality and broader outcome
calibration; expand supported Timecue execution semantics; and add larger,
validated search and provider and context coverage only where the evidence and
operational data justify it.

For live-account checks, see
[`docs/live-timecue-verification.md`](docs/live-timecue-verification.md). For
the upstream capability boundary, see
[`docs/v4-timecue-capabilities.md`](docs/v4-timecue-capabilities.md). Deployment
notes are in [`docs/deployment.md`](docs/deployment.md).
