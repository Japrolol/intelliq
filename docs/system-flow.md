# IntelliQ system flow

> Existing v3 flow. The revised planned daily analysis, execution and learning
> flow is in [implementation plan v4](implementation-plan-v4.md).

1. A manager signs in with a Timecue account. IntelliQ checks the current
   user and authorized organization on every protected request.
2. IntelliQ reads projects, tasks, full assignments, workers, specialties,
   reservations, and worklogs through existing Timecue routes using that user's
   session. It records the read window and missing sections in an internal
   snapshot.
3. The manager confirms missing planning inputs: target dates, remaining
   person-hours, dependencies, skills, working calendars, and transfer costs.
   An incomplete snapshot or missing critical inputs produces `needs_inputs`.
4. Changed task and worklog text goes through a bounded evidence extractor.
   It returns exact quotes with a source, reported state, and possible task
   links. The UI shows these as reports pending review. A manager can confirm
   a concrete planning change; this versions the assumptions and triggers a
   new baseline. The extractor never supplies delay probabilities.
5. The scheduler builds a task dependency graph and simulates future hourly
   work across the whole relevant portfolio. It samples remaining effort once
   per task, respecting worker skills, shifts, crew caps, reservations, and
   prerequisites. The unchanged simulation is the baseline.
6. A bounded candidate generator tests feasible transfer, overtime, or priority
   actions. Baseline and candidates share sampled futures. Each result shows
   target-project benefit, donor-project date movement and buffer consumption,
   and explicit action hours. A candidate with missing donor commitments is
   rejected.
7. The manager selects a stored strategy and records a Decision Contract with
   frozen inputs, predicted outcomes, limits, and checkpoints. The UI states
   that the action has not been applied to Timecue.
8. Later observations can be compared with the frozen prediction. Only direct
   comparable transfer-setup observations update a versioned setup parameter.
   Historical synthetic replay uses isolated fixture data and a test clock.

The local fixture demonstrates the full story without implying that a live
Timecue account, provider, or customer outcome was validated. Live auth and
upstream integration require a configured Timecue URL and an authorized test
account; failures are visible rather than replaced with fixture results.
