# IntelliQ v4 — daily portfolio decisions

Status: implementation plan, not a claim of completed functionality.
Date: 20 September 2026.

This is the governing plan for the revised hackathon experience. It supersedes
conflicting product scope, navigation, infrastructure and read-only execution
rules in the v3 handoff and earlier ADRs. Existing numerical and security
invariants remain unless explicitly replaced here. Timecue source code must
not be changed. Production work is listed separately below.

## 1. Product contract

An owner opens IntelliQ in the morning and sees the best evaluated actions to
take now across the business. The default experience is decisions, not projects,
charts, a chatbot, or a multi-step analysis wizard. Detailed evidence, calculations,
project relationships and history are available when the owner asks for them.

### Agreed constraints

- Hackathon first, separate production roadmap. English UI and English demo data.
- Timecue remains the operational source of truth. Import CSV/XLSX through
  IntelliQ, write through existing Timecue APIs, then read the records back.
- No Timecue source changes, direct database writes, invented upstream fields,
  new upstream endpoints, shared service credentials or browser-stored tokens.
- IntelliQ owns planning assumptions, forecasts, extracted knowledge, decisions,
  manual execution steps and learning records, all linked to Timecue IDs.
- Prepare existing Timecue members for the demo. Imports flag unmatched workers
  and dependent assignments for correction before execution; no invitation flow.
- Photos supply knowledge/evidence. Extract automatically; confirmation is
  required before an extracted claim changes planning or Timecue.
- Daily recommendations ready at 06:00 in the organization's timezone. Persist
  the owner's renewable session securely server-side for scheduled access.
- Recommend transfers, sequencing, permitted overtime, material-delay responses
  and weather-driven rescheduling. Evaluate bounded combinations.
- Compare schedule risk, dates, capacity and buffers. No money in the hackathon.
- Select scenario → review exact changes → Apply. Apply supported upstream
  changes and track remaining manual steps in IntelliQ.
- Required interactive 3D relationship explorer: projects, tasks, shared workers,
  dependencies, scenario switching and impact details. Locations/travel belong
  in details; a geographic map is not required.
- Demonstrate actual recorded execution/outcomes influencing a later forecast.
  More analyses alone do not constitute learning or demonstrate accuracy.
- Use Timecue design tokens, existing IntelliQ logos, shadcn controls, Tailwind
  and cn(). Large touch targets, generous spacing, shallow navigation.

### Implementation defaults selected to resolve open details

These are explicit engineering defaults, adjustable without redesigning the system.

- Action window: start now/next working shift, schedule changes over seven days.
  Forecast starts at 28 days and can extend to 56 when completion is censored.
  Missing forecast/weather coverage remains visible; never invent coverage.
- Use upstream priority only if an actual field exists. Otherwise use confirmed
  IntelliQ priority, initially equal across projects; do not infer commercial
  importance from task titles.
- Protected commitments are scenario constraints. If all options violate them,
  present the least harmful evaluated compromises as exceptions requiring explicit
  acknowledgement; never call them compliant. Skill/safety constraints cannot be
  waived by ranking. The owner can still continue the current plan.
- Limit search initially to 24 unique elementary actions and 64 total candidate
  bundles, each containing at most three compatible actions. Include baseline.
  Use 100 paired samples initially; validate finalist stability at 500 when the
  runtime budget permits. Report candidate/sample counts and search limits.
- The first measured learning parameter is transfer setup duration because the
  existing calibration seam supports it. Portfolio outcome auditing is the goal;
  setup is one demonstrable parameter update, not a proxy for decision quality.

## 2. Current implementation and gaps

Source inspection established these reusable foundations:

| Area | Current state | Required change |
|---|---|---|
| Engine | evaluate_portfolio already schedules multiple projects together | Make portfolio runs explicit; bounded combined actions, ranking and trace output |
| API | Analysis accepts projectId; synchronous execution; mode mostly labels result | Portfolio jobs with optional focus; explicit lifecycle and capabilities |
| UI | Project-first SWR pages with separate evidence/decision routes | Decision home, shared detail page and TanStack Query |
| Diagnostics | Engine returns code/entity IDs; UI expects kind/label/detail | Shared typed schema, resolved names and useful explanations |
| Outcomes | Quantiles and censoring returned; much is hidden | Honest missing-value states, distributions, all-project impacts |
| Live seed | 11 tasks/3 workers in last inspected overlay; all latest projects censored | Prepared members and feasible, capacity-checked demo imports |
| Evidence | Lexical extraction; gated LLM adapter | Real provider/configuration, versioned cached extraction and provenance |
| Provider config | OPENAI_API_KEY present; adapter expects LLM_* configuration | Unified validated settings, explicit enabled/provider state |
| Decisions | Frozen record, no operational writeback | Execution receipts, partial application, manual steps and reconciliation |
| Sessions | Process-local upstream token vault | Encrypted durable per-user session reference and refresh coordination |
| Runtime | PostgreSQL, FastAPI, Vite, Turbo, system Caddy | Redis/ARQ worker and daily schedule |

Main references: apps/api/src/app/simulation/engine.py,
apps/api/src/app/api/routes.py, apps/api/src/app/integrations/timecue.py,
apps/api/src/app/intelligence/, apps/api/src/app/persistence/store.py,
apps/frontend/src/pages/AnalysisPage.tsx and WorkspacePage.tsx.

The Timecue checkout exposes assignment replacement with worker_profile_ids and
team_ids, task planned dates, project addresses and default_location coordinates.
Assignment replacement does not itself express dated temporary transfers. Inspect
existing calendar/task semantics before compiling each action. Never turn a
two-hour transfer into an unexplained permanent reassignment.

## 3. Simplest UI and navigation

### Route structure

