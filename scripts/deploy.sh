#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${INTELLIQ_DEPLOY_ENV:-${ROOT_DIR}/.env.deploy}"
COMPOSE_FILE="${ROOT_DIR}/compose.production.yaml"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing deployment env file: ${ENV_FILE}" >&2
  echo "Set INTELLIQ_DEPLOY_ENV or create .env.deploy; do not commit it." >&2
  exit 2
fi

compose=(docker compose --env-file "${ENV_FILE}" --file "${COMPOSE_FILE}")

case "${1:-up}" in
  --check)
    "${compose[@]}" config --quiet
    ;;
  up)
    "${compose[@]}" config --quiet
    "${compose[@]}" up -d --wait postgres redis
    "${compose[@]}" run --build --rm migrate
    "${compose[@]}" up -d --build --wait api worker web reverse-proxy
    ;;
  *)
    echo "Usage: scripts/deploy.sh [--check|up]" >&2
    exit 2
    ;;
esac
