# AGENTS.md - Construction Intelligence

## IntelliQ Overrides — Read First

This repository is IntelliQ, a hackathon decision layer that consumes authorized
Timecue data. The IntelliQ-specific rules in this section take precedence over
Blueprint-specific stack, path, and command examples copied below.

### Product and delivery boundary

- Follow `docs/implementation-plan-v4.md` as the governing revised plan. It
  supersedes conflicting v3 scope and inherited Blueprint product instructions.
- Build the decision-first daily portfolio flow: Timecue data/import → confirmed
  assumptions/evidence → portfolio scenarios → owner review → supported Timecue
  writes plus tracked manual steps → actual observations and later forecasts.
- Latest owner direction supersedes the earlier 3D requirement: Decisions use
  a readable 2D what-if option tree built from evaluated action bundles and
  actual outcomes, with real worker progress while simulation runs. The people/
  project relationship network is not the primary decision view. Home shows
  distinct published decisions directly, with no comparison-page gate.
  Home remains simple, with no persistent sidebar or analysis wizard.
- Do not modify Timecue source code. Adapt assumptions/projections in IntelliQ
  when existing upstream APIs cannot represent an operation.
- No autonomous operational writeback: selection, exact-change review and Apply
  authorize a frozen bundle. Report verified, partial and manual execution honestly.
- Redis/ARQ and TanStack Query are planned for the daily workflow. Avoid unrelated
  CRUD, chatbot-first UI, monetary estimates, embeddings or production scope creep.
- Preserve validated numerical and evidence invariants from the v3 handoff.
  Do not invent upstream fields, permissions, results or causal claims.

### Timecue integration and authentication

- IntelliQ reads and performs approved writes through existing Timecue APIs;
  no new Timecue/Blueprint routes or source modifications are allowed.
- Use the signed-in user's existing Timecue authentication session established by
  the login flow. The IntelliQ backend may call existing authorized Timecue
  routes on that user's behalf, but must never use a shared API key, copied
  signing secret, local password store, or credentials in browser storage.
- If IntelliQ runs on a separate origin, bridge the session server-side with
  host-only HttpOnly cookies or an equivalent per-user server-side session. Do not
  assume cross-origin browser cookies will provide SSO.
- Keep upstream access per user and per request. Never share a mutable HTTP
  client's cookie jar between users; refresh once on an upstream 401, retry once,
  and clear the local session after refresh/logout failure.
- Aggregate the existing Timecue projects, tasks, assignments, workers,
  specialties, reservations, and worklogs inside IntelliQ's adapter. Complete
  pagination and effective assignments before simulation. A new upstream
  `intelliq/snapshot` route is out of scope.
- Re-check current Timecue authentication, route contracts, verified-account
  behavior, and organization permissions against the live checkout before coding.
  A failed live request must remain a visible integration failure; never silently
  switch to fixture data.
- IntelliQ's own internal API is allowed for its React frontend, local planning
  data, analyses, decisions, and job status. “No new API” means no new upstream
  Timecue API surface, not that browser code should bypass a secure backend.

### IntelliQ implementation stack and paths

- Backend: Python 3.12+, FastAPI, Pydantic/settings, HTTPX, SQLAlchemy with
  PostgreSQL/Psycopg for runtime persistence, pytest, Ruff, and the bounded
  simulation engine. SQLite is allowed only when a test explicitly requests
  isolated in-memory storage.
- Frontend: React DOM, TypeScript, Vite, React Router, shadcn, Tailwind and cn().
  Migrate existing SWR routes to TanStack Query in the planned slices; do not
  maintain duplicate caches for the same resource. Keep the Turborepo workspace;
  do not introduce inherited Expo or React Native structure here.
- Use `apps/api/src/app`, `apps/api/tests`, `apps/frontend/src`, `fixtures`,
  and `docs` unless the current checkout already establishes a better compatible
  structure.
- IntelliQ's PostgreSQL database is separate from Timecue's. Enforce
  organization scoping in application services/repositories; do not claim
  Blueprint's RLS policies automatically apply to IntelliQ. Use explicit,
  reviewed migrations for runtime schema changes.
- Use actual package scripts rather than inherited Blueprint commands. Alembic
  exists; Redis/ARQ is planned in v4. Expo/mobile clients remain out of scope.