```text
/login                         Timecue authentication
/                              Today's portfolio decisions
/decisions/:recommendationId    Compare, review, apply; Details below
/analyses/:runId                Full portfolio analysis and 3D explorer
/imports/new                   Template download, upload and preview
/imports/:importId             Import progress, errors and receipts
/knowledge                     Documents and reviewable extracted facts
/settings                      Organization, refresh time, priorities, connection
```

Use a small header: logo links home; organization menu; Add data menu; account
menu. No persistent sidebar, tabbed navbar or separate project/evidence workflow.
On every secondary page provide a visible Back control with a stable home fallback.
The organization picker is only shown when necessary and remembers the selection.
Project filtering lives inside analysis details and never creates independent runs.

### Home: Today

```text
IntelliQ                 Organization ▾     Add data     Account

Today's decisions                         Updated 06:00
Based on current Timecue data              Refresh

Electrical handover threatens Riverside
Recommended: shift one electrician; retain Workshop's protected crew
Riverside: [computed benefit]   Workshop: [computed buffer change]
                                           Compare options →

Needs your confirmation (only when relevant)
Delivery photo: steel arrived at Riverside        Review

Recent decisions / pending manual steps (collapsed)
```

- One primary active portfolio recommendation containing alternative bundles.
  Multiple issues may be summarized, but do not publish independent conflicting
  project approvals. Future independent recommendations need disjoint-resource proof.
- No default history chart, raw diagnostics, simulation counts or task table.
- Use actual computed values; layout examples above are placeholders, not fixture
  numbers to hardcode. State affected projects and trade-offs in plain English.
- Continue-current-plan state: show why it remains preferred and a Details link;
  do not manufacture work or ask to approve the same already-applied action daily.
- Missing-input state: one focused confirmation card with suggested values,
  provenance and an Edit option. Batch related defaults into one review.
- Failed refresh retains the last result labelled with its timestamp and blocks
  stale Apply. Never show old recommendations as freshly computed.

### Compare and review

Desktop: up to three equal-width option cards; mobile: stacked compact cards,
recommended option first. Always include a compact current-plan comparison.
Each option shows action summary, affected people/dates, target benefit, donor
impact, buffer changes and manual-step count. Distinct options may be fewer than
three; never relabel an identical bundle as three different solutions.

Selecting a card opens its review in the same page (Sheet/Dialog on desktop,
full-width accessible sheet on mobile): exact before/after changes grouped by
project, Timecue changes versus manual steps, assumptions that matter and Apply.
No mandatory free-text rationale; prefill a source-backed explanation, editable.
Normal path from home: Compare → select option → Apply (three clicks).
Evidence confirmation and exceptional conflict recovery are separate tasks.

After Apply, show verified changes, pending manual steps and any failure. Do not
optimistically claim success. Manual steps have an explicit Complete action and
optional source attachment; distinguish manager attestation from upstream evidence.

### Details: analysis and 3D explorer

One scrolling page, not tabs inside tabs:

1. Back, run timestamp, freshness and scenario selector.
2. Portfolio comparison: project rows with baseline/selected risk, p50, p10–p90,
   buffer and completion coverage; narrow screens use stacked rows.
3. Interactive graph with adjacent entity inspector (bottom sheet on mobile).
4. Why this changes the plan: bottlenecks, action effects and constraints.
5. Completion distributions and capacity by day using Recharts.
6. Source evidence and assumptions, then prior daily runs and outcome history.

3D design: stable project lanes in depth, dependency order horizontally, task
rows vertically. Worker connections are distinct from directed dependency edges.
Keep positions stable across scenario switches so changes are easy to follow.
Select a node/edge to see names, skills, effort, prerequisites, time window,
baseline versus scenario result, source and explanation. Hover is optional;
everything important is available by click/tap and accessible HTML inspector.
Controls: scenario, project filter, fit view, reset, show affected only, 2D/list.
Lazy-load the canvas; render on demand; no forced orbit, constant motion or giant
force-directed hairball. Aggregate large groups and expand on selection.
The graph shows the operational model and scenario consequences, not every
Monte Carlo sample as a fictional branching causal tree. Trace playback is later.

### Visual rules

Reuse Timecue colors and the existing logo assets; no new branding exercise.
Use 24–32px desktop card padding, 16–20px mobile padding, 24px section gaps and
at least 44px control targets. Inputs have comfortable horizontal padding.
Use semantic shadcn Button, Select, Input, Table, Sheet, Dialog, Alert, Badge,
Accordion, Skeleton and Tooltip. Charts/canvas are custom visualizations inside
those surfaces. Avoid dashboard decoration, KPI walls and unexplained acronyms.
Status is conveyed through text/icons as well as color. Do not rely on hover.

## 4. Packages and module boundaries

Keep React 19, Vite, TypeScript, React Router, Tailwind, shadcn/Radix,
lucide-react, Recharts, clsx and tailwind-merge. Check compatibility and pin
chosen versions at installation; this plan intentionally does not invent pins.

| Package | Role | Decision |
|---|---|---|
| @tanstack/react-query | Server state, polling, invalidation | Replace SWR in migrated routes; remove SWR when migration finishes |
| three, @react-three/fiber, @react-three/drei | Required interactive 3D graph and controls | Lazy-loaded detail bundle; verify React 19 compatible versions |
| elkjs | Stable layered dependency layout | Run layout in a web worker; map project lanes into depth |
| @xyflow/react | Accessible 2D graph alternative | Use same graph DTO; list remains available without WebGL |
| react-hook-form, zod, @hookform/resolvers | Import fixes, focused assumption forms | Add with those forms, not for trivial buttons |
| openapi-typescript | Generated frontend API types | Generate from FastAPI schema; stop handwritten divergent DTOs |
| @tanstack/react-table | Large import previews | Optional; plain shadcn Table is enough for bounded demo data |

