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
docker compose exec -T worker sh -c '! test -e /var/run/docker.sock' \
  && echo "ok: the worker has no Docker socket"