### Security and evidence invariants

- Latest owner decision: knowledge uploads and fetched source knowledge are
  accepted automatically after extraction validation, not presented for
  accept/reject review. Automatic acceptance is not human verification.
  Knowledge management is graph-first with import and deletion; deleting a
  source excludes it from future retrieval while retaining audit history.
  Accepting knowledge never implicitly approves operational writes or changes
  to numerical planning inputs; scenario review and Apply remain required.

- Revalidate tenant, user, organization permissions, source revisions, and input
  freshness on every protected IntelliQ read and mutation.
- Keep reported text, confirmed planning inputs, model diagnostics, and
  assumptions visibly separate. Unconfirmed extraction cannot alter a forecast.
- Store and render metrics from validated simulation results, not free-form model
  output. Never claim live-provider, authenticated E2E, deployed, or production
  behavior without actually verifying that boundary.

## Product Direction

This repo is a construction operations app for small and medium construction companies.

Current product priority:

1. Workforce management.
2. CRM and AI-assisted offer generation.
3. LiDAR and on-site intelligence later, as a supporting differentiator.

LiDAR already exists in a basic form, but it should not dominate the MVP roadmap. Stabilize it when needed; do not make it the early app identity.

Core positioning:

> You focus on the work. The system focuses on the paperwork.

The product is trust-first, not surveillance-first. Do not build GPS tracking, strict clock-in/out enforcement, or approval-heavy bureaucracy unless explicitly requested.

The early app should answer:

- Who is doing what?
- Where are they doing it?
- When is it planned?
- What was actually done?
- What did we agree with the client?
- What should we propose, and what needs human review?

## Users And Roles

Default product roles:

- Owner
- Manager / Project Manager
- Worker
- Client

One user can belong to multiple organizations. Always respect current organization context and tenant boundaries.

Workers have accounts. Worker flows must be fast, practical, and retrospective. Workers should not be forced to interact with the phone all day.

Client portal flows should use magic links in v1, not full client accounts.

## Repo Stack

Backend:

- FastAPI
- Python 3.12+
- SQLAlchemy
- Alembic
- PostgreSQL
- Redis
- ARQ workers
- Pydantic / pydantic-settings
- Boto3/object storage for files and scan artifacts

Frontend:

- Expo React Native
- TypeScript
- React Navigation
- TanStack Query
- Axios generated API client from `apps/api/api-schema.json`
- NativeWind / Tailwind utilities
- Zustand for persisted client state where already used
- iOS-only RoomPlan/LiDAR native module
- Three.js / expo-three for mesh fallback previews

Native / geometry:

- Keep raw ARKit and RoomPlan buffer extraction in Swift.
- Use Rust only for later-stage geometry processing when it meaningfully reduces complexity or improves performance.
- RoomPlan/USDZ is the primary scan preview path; mesh/GLB is secondary.

## Repository Layout

- `apps/api`: FastAPI backend with `src/app`, migrations, and tests.
- `apps/frontend`: React DOM/Vite app with TypeScript source and Playwright tests.
- `modules/scanner`: native scanner module code.
- `rust`: Rust geometry-related code.
- `docs` and `apps/docs`: product and technical specs.

Do not move major boundaries unless the task explicitly asks for architecture work.

## Backend Rules

