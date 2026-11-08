#!/usr/bin/env bash
# Smoke test of a running Compose stack, without any model call:
# the UI loads, the API refuses a missing key, and a new workspace key lists datasets.
set -euo pipefail
base="http://127.0.0.1:${DA_AGENT_WEB_PORT:-8080}"

# expect LABEL STATUS CURL_ARGS...: the label is printed, never the arguments (they hold the key).
expect() {
  local label="$1" want="$2" got
  shift 2
  got=$(curl -s -o /dev/null -w '%{http_code}' "$@")
  if [ "$got" != "$want" ]; then
    echo "FAIL: $label returned $got, expected $want" >&2
    exit 1
  fi
  echo "ok: $label ($want)"
}

expect "the UI loads" 200 "$base/"
expect "the API refuses a missing key" 401 "$base/v1/datasets"
key=$(docker compose exec -T api python -m da_agent.admin create-workspace "Smoke test" \
  | sed -n 's/^key: //p')
expect "a new workspace key lists datasets" 200 -H "Authorization: Bearer $key" "$base/v1/datasets"

# Browser sign-in (ADR 0025): an operator link becomes a cookie that opens the dashboard and
# nothing in a workspace. The cookie jar is deleted on exit.
jar=$(mktemp)
trap 'rm -f "$jar"' EXIT
link=$(docker compose exec -T api python -m da_agent.admin login-link --operator)
expect "an operator link signs in" 200 -c "$jar" -H "Origin: $base" \
  -H "Content-Type: application/json" -d "{\"token\": \"${link#*#token=}\"}" \
  "$base/v1/auth/redeem"
expect "the operator sees the dashboard" 200 -b "$jar" "$base/v1/ops/summary"
expect "the operator sees no workspace data" 404 -b "$jar" "$base/v1/datasets"
expect "a key cannot open the dashboard" 404 -H "Authorization: Bearer $key" \
  "$base/v1/ops/summary"
docker compose exec -T worker sh -c '! test -e /var/run/docker.sock' \
  && echo "ok: the worker has no Docker socket"
