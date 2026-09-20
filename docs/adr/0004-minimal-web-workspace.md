# Minimal web workspace

## Status

Accepted.

## Context

The first interface obscured the planning workflow with repeated status cards,
marketing copy, and explanations of implementation details. The requested
rewrite prioritizes a simple working interface.

## Decision

Use a compact header and five flat navigation links: Portfolio, Planning,
Analysis, Evidence, and Decisions. Use native forms, tables, short page titles,
and a single CSS foundation. Secondary decision information uses disclosures.
Show demo labeling when synthetic data is active; explain the absence of
Timecue writeback where the manager records a decision.

Color tokens match Timecue's light theme in Blueprint's
`apps/frontend/constants/theme.ts`: warm background `#F6F4EF`, white cards,
foreground `#1F2933`, primary `#C76B3C`, muted text `#667085`, border `#D9D4C7`,
and its success, warning, and error colors. The supplied IntelliQ PNG artwork
is stored unchanged in `public/brand`; SVG viewports fit its existing whitespace
to the header and login without redrawing the logo.

Keep the existing authenticated API client, CSRF handling, tenant-scoped
resources, and SWR data hooks. Presentation changes do not change backend
contracts or simulate missing data.

## Consequences

The app has less persistent navigation chrome and fewer competing actions.
The navigation scrolls horizontally on small screens; tables scroll inside
their containers. Errors and pending states remain visible.

## Alternatives Considered

Retaining the sidebar and decorative dashboard was rejected because it kept
the complexity the user requested removing. A new UI framework adds no value
to this bounded rewrite.