- Keep business logic out of route handlers.
- Put domain behavior in services.
- Keep repositories/data access focused and explicit.
- Enforce permissions server-side.
- Validate organization/project ownership on every scoped resource.
- Treat `organizations.id` as the tenant id for tenant-owned data.
- Use explicit Alembic migrations for schema changes.
- Regenerate `apps/api/api-schema.json` after API contract changes.
- Add tests for permission and tenant-safety behavior when touching scoped resources.
- Do not introduce heavyweight infrastructure unless the feature clearly needs it.
- Write new Python code to satisfy basedpyright-style strict typing: avoid avoidable `Any`, type JSON/config shapes explicitly, narrow untrusted data before use, and keep dynamic dict access out of services/scripts unless it is validated first.
- When changing backend Python, actively follow basedpyright expectations while designing and editing: keep repository/service return types precise, avoid untyped containers, prefer explicit DTO/schema shapes, and fix type issues instead of silencing them unless a local suppression is genuinely justified.
- Avoid N+1 queries. Prefer set-based database operations for bulk work, targeted joins only when they materially reduce queries or simplify the data shape, and avoid over-fetching or unnecessarily wide joins.
- Label SQL projection columns and map result rows through named attributes or typed named projections. Do not use positional access such as `row[0]` or `row[10]`; it is fragile and obscures the query contract.
- Avoid O(n^2) algorithms on normal list, permission, membership, task, schedule, or bulk-operation paths. Use dictionaries/sets for lookup-heavy logic, but keep the optimized code simple and readable rather than clever.
- When a pure string value is reused as a domain value, status, resource type, event name, permission key, storage key segment, route/key identifier, or other non-display contract, extract it to a named constant or enum/StrEnum instead of repeating string literals. Keep one-off user-facing copy in translations, not constants.
- If you notice a technology, architecture pattern, library, or implementation technique that would be useful to learn or could materially improve the product, suggest it with concrete tradeoffs and why it fits. Do not add new technology just because it is interesting; keep implementation conservative unless it is explicitly approved.

Useful backend commands:

```sh
cd apps/api
uv sync
uv run ruff check
uv run python -m pytest
uv run python scripts/generate_openapi.py
alembic -c alembic.ini upgrade head
arq src.app.workers.worker.WorkerSettings
```

## Tenant And RLS Flow

Organization is the tenant boundary. For tenant-owned data, use `organization_id` as the tenant id and enforce tenant isolation through PostgreSQL RLS on the active scoped session.

When adding or changing tenant-owned tables:

- Add `organization_id` using the shared tenant mixin where possible.
- Include explicit foreign keys/indexes for `organization_id`.
- For nested resources, denormalize `organization_id` onto the row instead of relying only on parent joins. Example: zones, scans, scan-derived rows, tasks, worklogs, absences, CRM/offers, files, notifications.
- Populate `organization_id` on every create path from the already-authorized route organization id.
- Add an Alembic migration that backfills `organization_id` for existing rows before making it non-null.
- Enable and force RLS in the migration:

```sql
ALTER TABLE table_name ENABLE ROW LEVEL SECURITY;
ALTER TABLE table_name FORCE ROW LEVEL SECURITY;
```

- Add a tenant isolation policy based on app context:

```sql
USING (
  current_setting('app.rls_bypass', true) = 'on'
  OR organization_id = nullif(current_setting('app.organization_id', true), '')::uuid
)
WITH CHECK (
  current_setting('app.rls_bypass', true) = 'on'
  OR organization_id = nullif(current_setting('app.organization_id', true), '')::uuid
)
```

Request/session flow:

- Use request context (`contextvars`) for async-local request metadata such as host, referer, current user id/email, and current organization id.
- The normal request-scoped DB session applies that context to PostgreSQL settings for RLS.
- `get_current_user` sets user context after validating the token/session.
- `require_org_permission(...)` validates PBAC for the route `organization_id`, then sets the current organization context.
- Route paths should still include `organization_id`; it selects the organization to authorize and bind into RLS.
- Normal tenant-owned repository queries should run through the active scoped session and rely on RLS for tenant filtering. Do not add duplicate predicates like `Model.organization_id == organization_id` in normal scoped reads/deletes just to repeat the RLS policy.
- Keep explicit resource predicates that identify the object inside the current tenant, such as `Project.id == project_id`, `Zone.project_id == project_id`, `Scan.zone_id == zone_id`, or `Member.id.in_(member_ids)`.
- Services still populate `organization_id` on create from the authorized route organization because RLS `WITH CHECK` requires the inserted row to match the active tenant.
- Use the explicit global/admin session path only for internal cross-tenant work. It sets `app.rls_bypass = 'on'`; do not use it from normal controllers.

Special cases:

- Membership and invite discovery may use user-aware RLS policies before an organization is selected. If a service must prove that a user belongs to a specific organization before org context is bound, use a clearly named explicit lookup for that pre-scope check instead of a normal scoped repository method.
- Bootstrap flows that create or join an organization must set organization context before writing tenant-owned membership rows.
- Admin/internal scripts and seeders must use the explicit global admin session path and should be easy to audit because they intentionally bypass tenant RLS.
- Frontend permission checks are display-only; server PBAC and RLS remain authoritative.

