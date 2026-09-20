# IntelliQ v4 Timecue capability matrix

This matrix records the existing Timecue surface used by the v4 import slice.
It is an IntelliQ integration note, not a new Timecue contract. Paths are
relative to the configured Timecue `/api/v1` base URL. IntelliQ must use the
signed-in user's server-side session and must surface an upstream failure.

## Read surface

| Capability | Existing route/method | IntelliQ use | Permission boundary | Readback/limits |
| --- | --- | --- | --- | --- |
| Organizations | `GET /organizations` | Select the organization already visible to the signed-in user | Authenticated upstream session; IntelliQ also checks the organization membership | Never infer or create an organization during import |
| Members | `GET /organizations/{org}/members` | Match `Workers.timecueMemberId` to an existing member | Organization-scoped authenticated read; IntelliQ requires the existing worker-read permission for the protected route | Exact ID matching only; duplicate or missing matches block the row |
| Worker profiles | `GET /organizations/{org}/workforce/workers?offset=&limit=` | Resolve a member to a worker profile for assignment compilation | `workers.read` in the IntelliQ snapshot boundary | Follow `nextOffset`; do not truncate or use a partial page |
| Specialties | `GET /organizations/{org}/workforce/specialties` | Validate planning `skillKeys` against the upstream vocabulary when available | `workers.read` in the IntelliQ snapshot boundary | Unknown skills remain a preview error; no inferred specialty is created |
| Projects | `GET /organizations/{org}/projects` | Detect source revision and verify newly created project IDs | `projects.read` | Name similarity is display-only; it is not an import match |
| Tasks | `GET /organizations/{org}/projects/{project}/tasks?include_completed=true` | Verify task IDs and read back created tasks | `tasks.read` | Readback is scoped to the project returned by Timecue |
| Effective assignments | `GET /organizations/{org}/projects/{project}/tasks/{task}/assignments` | Preserve existing direct workers and team assignments before an assignment write | `tasks.read` / assignment capability at upstream | A team assignment is never replaced with an empty `teamIds` list |
| Worklogs | `GET /organizations/{org}/worklogs` with cursor pagination | Existing portfolio evidence only; not written by the import slice | `worklogs.read` | Cursor pagination must complete or remain a visible integration failure |
| Calendar | `GET /organizations/{org}/calendar` | Existing reservations and capacity context | `projects.read`/calendar capability as granted upstream | Team-derived reservations remain unresolved rather than being guessed |

The current IntelliQ live adapter composes projects, tasks, effective
assignments, workers, specialties, worklogs and calendar reservations from
these reads in `apps/api/src/app/integrations/timecue.py`. It refreshes the
one per-user upstream session once after a 401 and retries that read once.

## Write surface used by imports

| Operation | Existing route/method and verified body | Permission | Import policy | Receipt/readback |
| --- | --- | --- | --- | --- |
| Create project | `POST /organizations/{org}/projects`; verified body is `name`, `description`, `status` (`lead`/`estimation`/`waiting_for_client`/`accepted`/`scheduled`/`in_progress`/`done`/`archived`), `currency`, `countryCode`, and `timezone`. Spreadsheet `planned` maps to `scheduled`. Defaults `PLN`/`PL` when omitted. | `projects.write` | Create only after a stable row has no prior mapping. The template address is retained as IntelliQ source data because this slice does not assume an upstream address field | Read `GET .../projects`, locate the returned ID, and record the response plus canonical row |
| Create task | `POST /organizations/{org}/projects/{project}/tasks`; the seed path uses `projectId`, `title`, `description`, `status`, `plannedStartAt`, `plannedEndAt`, and `estimatedMinutes` | `tasks.write` | Create only after its project mapping is applied. No name-based update or duplicate create is attempted | Read `GET .../projects/{project}/tasks?include_completed=true` and verify the returned ID/project |
| Change existing task dates | `PATCH /organizations/{org}/projects/{project}/tasks/{task}` with the verified `plannedStartAt` and `plannedEndAt` fields | `tasks.write` | Supported only when an explicit upstream task ID/mapping is present and the exact before-values are in the approved record. A timed transfer is not represented as a permanent date or assignment change | Read the task collection and compare the exact intended fields; drift stops the batch |
| Add/retain task workers | `PUT /organizations/{org}/projects/{project}/tasks/{task}/assignments` with `workerProfileIds` and `teamIds` | `tasks.assign` | Read current direct workers and teams first. The import compiler unions requested worker profiles with existing direct workers and preserves existing team IDs. It never removes effective workers or converts a timed transfer into a permanent assignment | Read the assignment endpoint and require the requested workers/teams in the canonical response |

The seed script also demonstrates `POST/PATCH` worker-profile routes,
`POST /organizations/{org}/calendar/events`, and
`POST /organizations/{org}/worklogs/mine`. They are intentionally outside this
import writer: imports do not create accounts, invite members, mutate worker
profiles, create calendar reservations, or manufacture worklogs.

## Failure and reconciliation rules

- Preview writes only an IntelliQ import ledger. It performs upstream reads but
  never writes Timecue.
- Every commit step has a stable local row key, intended operation, precondition,
  request summary, upstream ID, readback and receipt state.
- The writer may refresh the current per-user session once after a 401 and retry
  that exact request once. It does not blindly retry a create after a timeout or
  repeat a previously applied step. Ambiguous outcomes become
  `needs_reconciliation` and require a readback/review.
- A failed dependency blocks dependent task/assignment steps. Independent
  projects can still apply, so the batch reports `partially_applied` with the
  successful and failed receipts.
- IntelliQ's organization permission checks remain authoritative for these
  routes. Timecue remains authoritative for the upstream response and may deny a
  request even when the local permission snapshot is current.
- Temporary/timed transfers, exact worker removal, team membership changes,
  unsupported address updates, and any operation without a verified route stay
  as explicit manual steps. No account creation or invitation flow is implied.

## Reusable upstream helper seam

`src.app.services.imports.TimecueRouteClient` exposes:

```python
request(method, path, *, params=None, json_body=None) -> object
```

It is constructed with the current `LiveTimecueAdapter` and authenticated local
session. It uses the adapter's per-session cookie boundary and refresh callback;
it does not own a shared cookie jar or credentials. Data routes can use it for
the member/readback checks, while tests can inject a small recording client.
The helper is deliberately local to the data slice so the shared Timecue
adapter remains unchanged; a later integration owner can promote the seam if
other write-capable slices need the same exact contract.

## Confirmed local planning projection

Imported target finish dates, effort ranges, dependencies, transfer
assumptions and supported task fields are consumed by the IntelliQ planning
overlay only after verified mappings and readbacks. A project template's
defaultLatitude/defaultLongitude becomes the overlay location; no unsupported
upstream address mutation is attempted. The merge preserves the existing
overlay and increments its version through Store.save_planning.

Knowledge review exposes a separate local projection seam:

~~~text
apply_confirmed_claim_projection(
    store, organization_id, claim_id, change, snapshot,
    actor_id=..., source_revision=...
) -> claim
~~~

The protected review DTO accepts only taskId, projectionKey,
expectedPlanningVersion, remainingPersonHours, materialAvailableAt and
earliestStartAt. It validates the task against the authorized snapshot,
requires timezone-aware dates and ordered finite non-negative effort values,
takes the organization planning lease, saves planning before claim provenance,
and records the replay digest in task planningNotes. It never writes to
Timecue. A repeated projection key with the same digest is an
idempotent_replay; a changed key or stale planning version is a visible
conflict. The claim retains sourceDocumentId and sourceRevision, and a newer
upload supersedes old claims without deleting their audit history.