Parse CSV/XLSX on the backend using Python csv and openpyxl; avoid two independent
spreadsheet parsers. Templates are static downloads. Photo uploads use a shadcn
styled file input; no upload framework is necessary for the first slice.

Frontend modules: features/decisions, analyses, imports, knowledge, settings;
shared api/generated, query-keys, formatters and UI primitives. Graph renderer,
layout and inspector consume a shared immutable graph model. URL owns selected
run/scenario/project; local React state owns only transient controls.

Backend: retain apps/api. Extract services from routes into portfolio, analyses,
recommendations, execution, imports, knowledge and learning modules. Keep the
simulation engine independent of HTTP, LLMs, database and queue clients.
Worker entry point lives under apps/api/src/app/workers. Add a Turbo worker task;
no second backend or separate business-logic copy. Keep apps/reverse-proxy.

## 5. Ownership and data model

Operational screens use authorized Timecue reads/read-through snapshots with a
visible as-of time. Local projections are caches and analysis inputs, not a
second editable project database. Historical analysis pages deliberately show
the frozen snapshot used for that run, labelled historical.

All tenant-owned records have organization_id; scoped FKs/queries prevent cross-
organization links. Do not assume Timecue's RLS protects IntelliQ. Use explicit
Alembic migrations. Existing history remains readable through a legacy adapter.

| Entity | Essential fields and purpose |
|---|---|
| timecue_connections | user/org binding, encrypted access/refresh material, expiry, refresh generation, revoked_at; never return secrets |
| portfolio_schedules | org, timezone, ready_by=06:00, next_run_at, connection_id, enabled |
| portfolio_snapshots | immutable normalized Timecue payload, resource revisions, read window, source hash, completeness |
| planning_versions | confirmed dependency/effort/calendar/priority/material/transfer assumptions with provenance and effective dates |
| knowledge_documents | org/project scope, object reference, content hash, source revision, MIME, captured_at/known_at, rights/source metadata |
| extraction_runs | document revision, provider/model/prompt/schema versions, status, result, usage, unique cache key |
| evidence_claims | typed assertion/state/value/unit, entity links, exact quote/page/region, source, proposed/confirmed/rejected/superseded |
| engineering_rules | version, applicability, work type, units, threshold/effect, source, approval, validity; global reviewed templates plus org overrides |
| external_context | location, provider, fetched/issued/valid times, route/weather values and coverage, assumptions |
| analysis_runs | snapshot/planning/rule/calibration/context versions, seed, samples, action window, horizon, status/stage, trigger, parent run |
| scenario_results | run, normalized action bundle/hash, feasibility, constraint violations, per-project outcomes, deltas, ranking components |
| recommendations | current run, selected scenario IDs, issue summary, validity, superseded_by, continue/change/needs_input |
| decision_contracts | immutable approved scenario, actor/time, before/after forecast, exact approved changes, assumptions and exceptions |
| execution_steps | decision, ordered command/manual type, target, preconditions, idempotency key, receipt, attempts, state |
| import_batches/rows | file hash/schema version, local row key, mapping, validation, upstream ID/receipt and execution state |
| outcome_observations | decision/task links, event_at/known_at, actual value/unit/source, execution fidelity, comparable/excluded reason |
| calibration_versions | parameter/context, prior, accepted evidence IDs, posterior estimate/uncertainty, count and validity |
| jobs/outbox | durable request/status/progress/attempts, queue dispatch and deduplication |

Separate snapshot identity from its content hash: new fetch timestamps alone do
not imply a changed plan. Freeze all version references for reproducibility.
Normalize frequently queried entities; large simulation arrays/graph artifacts
can use versioned JSONB or object storage, not thousands of redundant row copies.

## 6. Planning, simulation and cause explanations

### Graph and resource semantics

Use a task prerequisite DAG across the portfolio, including explicit cross-project
dependencies where confirmed. Reject cycles with the exact cycle path. Worker,
skill, assignment, reservation, location and evidence links form a separate typed
relationship graph; do not force resource conflicts into an acyclic prerequisite
graph or imply that the graph establishes statistical causality.

### Pipeline

```text
Timecue read → confirm completeness → reuse/extract evidence → readiness
  → freeze snapshot + confirmed assumptions + weather/routes + calibration
  → simulate baseline → inspect bottlenecks → generate bounded action bundles
  → simulate bundles with paired samples → filter/rank → explain → publish
  → owner review/apply → reconcile Timecue/manual steps → observe → calibrate
```

The scheduler is deterministic for fixed inputs and sampled values. Sample
remaining reference person-hours from the existing triangular effort ranges.
Honor skills, crew limits, shifts, reservations, dependencies, material gates,
travel/setup and permitted overtime. No worker can supply overlapping capacity.
Use a fixed seed/common random draws for baseline and candidates. Later weather
sampling must represent correlated site conditions, not independent task coin flips.

Available productive capacity reduces remaining effort. Adding a worker helps
only if skills, crew limits, work front and prerequisites allow it. Record the
capacity delta and downstream consequence. Existing additive productivity is an
explicit modelling assumption; do not invent individual worker quality scores.

Generate transfers between eligible sites, permitted overtime, feasible sequencing
changes, and work shifts around confirmed delivery/weather windows. Expediting a
delivery is a manual request with uncertain effect, never assumed successful just
because it was recommended. A material date override requires confirmation.
Compile candidate action capabilities before ranking so unsupported effects and
manual preconditions are visible. Deduplicate, reject conflicting bundles and
prune dominated options; never add single-action benefits to estimate a bundle.

### Ranking policy v1

Hard feasibility first: skills, maximum crew, working availability, protected
reservations and applicable safety constraints. Rank distinct feasible scenarios
using a versioned policy, not LLM prose or arbitrary hidden weights.

