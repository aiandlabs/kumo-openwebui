#!/usr/bin/env bash
# Smoke test for the full demo stack: brings up Kumo, Open WebUI and Open Terminal with
# `docker compose`, waits for health, and checks that the pages and APIs respond.
#
# Needs no ai& key: it makes no model calls. In CI it runs with a dummy .env (see
# .github/workflows/ci.yml). Locally it uses your .env, on its own compose project and ports,
# so it does not touch a running demo:
#
#   scripts/compose-smoke.sh                      # project kumo-smoke, ports 8480 / 3400
#   KEEP_UP=1 scripts/compose-smoke.sh            # leave the stack running afterwards
set -euo pipefail

cd "$(dirname "$0")/.."
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-kumo-smoke}"
export KUMO_PORT="${KUMO_PORT:-8480}" WEBUI_PORT="${WEBUI_PORT:-3400}"
kumo="http://localhost:${KUMO_PORT}"
webui="http://localhost:${WEBUI_PORT}"

if [[ ! -f .env ]]; then
  echo "No .env: copy .env.example first (any values work for the smoke test)." >&2
  exit 2
fi

cleanup() {
  status=$?
  if [[ $status -ne 0 ]]; then
    echo "::group::docker compose logs"
    docker compose logs --no-color --tail 200 || true
    echo "::endgroup::"
  fi
  if [[ -z "${KEEP_UP:-}" ]]; then
    docker compose down -v --remove-orphans >/dev/null 2>&1 || true
  fi
  exit $status
}
trap cleanup EXIT

docker compose up -d --build --wait --wait-timeout "${WAIT_TIMEOUT:-600}"

failures=0
check() {
  # check <name> <url> [grep pattern]
  local name=$1 url=$2 pattern=${3:-} body
  if ! body=$(curl -fsS --max-time 30 "$url"); then
    echo "FAIL $name: $url did not respond with 2xx"
    failures=$((failures + 1))
    return
  fi
  if [[ -n $pattern ]] && ! grep -q -- "$pattern" <<<"$body"; then
    echo "FAIL $name: $url responded but did not contain '$pattern'"
    failures=$((failures + 1))
    return
  fi
  echo "ok   $name"
}

deal_id=$(curl -fsS "$kumo/openapi.json" >/dev/null && docker compose exec -T kumo python -c \
  "import sqlite3,os; print(sqlite3.connect(os.environ['KUMO_DB']).execute('SELECT id FROM deal LIMIT 1').fetchone()[0])")

check "Kumo health" "$kumo/healthz" '"ok"'
check "Kumo pipeline board" "$kumo/" 'class="board"'
check "Kumo deal page" "$kumo/deals/$deal_id" "hx-post"
check "Kumo stage page" "$kumo/stage" "/debug?compact=1"
check "Kumo debug panel" "$kumo/debug"
check "Kumo live call feed" "$kumo/debug?compact=1"
check "Kumo API docs" "$kumo/docs" "swagger"
check "Kumo tool-server OpenAPI" "$kumo/openapi.json" '"openapi"'
check "Kumo OpenAPI lists deals" "$kumo/openapi.json" '/api/deals'
check "Open WebUI health" "$webui/health" '"status":true'
check "Open WebUI page" "$webui/" "<html"

# The connections Open WebUI and the terminal use, from inside the compose network.
if docker compose exec -T open-webui curl -fsS --max-time 15 http://kumo:8080/openapi.json >/dev/null; then
  echo "ok   Open WebUI reaches Kumo's tool server"
else
  echo "FAIL Open WebUI cannot reach http://kumo:8080/openapi.json"
  failures=$((failures + 1))
fi
if docker compose exec -T kumo python -c \
  "import urllib.request; urllib.request.urlopen('http://open-terminal:8000/openapi.json', timeout=15)"; then
  echo "ok   Open Terminal serves its OpenAPI"
else
  echo "FAIL Open Terminal does not answer on http://open-terminal:8000/openapi.json"
  failures=$((failures + 1))
fi

if [[ $failures -gt 0 ]]; then
  echo "$failures check(s) failed"
  exit 1
fi
echo "All checks passed"
