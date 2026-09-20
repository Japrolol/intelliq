# IntelliQ v4 hackathon demo

## Current v4 rehearsal

Run `pnpm dev` and open `http://intelliq.localhost:8082`. This starts local
PostgreSQL/Redis, API, ARQ worker, frontend and workspace Caddy. The existing
system Caddy daemon and Timecue source remain unchanged.

The ignored API environment currently uses **live Timecue** on port 8000,
durable encrypted sessions, background jobs and authorized LLM processing.
Sign in with your Timecue account. Weather/routing keys are not yet configured;
coverage warnings are intentional. Never put credentials into git.

For a synthetic rehearsal, use fixture mode with a separate database, never a
reset of the operational volumes. `pnpm --filter frontend test:e2e` provisions
its own temporary fixture database with jobs/external processing disabled.

1. **Today's decisions:** show the portfolio recommendation and publication date.
   Prepare the first analysis; later daily publication defaults to 06:00 in the
   organization's timezone.
2. **Compare options:** show dates, schedule risk, capacity and donor buffers.
   Fast/Balanced/Safe may identify the same evaluated plan. Keeping the current
   plan is valid when no tested action improves the constrained trade-off.
3. **Details:** inspect finish distributions, capacity and the interactive shared
   worker/project/task graph. Switch scenario and select entities for impact
   details. Use 2D/list on mobile or when WebGL is unavailable.
4. **Review → Apply:** inspect exact changes before approval. Only supported
   existing Timecue routes execute. Temporary transfers remain manual steps,
   never permanent assignment substitutes. Fixture receipts explicitly state
   that no upstream write was attempted.
5. **Manual completion:** attest only an implemented step. Uncertain writes need
   reconciliation; stale plans need a new review, not blind retries.
6. **Measured outcome:** choose the confirmed transfer worker and enter observed
   setup hours, event time and note. Label a synthetic measurement as synthetic.
   Run a later analysis to show calibration and forecast changes. The original
   prediction stays frozen; this does not prove a globally optimal decision.

### Import and knowledge

- Add data → Import portfolio. Use `fixtures/imports/portfolio.xlsx` or matching
  CSV files. The English template contains placeholder member/specialty IDs;
  map them to the authorized Timecue organization first. Unmatched members block
  commit. Review source values and confirm planning assumptions separately.
- Inspect receipts and canonical readback; partial delivery is not success.
- Upload a supplied note/photo. Extraction yields proposed claims with provenance.
  Review a claim, task and whitelisted planning field before projecting an input.
  Confirming a fact alone never silently changes planning or Timecue. Versioned
  extraction caches avoid repeatedly paying to analyze an unchanged source.
- Run a new analysis to see confirmed material/readiness constraints. Weather
  requires applicable confirmed rules and dated coverage; illustrative thresholds
  are not engineering or safety certification.

The simulator uses paired seeded samples and a bounded candidate search. The LLM
extracts evidence and explains grounded results; it never supplies invented
metrics or autonomously approves execution. Learning currently calibrates measured
transfer setup, not arbitrary worker performance from repeated generated prose.

Timecue writeback and live weather/routes still require the connected
rehearsal. No external deployment has occurred.

## Historical numerical fixture story

The sequence below describes the original deterministic fixture, not the v4 UI.

Use fixture mode for the repeatable story. It is synthetic and should be visibly
labelled throughout. Keep live Timecue credentials and real customer data out
of the presentation recording.

1. Open the fixture portfolio. Point out Alpha's target and Beta's donor buffer.
2. Open Alpha's worklog evidence. The note reports that electrical work blocks
   wall closure; it also says the cables have arrived. Confirm only a valid
   missing planning input. If the dependency already exists, show that the
   note corroborates it rather than adding a second delay.
3. Run the unchanged baseline. Explain that the probabilities are modelled
   against manager-supplied remaining effort and declared calendars.
4. Test a Monday transfer of electrician EB from Beta to Alpha. Show exact
   dates and nonproductive transfer/setup time.
5. Compare both projects. The deterministic unit fixture expects Alpha to
   finish Wednesday instead of Thursday and Beta Wednesday instead of Tuesday.
   Beta's late days remain zero, but its one-day buffer is consumed.
6. Record the chosen strategy. Read the visible “Not applied to Timecue” state.
7. On the recorded decision, show the frozen checkpoint definition separately
   from the manager's implementation report. Add a directly observed setup
   duration and show the new fixture-only calibration version. Check the
   checkpoint with a cited observation or manager-attested note; missing
   evidence must not become a successful result.
8. If using the API during the pitch, call the fixture-only decision replay
   route with an explicit `asOf` clock. Show that later evidence is hidden
   before it was known and that the original prediction remains unchanged.
   Never present the alternate simulation as an observed counterfactual.

Before presenting, start the Dockerized PostgreSQL service and both apps from
the README commands. The local database persists between runs; use a fresh
demo database when a clean history is essential, and do not delete the named
volume as a casual reset. Rehearse the whole flow before presenting.