- Fast: minimize priority-project late probability, then late days/p90 finish.
- Balanced: minimize priority-weighted portfolio late probability, then expected
  positive delay, then negative buffer changes, then disruption.
- Safe: minimize worst-project late probability, then worst tail finish/buffer
  exposure, then disruption.
- Disruption tie-break: changed worker-days, overtime hours, transfer overhead,
  then stable scenario ID. All ranking components appear in Details.
- Equal priorities default to weight 1. Confirmed priority tiers map to documented
  weights 1/2/3. No monetary score in v1.
- Risk ties do not automatically hide completion improvements: compare censored
  counts and known lower bounds before declaring a candidate non-beneficial.
  Incomparable outcomes remain labelled, never imputed as zero delay.

### Output and uncertainty

Return per project: deadline, p10/p50/p90 finish when identifiable, delay risk,
expected positive delay when estimable, completion/censoring counts, target buffer,
buffer delta, and capacity by day. Return finish histogram bins including an
explicit unfinished bucket. Retain small representative traces for explanations.
If runs remain unfinished, show “Beyond simulated horizon” and causes, not blank
dashes or horizon-end dates masquerading as completion. Expose covered period.

Diagnostics share one API/UI schema: code, message, severity, scenario IDs,
entity references, affected time interval, blocker references and source/assumption
references. Distinguish ordinary prerequisite waiting from a harmful bottleneck.
Track waiting/resource-shortage hours and frequency across runs where needed;
a diagnostic's presence alone is not an attribution percentage.

Explanations separate observed evidence, scheduler constraints and model sensitivity.
For selected bottlenecks, bounded counterfactual reruns test a changed constraint
under the same samples. Effects can interact; do not sum them into causal claims.
The LLM may verbalize only referenced computed outcomes and validated facts.

## 7. LLM workflow, photos and knowledge

Use an explicit typed state machine with persisted stages for v1. ARQ runs jobs;
the numerical scheduler calculates results. A free-running agent is unnecessary.
LangGraph is a later-compatible orchestration option if durable branching and
interrupts outgrow the small state machine; do not add two competing job systems.

Bounded services/tools: read_portfolio, extract_document, retrieve_confirmed_context,
get_weather, get_travel_matrix, propose_assumptions, propose_candidate_actions,
simulate_scenarios, summarize_results. Each has a Pydantic request/response contract,
tenant scope, allowed IDs, timeout, retry/budget limit and provenance. LLM tool
arguments never select arbitrary URLs, credentials, tenants or execution commands.
Operational execution is a separate service reachable only through owner approval.

Choose one configured multimodal provider for the demo; reuse the user's OpenAI
configuration instead of requiring Gemini solely because it appears in the BMC.
Keep a provider adapter for text/image extraction. Confirm supported model and
structured response capability at implementation. Unify .env loading and key
aliases; report provider health and lexical fallback explicitly.

Photo lifecycle: upload → validate/store → deduplicate by org/content hash → extract
structured claims → review → confirm facts/explicit planning changes → rerun.
Cache key includes content hash and provider/model/prompt/schema versions. Reuse
unchanged extraction; document revisions/corrections create new versions. Confirming
a fact need not imply any particular operational write; show that mapping explicitly.
Reject stale confirmation if the source changed. A photo of delivered material does
not by itself establish installed quantity or task completion.

Context per analysis: compact current portfolio, relevant confirmed claims, rule
versions, recent decisions and their execution/outcomes, plus calibrated parameters.
Retrieve within org/project/time scope; use database filters first. Do not append
the entire history to every prompt or treat earlier LLM explanations as observations.
Keep task/site photos separate from global engineering reference documents/images.
Only curated, attributable reference material enters the shared knowledge base;
private customer images never become shared knowledge. Embeddings are not needed
for the initial bounded corpus.

## 8. Location, travel and weather

Read Timecue coordinates where available. Otherwise geocode the existing address
with a configured provider; ambiguous matches require a one-time correction.
Store analytical geocoding in IntelliQ if no compatible upstream write exists.
OpenStreetMap is geographic data, not a weather or route-time API. Use an explicit
geocoder and route provider; public Nominatim has strict usage limits and is not
an unrestricted autocomplete backend.

Default provider choice for implementation: WeatherAPI.com for hourly forecasts,
and openrouteservice (ORS, using OpenStreetMap data) for directed travel-time and
distance matrices. Google Weather/Routes remain optional adapter replacements,
not additional required accounts. Use existing HTTPX; no frontend provider SDK.

Expected backend configuration (planned; not wired into the current runtime):

```dotenv
WEATHER_PROVIDER=weatherapi
WEATHERAPI_API_KEY=
WEATHER_FORECAST_DAYS=7
ROUTING_PROVIDER=openrouteservice
OPENROUTESERVICE_API_KEY=
ROUTING_PROFILE=driving-car
```

Keys belong in apps/api/.env or deployment secrets, never VITE_* variables or
source control. Obtain a WeatherAPI plan with the needed forecast coverage and
an ORS key with matrix access; verify actual entitlements at startup/integration
check rather than assuming a particular free-tier quota. A shorter forecast is
reported as shorter coverage. API keys are not required for OpenStreetMap itself.

Weather adapter: HTTPS forecast.json by latitude/longitude, hourly temperature,
precipitation, rain probability, wind/gust and other rule-required fields. Normalize
units and use epoch timestamps plus the site timezone to avoid DST ambiguity.
Missing fields remain unavailable, not zero. Refresh before the daily analysis,
reuse the frozen response for every candidate and every Monte Carlo sample.
Start with a one-hour application cache TTL, adjusted to provider update frequency
and terms. Never call the weather service inside the hourly simulation loop.

