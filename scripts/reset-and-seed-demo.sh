#!/usr/bin/env bash
# Wipe local IntelliQ + Timecue Compose volumes, migrate, and seed the Warsaw demo.
# Does not start the IntelliQ or Timecue web apps. After this, run `pnpm dev`
# and keep Timecue's API on 127.0.0.1:8000 for live mode and app.timecue.localhost.
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
INTELLIQ_API="${ROOT_DIR}/apps/api"
TIMECUE_REPO="${TIMECUE_REPO:-${HOME}/RustroverProjects/blueprint}"
TIMECUE_API="${TIMECUE_REPO}/apps/api"
TIMECUE_URL="${TIMECUE_API_URL:-http://127.0.0.1:8000}"
ORG_NAME="${TIMECUE_ORGANIZATION_NAME:-Northstar Renovations}"
EMAIL="${TIMECUE_SEED_EMAIL:-owner@example.com}"
PASSWORD="${TIMECUE_SEED_PASSWORD:-password}"
STARTED_TIMECUE_API=0

log() {
  printf '%s\n' "$*"
}

die() {
  printf '%s\n' "$*" >&2
  exit 1
}

wait_http() {
  local url="$1"
  local attempts="${2:-30}"
  local i
  for i in $(seq 1 "${attempts}"); do
    if curl -sf "${url}" >/dev/null; then
      return 0
    fi
    sleep 1
  done
  return 1
}

wait_container_healthy() {
  local name="$1"
  local i health
  for i in $(seq 1 30); do
    health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${name}" 2>/dev/null || echo missing)"
    if [[ "${health}" == "healthy" || "${health}" == "running" ]]; then
      return 0
    fi
    sleep 2
  done
  die "${name} did not become healthy"
}

disconnect_timecue_e2e() {
  docker network disconnect -f blueprint_app_net blueprint-e2e-postgres-1 >/dev/null 2>&1 || true
  docker network disconnect -f blueprint_app_net blueprint-e2e-redis-1 >/dev/null 2>&1 || true
}

[[ -d "${TIMECUE_API}" ]] || die "Timecue checkout not found at ${TIMECUE_REPO}. Set TIMECUE_REPO."
[[ -f "${INTELLIQ_API}/alembic.ini" ]] || die "IntelliQ API checkout is incomplete."
command -v docker >/dev/null || die "docker is required."
command -v curl >/dev/null || die "curl is required."
command -v uv >/dev/null || die "uv is required in PATH."

export TIMECUE_SEED_EMAIL="${EMAIL}"
export TIMECUE_SEED_PASSWORD="${PASSWORD}"
export TIMECUE_API_URL="${TIMECUE_URL}"

log "1/7  Wiping IntelliQ Compose volumes"
(
  cd "${ROOT_DIR}"
  docker compose down -v --remove-orphans
)

log "2/7  Wiping Timecue Compose volumes"
disconnect_timecue_e2e
(
  cd "${TIMECUE_REPO}"
  docker compose down -v --remove-orphans || true
)
disconnect_timecue_e2e

log "3/7  Starting Compose databases"
(
  cd "${ROOT_DIR}"
  docker compose up -d --wait
)
(
  cd "${TIMECUE_REPO}"
  docker compose up -d
)
wait_container_healthy blueprint-postgres-1
wait_container_healthy intelliq-postgres-1

log "4/7  Running migrations"
(
  cd "${TIMECUE_API}"
  .venv/bin/alembic -c src/db/alembic.ini upgrade head
)
(
  cd "${INTELLIQ_API}"
  PYTHONPATH=. uv run python -m alembic -c alembic.ini upgrade head
)

log "5/7  Seeding Timecue light users and Northstar org"
(
  cd "${TIMECUE_API}"
  uv run python scripts/seed.py light
)

log "6/7  Ensuring Timecue API on ${TIMECUE_URL}"
if ! wait_http "${TIMECUE_URL}/openapi.json" 3; then
  log "Starting Timecue API on 127.0.0.1:8000 for seeding, Caddy login, and IntelliQ live mode"
  (
    cd "${TIMECUE_API}"
    # shellcheck disable=SC1091
    source .venv/bin/activate
    exec fastapi dev main.py --host 127.0.0.1 --port 8000
  ) >/tmp/timecue-api-8000.log 2>&1 &
  STARTED_TIMECUE_API=1
  wait_http "${TIMECUE_URL}/openapi.json" 40 || die "Timecue API did not start. See /tmp/timecue-api-8000.log"
fi

log "7/7  Preparing specialties and seeding Warsaw demo"
(
  cd "${INTELLIQ_API}"
  PYTHONPATH=. uv run python scripts/prepare_timecue_demo_org.py \
    --timecue-url "${TIMECUE_URL}" \
    --organization-name "${ORG_NAME}" \
    --email "${EMAIL}"
  PYTHONPATH=. uv run python scripts/seed_timecue_demo.py \
    --timecue-url "${TIMECUE_URL}" \
    --organization-name "${ORG_NAME}" \
    --email "${EMAIL}"
)

log ""
log "Demo data is ready."
log "  Timecue login: ${EMAIL}"
log "  Organization:  ${ORG_NAME}"
log "  Timecue API:   ${TIMECUE_URL}"
log ""
log "Launch IntelliQ with:  pnpm dev"
log "Then open http://intelliq.localhost:8082 and sign in with the Timecue account."
log "IntelliQ API listens on 127.0.0.1:8002 so Timecue Caddy can keep localhost:8000."
if [[ "${STARTED_TIMECUE_API}" -eq 1 ]]; then
  log "Timecue API was started in the background (log: /tmp/timecue-api-8000.log)."
fi