## ADR And Code Documentation

Keep architecture decisions explicit and close to the product modules they affect.

ADR expectations:

- Before finishing a meaningful backend feature or architectural slice, explicitly check whether an ADR must be created or updated. If the answer is no, be able to explain why the change is local and not a durable architecture decision.
- When adding a meaningful feature, create or update the relevant ADR if the work changes module boundaries, tenant/RLS behavior, permission architecture, event/worker behavior, frontend state/navigation architecture, scan processing, or other durable design decisions.
- Add ADRs under `docs/adr/` for meaningful module or architecture decisions.
- Use one ADR per coherent decision, not one ADR per tiny code change.
- Name files with a stable number and slug, for example `0001-tenant-rls-context.md`.
- Use this structure: Status, Context, Decision, Consequences, Alternatives Considered.
- ADRs should explain why the approach exists, what tradeoffs it accepts, and what future work must preserve.
- Create ADRs for module boundaries, tenant/RLS design, permission architecture, event/outbox design, worker/job architecture, frontend state architecture, scan processing architecture, and similar decisions.
- Update an existing ADR when the decision evolves; create a new ADR when the decision is replaced.

Python documentation expectations:

- Treat documentation as part of implementation, not cleanup. When adding or changing public service methods, permission helpers, RLS/request-context helpers, worker tasks, migration helpers, storage helpers, or non-trivial repositories, add or update useful docstrings in the same change.
- Add module docstrings for non-trivial backend modules explaining the module's role, boundary, and invariants.
- Add class/function docstrings where the purpose, contract, side effects, tenant/security assumptions, or failure behavior are not obvious from the signature.
- Do not write mechanical comments or docstrings that restate each line. Avoid text like "assigns the value" or "calls the function".
- Prefer docs that answer: why this exists, what it guarantees, what must be true before calling it, and what can go wrong.
- Keep docstrings current when changing behavior. Outdated docs are worse than missing docs.
- Public service methods, permission helpers, RLS/request-context helpers, worker tasks, and complex scan/geometry functions should have useful docstrings.
- Small private helpers only need docstrings when they encode a non-obvious rule or business invariant.

TypeScript / JavaScript documentation expectations:

- Treat JSDoc the same way for code contracts, not for ordinary frontend view markup. Do not add JSDoc/docstrings to pure screen or presentational components just to describe what is visible.
- Add JSDoc for non-trivial functions, hooks, shared utilities, permission helpers, generated-client wrappers, state stores, query/mutation helpers, and cross-screen workflow logic when the contract or side effect is not obvious.
- JSDoc should explain intent, inputs/outputs, invariants, side effects, tenant/security assumptions, or failure behavior. Do not add comments that only narrate simple mechanics.
- Keep comments close to the code they explain and update them when behavior changes.

## Frontend Rules

- Use existing module patterns under `apps/frontend/src/modules` and existing API query/mutation wrappers.
- Use the generated Axios API client rather than ad hoc HTTP calls.
- After backend API changes, regenerate the client with:

```sh
cd apps/frontend
pnpm generate:client
```