Routing adapter: POST /v2/matrix/driving-car with distinct site coordinates in
[longitude, latitude] order, requesting durations/distances. Store directed
A→B and B→A values independently; an unreachable/null entry is not zero travel.
ORS estimates must not be labelled live-traffic/departure-specific predictions
unless the selected service explicitly supports that capability. Cache by ordered
coordinates, profile and routing options; start with a 24-hour application TTL
and invalidate on site changes, subject to provider terms. Retrieve directions
geometry only if a later map needs it; the matrix suffices for the demo.

Prefer Timecue coordinates; if geocoding is needed, use ORS geocoding when enabled
on the account or a separately configured service. Persist a confirmed location
match and reuse it, rather than re-geocoding each analysis. Keep provider attribution
in Details. WeatherAPI query-string credentials must be redacted from request/error
logs. Bound concurrency, honor rate-limit responses and retry only transient reads.

Travel consumes worker availability: outbound travel + setup + any required return
reduce productive capacity and can change donor recovery time. Setup remains an
independent company parameter, not part of the route API's estimate. Missing routes
require a confirmed travel assumption. Passenger travel does not establish
heavy-equipment route suitability.

Weather input is time/location-specific forecast coverage. Evaluate work type,
exposure, material/equipment method and a versioned applicable rule. Wind, rain,
temperature and curing windows can restrict availability or productivity.
The BMC's example thresholds and fatigue percentages are not universal validated
rules. Demo rules must be attributable/confirmed and labelled assumptions where
not validated. Beyond provider coverage, disclose the assumption or withhold
weather-dependent recommendations. No silent “weather is fine” fallback.

Live forecast updates can automatically recompute results under already-confirmed
weather rules. Confirmation is required when introducing or changing a planning
rule/assumption, not for each new hourly forecast. V1 uses the provider's hourly
forecast as an explicit scenario input, not as a validated ensemble. Do not treat
chance-of-rain as independent random events for each task/hour. Preserve weather
and routing response versions with the analysis so later changes cannot rewrite
the explanation of an approved decision.

Acceptance examples: an applicable rain restriction delays an exposed task but
does not block an indoor task; wind/gust units are correctly converted; a distant
donor loses more productive time than a nearby donor; unavailable routes prevent
unsupported transfers; expired/limited forecast coverage is visible; provider
failure preserves the last-known timestamp and never produces fresh-looking data.

## 9. Applying scenarios and resolving conflicts

Selection alone writes nothing. Review shows precise planned API changes and manual
steps; Apply approves that immutable bundle. Recheck current access and source
preconditions for all affected projects/workers before dispatch.

Serialize apply operations per organization in v1 with a durable execution record
and short database lock/lease; do not hold a SQL transaction over network calls.
After any applied change, supersede conflicting recommendations and refresh the
whole portfolio. Concurrent owners cannot independently allocate the same worker
from the same base revision. Changed inputs require a refreshed review.

Execution states: pending → checking → applying → applied / partially_applied /
failed / needs_reconciliation. Each step has its own state and upstream receipt.
Manual steps retain pending/confirmed/failed status. Report operational write
success separately from actual physical implementation and measured outcome.

Execution steps also have prerequisites. Apply every supported step whose
preconditions hold; if a supported operation depends on an unfinished manual
step, leave it queued/blocked rather than creating an inconsistent partial plan.
Manual completion resumes only the originally approved operations after renewed
freshness checks. Changed scope requires a new review. The forecast of a fully
implemented scenario is conditional until all required steps are done. A refresh
of a partially applied decision models actual readback plus confirmed operational
facts, not the optimistic intended state. Show its remaining gap explicitly.

Use existing task date/assignment/calendar writes only when semantics match.
Preserve direct workers and team-derived assignments; replacing an effective
worker list can accidentally destroy team assignments. Team membership requires
special handling: do not claim a team member was removed while team assignment
still grants their participation. Mark unrepresentable steps manual.

Timecue lacks a verified multi-resource transaction/CAS contract here. IntelliQ
locking cannot prevent a simultaneous Timecue edit. Read before each step, compare
approved preconditions, apply, then read back. Stop on drift; surface partial
completion. Do not promise atomicity or exactly-once writes. For timeout after a
possibly successful write, reconcile before retrying. Avoid blind retries of
create requests lacking upstream idempotency. Automatic rollback can overwrite
someone else's edits; recovery uses a newly reviewed compensating action.

## 10. CSV/XLSX import

Deliver a workbook with Projects, Tasks, Workers, Skills and Assignments sheets;
CSV uses matching per-entity files uploaded together. Stable external keys link
rows before Timecue IDs exist. Include a separate Planning sheet/file for model
assumptions; preview clearly labels its IntelliQ destination.

Required columns: Projects(externalKey,name,timezone,address), Tasks(externalKey,
projectKey,title,plannedStart,plannedEnd), Workers(externalKey,timecueMemberId,skillKeys),
Skills(externalKey,name), Assignments(taskKey,workerKey). Optional validated fields
depend on the actual upstream contracts. Planning contains effort units/ranges,
prerequisite keys, specialty keys, crew bounds, confirmed target and priority.
Do not equate estimated elapsed minutes with remaining person-hours.

Flow: download example → upload → parse/validate → preview creates/updates/errors
and proposed assumptions → Confirm import → write existing Timecue APIs in dependency
order → read back canonical IDs → publish confirmed planning version → analyze.
One preview confirmation may explicitly approve included planning assumptions.
Unmatched members, ambiguous duplicates, invalid links/units/timezones and cycles
block execution until corrected. Existing updates require explicit matching and
visible before/after values; never silently overwrite similarly named projects.

