# ADR 0005: Daily portfolio decisions and supported execution

## Status

Accepted product/architecture direction, updated 20 September 2026.
See [implementation plan v4](../implementation-plan-v4.md).

Supersedes the record-only execution boundary in ADR 0001 and project-first
navigation in ADR 0004. ADR 0002's PostgreSQL/Turbo foundations remain; its earlier
infrastructure scope is extended with Redis/ARQ for scheduled work.

## Context

The owner needs daily actionable compromises across shared project resources.
Separate project screens obscure a simulation engine that already evaluates a
portfolio. The agreed scope now includes imports, owner-approved upstream writes,
required 3D detail exploration and actual-outcome feedback. Timecue source cannot
change, and its existing APIs cannot necessarily represent every suggested action.

## Decision

- Make one immutable portfolio run the basis for alternatives and approval.
- Balance bounded candidate generation across action families and bundles of
  one, two and three compatible actions; simulate the whole bundle together.
- Persist an optional, source-referenced AI explanation per published option.
  Cards and approval reuse that prose, while numerical comparisons and context
  provenance stay deterministic. No provider call occurs during approval.
  Weather availability is not evidence of a weather effect; route estimates
  must not explain options without transfers. Provider failure falls back to
  the stored action summary and never blocks numerical publication.
- Publish daily recommendations ready at 06:00 organization-local time, using
  encrypted renewable owner sessions and revalidated permissions.
- Keep operational records in Timecue; analytical assumptions/history in IntelliQ.
- Use explicit persisted workflow stages, Redis/ARQ dispatch and PostgreSQL truth.
- Review exact changes before applying existing API operations. Track unsupported
  actions as manual steps and partial results with receipts and reconciliation.
- Use decision home, comparison/review and a separate deep detail experience;
  3D is a required relationship view, with accessible 2D/list fallback.
- Separate Decisions and Projects navigation: desktop header links and mobile
  bottom navigation. Project screens expose canonical tasks, workers, worklogs
  and project-linked documents inside IntelliQ, not independent simulations.
- Put spreadsheet import in Projects and document/note upload inside the selected
  project. Keep organization switching only in the profile menu; no organization
  settings UI. Scheduled analysis defaults remain backend configuration.
- During a run, replace the decision content with the current real pipeline phase;
  publish results in place rather than inserting another progress card.
- Update forecasts from provenance-backed actual observations and versioned
  parameters, preserving original predictions.

## Consequences

Requires durable session handling, job lifecycles, shared DTOs, execution ledgers
and cache invalidation. Upstream multi-step writes are not atomic; IntelliQ cannot
prevent all races with Timecue users. These limits must be represented in results.
One portfolio approval invalidates competing recommendations. No upstream code
changes, autonomous application or monetary scoring enter the hackathon scope.

## Alternatives considered

- Independent project analyses: fail to coordinate shared resource decisions.
- Record-only approval: no longer satisfies the accepted application workflow.
- New Timecue orchestration endpoint: forbidden by the product constraint.
- Free-running LLM agent: unnecessary for bounded rules and scenario evaluation.
- 3D-only UI: unsuitable for the owner's quick daily flow and inaccessible without
  an equivalent inspector/list experience.
