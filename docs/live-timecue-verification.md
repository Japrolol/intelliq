# Live Timecue verification

Fixture tests do not prove that a Timecue account works. IntelliQ must be run
in an explicit live mode; it never changes to fixture data after an upstream
error.

## Local setup

1. Start Timecue's own dependencies and API using the Timecue checkout's
   instructions. Bind Timecue to `127.0.0.1:8000` so `https://app.timecue.localhost`
   can proxy `/api` there. IntelliQ FastAPI uses port `8002`.
2. Verify `http://127.0.0.1:8000/openapi.json` responds before configuring
   IntelliQ. This checks reachability only, not login or database readiness.
3. In `apps/api/.env`, set `DATA_MODE=live` and
   `TIMECUE_API_URL=http://127.0.0.1:8000`. Keep `DATABASE_URL` pointed at the
   IntelliQ PostgreSQL database, not the Timecue database.
4. Restart the IntelliQ backend. `GET http://127.0.0.1:8002/api/runtime` must
   report `{"sourceMode":"live","synthetic":false}`.
5. Open IntelliQ at `http://intelliq.localhost:8082` or `http://127.0.0.1:5173`
   and sign in with a verified Timecue account that has the required organization
   read permissions. Enter the password only in the UI, never in a command,
   document, or chat.

## Acceptance checks with that account

- The organization picker shows only authorized organizations; an unauthorized
  organization URL is denied.
- Portfolio loads real projects, tasks, workers, worklogs, and calendar
  commitments. Missing sections or permissions are visible, not silently
  replaced by fixture data.
- Planning setup exposes missing estimates/constraints. A baseline and one
  recovery comparison use the same current source revision.
- Evidence review shows exact source quotes. Reviewing evidence alone does not
  change a forecast; a planning-input change is separate and explicit.
- The chosen strategy records a decision without writing assignments or dates
  back to Timecue. A later implementation observation remains separate.
- Refresh and logout invalidate the correct user's session. Two accounts do not
  exchange organizations, notes, analyses, or cookies.

The local Timecue API was reachable on port 8000 on 2026-09-20, and its
unauthenticated `/api/v1/auth/me` returned 401. This is **not** a verified
account login or an authenticated end-to-end pass.