Import uses a durable ledger with file hash, row keys, upstream mappings and
receipts. Retry resumes reconciled steps, not a second set of projects. Partial
failures remain visible and do not trigger a misleading complete analysis.
Bound file size/row count/decompressed size; reject macros, formulas and external
links for the supplied-template demo. Never evaluate spreadsheet expressions.

Demo assets to deliver: blank template.xlsx, populated portfolio.xlsx, equivalent
CSVs, README, annotated sample delivery/field-note images, and expected import
counts. Prepare 8–10 actual demo members/profiles with adequate specialties; use
3 projects, at least 10 tasks, realistic reservations and 60 worklogs. Include a
useful transfer, a harmful/rejected alternative, material/weather cases and a
manual execution step. Mark synthetic demo documents clearly. Verify all projects
can finish with plausible capacity; do not tune hardcoded risk percentages.

## 11. Daily jobs, sessions and frontend freshness

PostgreSQL owns job and execution truth. Redis/ARQ handles dispatch/retry only.
Use an outbox to close the DB-commit/queue-dispatch gap. Idempotency keys deduplicate
daily jobs by org/local date and other triggers by meaningful source version.
ARQ jobs may run repeatedly; side effects must use the execution/import ledgers.
CPU simulations run in a bounded process executor, not on the async event loop.

Schedule preparatory work early enough to target 06:00 publication; start with
05:50 and measure actual duration. Persist next_run_at in UTC, derive from IANA
timezone and local date, handle DST and restart catch-up once per date. If late,
show “Updating” and the prior timestamp. Do not label an old run Today's result.

Scheduled jobs use the designated owner's encrypted renewable connection, scoped
to that organization. Revalidate identity/membership/permissions at execution,
coordinate refresh rotation across API/worker processes and stop on revocation.
No credentials in job payloads, prompts or logs. Reauthentication is an actionable
home state. User sign-out/disconnect revokes scheduled access for that connection.
The UI explains that enabling daily updates permits server-side refresh.

Triggers: daily, explicit refresh, successful import, confirmed planning change,
applied scenario, confirmed manual implementation and accepted outcome update.
Debounce changes/coalesce pending runs. Confirmation of irrelevant evidence need
not invalidate every forecast. Already-applied actions enter the next baseline.

TanStack Query keys include org/run/scenario. Poll lightweight job status every
2 seconds while running, back off to 5–10 seconds, stop at terminal states and
pause hidden tabs. Home freshness can poll a cheap revision endpoint every 60
seconds while visible. staleTime controls cache freshness, not polling frequency.
Immutable completed runs use long cache lifetimes. Invalidate affected home,
portfolio, evidence and execution queries after mutations. Reading GET endpoints
must not re-extract documents or enqueue LLM calls; move existing read-side effects
into explicit jobs. API checks freshness independently of frontend caching.

## 12. Learning and decision quality

For every approved bundle, preserve the original forecast and record implementation
fidelity, later Timecue events and relevant external changes. Compare predicted
versus actual dates, buffer usage and capacity where observed. An action that was
not implemented is not evidence its forecast failed.

Update only identifiable parameters from comparable observations. Initial example:
observed handover/setup hours updates a versioned setup-time distribution with
prior shrinkage and sample count, then changes a later relevant forecast. Extend
the current fixture-only calibration path to authenticated live observations with
provenance and validation. The owner asked for decision quality; report forecast
error and actual portfolio outcomes alongside this parameter update.

Do not conclude the chosen action was globally optimal: unchosen outcomes are
unobserved. Model counterfactuals remain model estimates. Distinguish one-case
adaptation from evidence of improved predictive accuracy across held-out outcomes.
No automatic worker ranking from hours alone; future productivity calibration needs
completed quantity, work type, skill context and confounders. Reusing a photo or
replaying a job must not count the same observation twice.

## 13. Proposed API contracts

All routes below are IntelliQ routes under /api/organizations/{org}, not Timecue
API additions. Generate shared DTOs from the implemented OpenAPI contract.

| Route | Behavior |
|---|---|
| GET /overview | Current recommendation, freshness, pending confirmations/manual steps |
| POST /analyses | 202 job; optional focusProjectId, trigger, expectedPortfolioRevision |
| GET /jobs/{id} | Status, stage, progress, actionable error; no provider calls |
| GET /analyses and /analyses/{id} | Paginated history and immutable portfolio result |
| GET /analyses/{id}/graph | Typed nodes/edges and scenario impact references |
| GET /analyses/{id}/scenarios/{scenarioId} | Metrics, actions, diagnostics and distributions |
| POST /recommendations/{id}/review | Compile exact execution preview against revision |
| POST /decisions | Freeze approved preview/hash; idempotent queue dispatch |
| GET /decisions/{id} | Frozen contract, execution receipts, observations |
| POST /decisions/{id}/steps/{stepId}/complete | Attested manual completion with provenance |
| POST /decisions/{id}/observations | Record comparable actual outcome |
| POST /imports/preview and /imports/{id}/commit | Validate preview then execute immutable batch |
| GET /imports/{id} | Rows, progress and receipts |
| POST /knowledge/documents | Upload and enqueue extraction |
| GET /knowledge and POST /evidence/{id}/review | Read claims; confirm/reject and explicit changes |
| GET/PUT /settings | Schedule, connection reference, priority and policy versions |

Core response types: PortfolioRun, ScenarioResult, ProjectOutcome, Diagnostic,
GraphNode/GraphEdge, EvidenceClaim, ExecutionPreview/StepReceipt, ImportPreview.
Outcome fields explicitly allow null with a missingReason/censoring explanation.
Actions are discriminated unions, not arbitrary dictionaries supplied by the LLM.
Standardize failures: code, message, entityRefs, retryable, requiredAction.

## 14. Delivery sequence and acceptance gates