- Use TanStack Query for server state and invalidation.
- When reading TanStack Query hook results, destructure only the fields needed from the hook return value. Do not store the whole query object just to read `.data`, `.isLoading`, `.error`, etc.
- When using mutation hooks, destructure the specific mutation functions and state needed, for example `const { mutateAsync: updateZone, isPending } = useUpdateZone();`. Do not keep the whole mutation object just to call `.mutateAsync` or read `.isPending`.
- Prefer backend-side filtering for server-owned lists. Keep frontend filter/search state in the UI, send it as query parameters, and build backend query conditions only when each filter is present instead of fetching broad lists and filtering them in component code.
- Debounce free-text search before sending it to backend-backed queries, especially when search changes query keys or request parameters.
- Use skeleton placeholders for screen/list loading states instead of visible text loaders such as "Loading" or "Ładowanie".
- Use Zustand only for client/session/context state where it already fits.
- When merging conditional `className` values, use the shared `cn()` helper instead of template-string ternaries.
- When a UI pattern or component is likely to be reused, extract it into a separate importable component instead of duplicating local component definitions across screens. Keep the reusable component small, named by product intent, and colocated with the module when it is module-specific.
- Before creating a new picker, select, multiselect, dropdown, action menu, tab bar, back button, empty state, or similar interaction component, search for an existing implementation and reuse it when the behavior and styling match. Do not create one-off section-specific components for the same UI pattern.
- When a pure string value is reused as a status, mode, route name, query key segment, storage/resource identifier, permission key, event name, or other non-display contract, extract it to a `const`, `as const` object/array, or enum-style type instead of repeating string literals. Do not use constants for one-off UI copy; that belongs in i18n translations.
- Avoid hardcoded hex colors in frontend code. Use Tailwind/NativeWind theme classes such as `bg-background`, `text-foreground`, `text-muted-foreground`, `border-border`, `bg-primary`, or shared theme tokens for APIs that require a color string.
- Avoid nested ternaries in component logic; use `switch` statements or small helper functions for multi-branch choices.
- For conditional rendering where the fallback is `null`, prefer `condition && <Component />` over `condition ? <Component /> : null`.
- API mutation/query error handlers should use a shared translated API-error helper such as `getTranslatedApiErrorMessage(...)`; do not parse `error.response.data.detail` inline in every hook.
- Worker flows should aim to complete in about 20 seconds or less.
- Use clear empty states, error states, large touch targets, and shallow navigation.
- When planning or designing important product UI, research comparable best-in-class apps and competitors first, especially construction/workforce tools such as Procore, Fieldwire, Raken, Buildertrend, Connecteam, and similar operational mobile dashboards. Translate proven patterns into Timecue's product direction instead of copying them directly: few clicks, obvious actions, compact mobile-first lists/cards, visible filters, and practical construction-ops workflows that do not need in-app onboarding.
- UI should be aesthetically pleasing but still field-usable: operational density, clear hierarchy, restrained surfaces, strong touch targets, and familiar controls should matter more than decorative dashboard chrome.
- Hide actions the user lacks permission for, but never rely only on frontend permission checks.
- Keep project workspace screens operational and identity-forward, not static metadata pages.
- Keep scan capture controls obvious, with cancel/back controls on the left.

Useful frontend commands:

```sh
cd apps/frontend
pnpm lint
pnpm generate:client
pnpm ios
pnpm android
pnpm web
```

Root commands:

```sh
pnpm lint
pnpm check-types
pnpm build
```

## Permission Direction

Use PBAC architecture.

Roles map to permission keys. Organization memberships can later gain overrides, but a frontend permission editor is not required for the MVP.

Default permission keys should include concepts like:

- `org.manage`
- `users.manage`
- `projects.read`
- `projects.write`
- `tasks.read`
- `tasks.write`
- `tasks.assign`
- `worklogs.read`
- `worklogs.write`
- `absences.read`
- `absences.write`
- `crm.read`
- `crm.manage`
- `offers.manage`
- `scans.manage`

Server-side permission checks are authoritative. Frontend permission checks are only UX.

## Event Architecture

Use event-driven architecture lightly.

Recommended v1 shape:

- PostgreSQL is the source of truth.
- Persist important events in a `domain_events` or outbox-style table.
- Background workers process events and send notifications.
- Redis can be used for websocket fanout, pub/sub, cache, or ephemeral state.
- Do not use Kafka or RabbitMQ in v1 unless there is a strong reason.
- Do not build full event sourcing.

Important domain events:

- `task.created`
- `task.assigned`
- `task.updated`
- `task.completed`
- `worklog.created`
- `absence.reported`
- `client.message.created`
- `offer.generated`
- `offer.sent`
- `offer.accepted`
- `offer.rejected`
- `scan.created`
- Future only: `scan.critical_issue_found`

## Notifications

- Use Expo push notifications for mobile.
- Store push tokens per user/device.
- Create in-app notification records.
- Send push notifications asynchronously from event handlers or workers.
- Push failures must not break core API flows.

## Chat And Discussions

For project communication:

