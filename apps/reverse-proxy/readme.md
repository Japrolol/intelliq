# IntelliQ reverse proxy

This package owns the local Caddy process for the IntelliQ workspace. It is
intentionally a host process: PostgreSQL runs in Docker, while the API,
frontend, and Caddy run under the root Turbo dev task.

Install Caddy separately and make sure `caddy` is available on `PATH`:

```sh
brew install caddy
```

The proxy serves `http://intelliq.localhost:8082`, forwards `/api/*` to the
FastAPI app on port `8002`, and forwards all other requests to Vite on port
`5173`. Port `8002` leaves Timecue's local Caddy `/api` upstream on `8000`.