Each slice includes API, UI, data contract and proportionate verification. Preserve
existing working modules. Do not split the work into disconnected FE/BE ownership.
Branch convention when a real base/issue exists: jh_feat_<issue>_<slice>.

Delivery audit (2026-09-20): checked items below mean implemented and locally
verified, not that every live acceptance gate passed. Live Timecue import/writeback
gates C/E and the fully connected rehearsal G still require an authorized account;
weather/routing live verification requires provider keys. No external deployment
has been performed.

### A — Contracts and truthful simulation output

- [x] Generate shared types; fix diagnostics and censoring UI.
- [x] Expose portfolio run semantics and all-project impacts; legacy history adapter.
- [x] Add bounded combinations/ranking and capacity/bottleneck traces.
- [x] Validate demo workforce completeness and useful attainable outcomes (synthetic fixture).
- Gate: fixed inputs/seed reproduce results; resource conservation, donor harm and
  no-benefit actions are explained; no fabricated metrics.

### B — Jobs, daily access and decision home

- [x] Durable encrypted connection with coordinated refresh and revocation.
- [x] Redis/ARQ, outbox, bounded CPU execution, scheduled 06:00 publication.
- [x] Decision-first home, compare/review, continue-current-plan and error states.
- [x] TanStack Query migration for these routes and cheap progress polling.
- Gate: a restart resumes/reconciles jobs; daily duplication and stale publication
  are prevented; owner can compare in the agreed three-click flow.

### C — Import and confirmed knowledge

- [x] Workbook/CSV schema and demo files with explicit member-mapping validation.
- [ ] Map supplied demo worker rows to actual authorized Timecue organization members.
- [x] Preview, correction, idempotent execution ledger and readback.
- [x] Photo storage, real LLM extraction, cached versioned evidence and confirmation.
- Gate: import creates actual Timecue records; repeating it creates no duplicates;
  unconfirmed photos never change a forecast; real provider boundary demonstrated.

### D — Context and explanations

- [x] Location projection, directed travel context and one weather adapter.
- [x] Applicable versioned work-type rules, material gates and coverage states.
- [x] Structured explanation service referencing simulation/source facts.
- Gate: a dated material/weather change affects the correct tasks and shared
  portfolio outcomes, with visible assumption/provider coverage.

### E — Apply and reconcile

- [x] Capability compiler for existing Timecue routes and manual remainder.
- [x] Immutable review hash, freshness checks, org serialization and receipts.
- [x] Retry/reconciliation, partial application and manual completion UI.
- Gate: apply changes are visible in Timecue and next baseline; competing stale
  decisions fail clearly; no temporary-transfer semantic substitution.

### F — Required 3D details

- [x] Shared typed graph, stable layout, 3D renderer, inspector and scenario switch.
- [x] 2D/list fallback, mobile touch/keyboard access and lazy loading.
- [x] Outcome charts, waiting/capacity explanations, history and source links.
- Gate: owner can identify the shared worker, affected dependency chain and donor
  buffer change directly from the graph. Required for demo acceptance.

### G — Actual outcome and rehearsal

- [x] Outcome capture, execution fidelity and calibration with provenance (fixture browser and API tests).
- [x] Before/after parameter versions and later forecast comparison.
- [ ] Rehearse import → evidence confirmation → daily recommendations → compare →
  3D explanation → Apply → Timecue readback → manual step → outcome → new forecast.
- Gate: preserve the original forecast; show a measured/attested observation and
  resulting parameter change without claiming demonstrated global optimality.

Validation priorities: tiny hand-computable schedules; fixed-seed reproducibility;
paired samples; skill/crew/time conservation; dependency cycles; censored outcomes;
cross-project conflict; stale approval; team assignment preservation; partial writes;
ambiguous timeouts; import duplication; tenant boundaries; extraction cache and
confirmation; session expiry; DST scheduling; actual-data provenance; mobile 3D
fallback. Use focused checks as slices land; full suite/browser rehearsal at the
release gate rather than spending early work on cosmetic test expansion.

Known existing commands: pnpm --filter api lint, pnpm --filter frontend lint,
pnpm build, pnpm --filter api test and pnpm test:e2e. New worker/scheduler commands
must be added to Turbo/pnpm dev and documented when implemented.

## 15. Separate production roadmap

- Monetary comparisons using explicit costs/contract terms, validated report
  templates and legal review before making due-diligence/protection claims.
- Larger search/optimization, convergence monitoring, uncertainty validation,
  correlated weather/delivery scenarios and richer crew productivity models.
- Equipment only when real capability, reservation and movement data exist.
- Google Sheets/other connectors, arbitrary spreadsheet mapping and onboarding.
- Durable authorized background access at scale, operational monitoring, backups,
  retention, provider budgets, disaster recovery and multi-user deployment proof.
- Source-backed engineering library with qualified review and version governance.
- Retrieval/embeddings where corpus size warrants them; LangGraph if orchestration
  complexity warrants it; neither required merely to label the product AI.
- Statistical calibration evaluation, quantity-based productivity and delivery
  reliability learning; no unqualified claims that every new decision improves it.
- Geographic maps, sampled-trajectory playback, notifications and billing.

Commercial BMC pricing and acquisition hypotheses are product research, not
implementation requirements for this demo. This plan makes no new timing promise;
estimate remaining time after slice A and preserve an integration/rehearsal buffer.

## 16. Research and package references

Official sources checked during planning; proposed UX is IntelliQ's interpretation.