- Persist messages in PostgreSQL.
- Use FastAPI WebSockets only for realtime delivery when needed.
- Use Redis Pub/Sub for multi-instance websocket fanout if the deployment needs it.
- Clients must reconnect and fetch missed messages by last seen id or timestamp.
- WebSocket is not the source of truth.
- Start with chat-lite/project discussion, not Slack-level chat.

## Workforce Product Rules

Tasks:

- Belong to a project.
- May belong to a zone.
- Use simple statuses such as `planned`, `assigned`, and `done`.
- Can assign workers and/or teams.

Worklogs:

- Retrospective logging is first-class.
- Required: project, date, hours.
- Optional: task, free-text work name, description, photos, materials later.
- No manager approval in v1.

Absences:

- Self-reported.
- No approval in v1.
- Manager uses them as operational information.

Assignments:

- Warn about conflicts.
- Do not block managers from assigning work because construction schedules are dynamic.

## CRM And AI Offer Rules

- CRM is project-centered.
- Client portal uses magic links in v1.
- Avoid full client accounts in v1.
- AI offer generation drafts proposals.
- Humans review before sending.
- No contract automation or e-signature in v1.
- Tests around AI behavior should use deterministic doubles/mocks.

## LiDAR Product Rules

- LiDAR is a future moat, not the current wedge.
- Stabilize scan flow, but hide AI findings until real.
- Do not show mocked AI findings as production functionality.
- Android should show a helpful fallback when scanning is iOS-only.
- RoomPlan/USDZ preview is primary.
- Mesh/GLB preview is secondary and should improve readability, not replace RoomPlan semantics.
- Direct RustFS/S3-compatible `localhost:9000` preview URLs can fail on phones; prefer API download to local cache before native preview.

## Linear Issue Rules

When generating Linear issues:

- Group by epics/modules.
- Include backend and frontend in the same issue.
- Include plain-English goal for a non-technical co-founder.
- Include user story.
- Include scope.
- Include backend scope.
- Include frontend scope.
- Include acceptance criteria.
- Include non-goals.
- Include dependencies.
- Include Codex implementation notes.
- Prioritize P0/P1/P2/P3.

Do not split frontend and backend unless the implementation becomes impossible to manage in one issue. Each issue should represent a complete product capability.

## Spec Execution Workflow

Large specs and roadmap issues are planning artifacts, not one-shot implementation tasks.

When development starts from a large spec or Linear issue:

- Read the relevant spec section first.
- Break the work into small, reviewable implementation tasks.
- Use the project branch naming convention: `jh_type_issue_description`, for example `jh_feat_con_16_worker_directory_basics`. Use underscores as separators. Do not use Linear-generated branch names when they do not match this convention.
- Keep each development branch focused on one small task or coherent capability slice.
- Implement backend, frontend, tests, and generated clients together when the user-facing capability requires them.
- Push and merge small increments instead of holding a large long-running branch.
- Keep acceptance criteria visible and mark only the completed slice as done.
- Do not implement adjacent roadmap features just because they are mentioned in the same spec.

## MVP Cut Lines

Must ship for Workforce MVP:

- Organization/user settings.
- Project edit.
- PBAC and tenant safety.
- Invite flow.
- Worker directory.
- Teams.
- Tasks.
- Assignments.
- Schedule view.
- Worker home.
- Manager Today dashboard.
- Worklogs.
- Absences.

Strongly recommended for pilot:

- Domain events.
- In-app notifications.
- Expo push notifications.
- Missing worklog reminders.
- Conflict warnings.
- Basic files/photos.

Explicitly not v1:

- GPS tracking.
- Full payroll export.
- Advanced overtime rules.
- Accountant role.
- Full offline mode.
- E-signature contracts.
- Full inventory.
- Vehicle/machine/tool tracking.
- Full AI scan findings.
- Automated scheduling optimizer.
- Enterprise BI dashboards.

## Anti-Patterns

Avoid:

- Overbuilding permissions UI.
- Enterprise dashboards.
- GPS surveillance.
- Strict clock-in/out as the primary model.
- Blocking manager assignments because of warnings.
- Full offline mode in v1.
- Kafka/RabbitMQ in v1 without strong reason.
- Fake AI findings.
- Complex material/tool borrowing workflows too early.
- Building LiDAR AI before workforce/CRM adoption.
- Splitting product capability issues into separate backend and frontend tickets by default.
