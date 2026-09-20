# ADR 0001 — Session-backed Timecue decision layer

## Status

Accepted for the hackathon build, 19 September 2026.

## Context

IntelliQ needs to compare recovery actions using Timecue operational data while
respecting the signed-in manager's organization permissions. The current
Timecue API already exposes projects, tasks, assignments, workforce, worklogs,
and calendar reads. It does not supply every planning value needed by the
simulator.

## Decision

- IntelliQ is a separate React/FastAPI application. Its backend logs in through
  Timecue on behalf of the user and calls existing authorized routes with that
  user's session. No new Timecue endpoint is part of the MVP.
- The browser holds only opaque IntelliQ HttpOnly session state and a CSRF
  token. Upstream credentials stay in a per-user server-side session boundary;
  they must not be written to browser storage, model prompts, snapshots, jobs,
  or persistent plaintext records.
- IntelliQ aggregates an internal snapshot and stores manager-confirmed
  planning inputs separately from Timecue. A snapshot with unresolved donor
  commitments does not produce transfer recommendations.
- The simulation runs locally over the whole relevant portfolio. Evidence
  extraction proposes reports; only confirmed planning changes alter the
  numeric model. Decisions record actions and checkpoints without applying
  assignments to Timecue.
- Fixture mode is an explicit synthetic data mode for the demo. It never
  silently replaces a failing live request.

## Consequences

The first build can demonstrate the decision flow without a Timecue API
change. Live mode still depends on verifying deployed route/cookie behavior
and complete permissioned reads. Cross-origin automatic browser SSO is outside
this decision. Process-local upstream sessions require a new sign-in after
process restart. SQLite is suitable for one demo process but is not a
multi-instance production persistence design.

## Alternatives Considered

- A new Timecue `/intelliq/snapshot` route: deferred because existing reads
  suffice for the hackathon and upstream code change adds integration risk.
- A shared Timecue API key or direct database access: rejected because it would
  bypass per-user authorization and tenant boundaries.
- Browser-side Timecue calls: rejected because the planning and decision
  service requires server-side session handling and local persistence.