- [shadcn components](https://ui.shadcn.com/docs/components): reusable control vocabulary.
- [TanStack Query defaults](https://tanstack.com/query/latest/docs/framework/react/guides/important-defaults): cache freshness and polling are separate controls.
- [React Flow](https://reactflow.dev/learn): 2D node/edge interaction and accessibility.
- [ELK.js](https://github.com/kieler/elkjs): layered layout independent of rendering.
- [Drei](https://github.com/pmndrs/drei): helpers for React Three Fiber.
- [Fieldwire tasks](https://help.fieldwire.com/hc/en-us/articles/360003458332-Introduction-to-Tasks): reference for field-oriented, contextual work detail; adapt rather than copy its navigation.
- [ARQ](https://arq-docs.helpmanual.io/): repeated execution requires idempotent jobs.
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence): checkpointing option if the workflow later needs it.
- [Nominatim policy](https://operations.osmfoundation.org/policies/nominatim/): public geocoding service constraints.
- [Google Weather](https://developers.google.com/maps/documentation/weather/overview) and [Routes matrix](https://developers.google.com/maps/documentation/routes/compute_route_matrix): candidate context integrations, subject to configured access and terms.
- [WeatherAPI documentation](https://www.weatherapi.com/docs/): default hourly forecast adapter; coverage depends on account entitlement.
- [ORS API examples](https://openrouteservice.org/dev/) and [restrictions](https://openrouteservice.org/restrictions/): default OSM-based matrix adapter, authorization and request limits.

## 17. Implementation handoff details

### Dependency and parallel-work boundaries

Slice A owns shared contracts. Once those contracts are committed, B (jobs/home),
C (imports/knowledge), D (external context) and F (graph renderer) can develop
against the same versioned fixtures. E depends on A's action semantics, B's durable
execution infrastructure and a verified Timecue capability matrix. G depends on
real execution/readback. Integration of F with real outputs is required even if
the renderer was developed in parallel. No agent may redefine shared DTOs alone.

Contract owner: OpenAPI/Pydantic schemas, fixture payloads, migration ordering and
generated frontend types. Engine owner: pure simulation/ranking/trace outputs.
Integration owner: upstream capability matrix, imports and execution receipts.
UI owner: home/comparison/details and accessibility. Intelligence owner: bounded
extraction, context, explanations and observation eligibility. Assign slices only
to available agents; these are ownership boundaries, not a requirement for five
simultaneous agents or a particular model/provider.

### Minimum capability matrix before execution coding

For each candidate operation, record existing Timecue method/path, required
permission, exact body/response, intended time semantics, readback strategy,
whether replacement is destructive, team expansion behavior, retry/reconciliation
method and the manual fallback. Cover task assignment replacement, planned date
changes, calendar events, project/task creation, worker skills and worklogs.
Do not expose Apply for an unverified command mapping; keep the action's manual
step instead. No general-purpose LLM-selected upstream request tool.

### Typed action envelope

Each action includes id, type, entityRefs, startsAt/endsAt with timezone,
preconditions, assumptionRefs, capability (api/manual/mixed), and predicted
effects referenced by result ID. Transfer adds workerIds, from/to project/task
IDs, outbound/setup/return durations and displaced assignments. Overtime adds
explicit permitted slots and cap. Resequence adds order/earliest-start changes.
Material response references confirmed availability or a conditional manual
request. Weather response references rule/context IDs and shifted work windows.

Graph DTO nodes have stable typed IDs (project/task/worker/material), Timecue or
IntelliQ source reference, label, project grouping, and per-scenario outcome refs.
Edges declare prerequisite, assignment, shared_resource or material_requirement.
The graph can resolve evidence/rules in the inspector without drawing every
document as a node. 3D/2D/list render the same DTO and selected scenario.

### Metric definitions for the UI

- Delay risk: fraction of trajectories finishing after the confirmed target;
  unfinished trajectories known to have passed the target count as late.
  If censoring prevents classification, expose unavailable/bounds explicitly.
- p50/p90: finish-date quantiles, with coverage/identifiability rules. A single
  conservative nullable policy is acceptable initially; explain it consistently.
- Expected positive delay: mean max(0, finish minus target) in calendar days when
  estimable; never average only finished runs and call it the unconditional mean.
- Target buffer: confirmed target minus median finish, in calendar days; negative
  means modelled lateness. Buffer change is scenario minus baseline buffer.
  Distinguish this from formal task float/critical-path slack.
- Capacity: available eligible person-hours versus used person-hours by local
  day; show transfer/setup/overtime separately. Calendar time is not effort.
- Observed finish error: actual finish minus the frozen predicted median, with
  forecast interval coverage and the number of comparable observations.

### Initial operational bounds

Use configurable demo limits: 5 active projects, 80 workers, 200 unfinished tasks,
10 MB per image, 20 MB per workbook, 5,000 total import rows. Validate limits at
entry and show a useful error; do not silently truncate a portfolio and omit donor
commitments. Bound decompression independently. Benchmark candidate/sample limits
on the demo machine before choosing job timeouts or claiming latency guarantees.

Document provider decisions in environment examples without secrets. The demo
requires an available multimodal model, weather source and route source; credentials
and permitted service access are deployment prerequisites. If unavailable, show
confirmed manual context or clearly synthetic provider fixtures and explicitly
record that live provider acceptance remains incomplete.

### Demo truthfulness and scope control

Actual outcome means an observed event or explicitly manager-attested event; a
fabricated demo result cannot be labelled a measured live outcome. Sample import
files and demonstration photos can be synthetic, but their provenance stays visible.
Use a real observed setup/task event when demonstrating the feedback loop; if there
is insufficient real-world elapsed time, distinguish the measured parameter update
from still-pending project completion and decision-quality evaluation.

All accepted capabilities remain in demo scope, including 3D. If available time
becomes insufficient, report the exact incomplete gate and propose an explicit
scope decision; do not silently replace live writeback, providers or actual-outcome
learning with mocked success. Avoid production roadmap work while demo gates remain.
