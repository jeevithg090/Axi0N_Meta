#!/usr/bin/env bash
# Warm, cool and keep alive Rulecam's two RunPod serverless GPUs (SAM and Glimmer).
#
#   scripts/ops/warm.sh on         # workers.min=1 on both, then wait until each /ping answers 200
#   scripts/ops/warm.sh off        # workers.min=0 again: scaled to zero, nothing bills when idle
#   scripts/ops/warm.sh status     # workers min / max / idle timeout and workers by status
#   scripts/ops/warm.sh keepalive  # one authenticated /ping each; loud warning if workers.max shrank
#
# A warm worker bills around the clock ($1.34/h per GPU on the RTX 4090 pool): turn it on only for
# demo slots, and `off` right after. RunPod lowers workers.max on an endpoint that gets no request
# (3 days: 2, 7 days: 0, until someone raises it by hand), so run `keepalive` once a day through
# judging and the finals (scripts/ops/keepalive_loop.sh). A keepalive /ping on a cold endpoint
# starts a worker: one cold start plus the 300 s idle timeout, about $0.15 per GPU.
#
# Needs RUNPOD_API_KEY in the environment: a RunPod key that can read and update the two endpoints
# and pass their load balancers. It is never printed and never on a command line (curl reads the
# header from stdin). Overrides: SAM_ENDPOINT_ID (imz4qu1vzwp4z6), GLIMMER_ENDPOINT_ID
# (z000o8uch2wj4i), SAM_WORKERS_MAX (2) and GLIMMER_WORKERS_MAX (3), the limits `keepalive`
# expects, WAIT_S (900), how long `on` waits for both /ping, and RUNPOD_REST
# (https://api.runpod.io/v2). Exit status: 0 ok, 1 a check failed, 2 usage or missing key.
set -euo pipefail

REST=${RUNPOD_REST:-https://api.runpod.io/v2}
SAM_ENDPOINT_ID=${SAM_ENDPOINT_ID:-imz4qu1vzwp4z6}
GLIMMER_ENDPOINT_ID=${GLIMMER_ENDPOINT_ID:-z000o8uch2wj4i}
SAM_WORKERS_MAX=${SAM_WORKERS_MAX:-2}
GLIMMER_WORKERS_MAX=${GLIMMER_WORKERS_MAX:-3}
WAIT_S=${WAIT_S:-900}
USER_AGENT="rulecam-ops/1.0"  # Cloudflare in front of RunPod blocks some default agents
PING_TIMEOUT_S=150            # the load balancer holds a request through a cold start (~2 min)

ENDPOINTS=("sam $SAM_ENDPOINT_ID $SAM_WORKERS_MAX" "glimmer $GLIMMER_ENDPOINT_ID $GLIMMER_WORKERS_MAX")

usage() {
  sed -n '2,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
  exit 2
}

need_key() {
  if [ -z "${RUNPOD_API_KEY:-}" ]; then
    echo "warm.sh: RUNPOD_API_KEY is not set" >&2
    exit 2
  fi
}

# rest METHOD PATH [JSON]: one RunPod REST call; prints the body, fails on an HTTP error.
rest() {
  local method=$1 path=$2 body=${3:-}
  local args=(-fsS -m 30 -X "$method" -A "$USER_AGENT" -H @- -H "Accept: application/json")
  if [ -n "$body" ]; then
    args+=(-H "Content-Type: application/json" --data "$body")
  fi
  printf 'Authorization: Bearer %s\n' "$RUNPOD_API_KEY" | curl "${args[@]}" "$REST$path"
}

# ping_code ID: the HTTP status of an authenticated GET /ping through the endpoint's load balancer.
ping_code() {
  printf 'Authorization: Bearer %s\n' "$RUNPOD_API_KEY" \
    | curl -s -o /dev/null -w '%{http_code}' -m "$PING_TIMEOUT_S" -A "$USER_AGENT" -H @- \
      "https://$1.api.runpod.ai/ping" || true
}

# workers_max ENDPOINT_JSON: the endpoint's workers.max, or empty.
workers_max() {
  python3 -c 'import json, sys; print((json.loads(sys.argv[1]).get("workers") or {}).get("max", ""))' "$1"
}

set_min() {
  local name=$1 id=$2 min=$3
  rest PATCH "/serverless/$id" "{\"workers\": {\"min\": $min}}" > /dev/null || return 1
  echo "$name: workers.min=$min"
}

wait_ready() {
  local name=$1 id=$2 started code
  started=$(date +%s)
  while true; do
    code=$(ping_code "$id")
    if [ "$code" = "200" ]; then
      echo "$name: /ping 200 after $(($(date +%s) - started)) s"
      return 0
    fi
    if [ $(($(date +%s) - started)) -ge "$WAIT_S" ]; then
      echo "$name: /ping still $code after $WAIT_S s" >&2
      return 1
    fi
    sleep 5
  done
}

show() {
  local name=$1 id=$2 endpoint workers
  endpoint=$(rest GET "/serverless/$id") || return 1
  workers=$(rest GET "/serverless/$id/workers") || return 1
  python3 - "$name" "$endpoint" "$workers" <<'PY'
import json
import sys

name, endpoint, workers = sys.argv[1], json.loads(sys.argv[2]), json.loads(sys.argv[3])
limits = endpoint.get("workers") or {}
idle = limits.get("idleTimeout", (endpoint.get("scaling") or {}).get("idleTimeout"))
summary = workers.get("summary") or {}
counts = " ".join(
    f"{k}={summary.get(k, 0)}" for k in ("running", "idle", "initializing", "throttled", "unhealthy")
)
print(f"{name}: min={limits.get('min')} max={limits.get('max')} idle_timeout_s={idle} {counts}")
PY
}

keepalive() {
  local name=$1 id=$2 expected=$3 code endpoint max
  code=$(ping_code "$id")
  endpoint=$(rest GET "/serverless/$id") || endpoint='{}'
  max=$(workers_max "$endpoint")
  echo "$name: /ping $code, workers.max=$max (expected $expected)"
  if [ -z "$max" ] || [ "$max" -lt "$expected" ]; then
    {
      echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
      echo "!!! $name ($id): workers.max is ${max:-unreadable}, expected $expected."
      echo "!!! RunPod shrinks idle endpoints; raise it in the console (Serverless > endpoint >"
      echo "!!! Edit > Max workers) before the next demo, or the endpoint cannot serve."
      echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    } >&2
    return 1
  fi
  [ "$code" = "200" ] || [ "$code" = "204" ]
}

each() {
  local action=$1 status=0 entry
  for entry in "${ENDPOINTS[@]}"; do
    # shellcheck disable=SC2086  # "name id max" splits on purpose
    "$action" $entry || status=1
  done
  return "$status"
}

on_one() { set_min "$1" "$2" 1; }
off_one() { set_min "$1" "$2" 0; }
ready_one() { wait_ready "$1" "$2"; }
show_one() { show "$1" "$2"; }

main() {
  [ $# -eq 1 ] || usage
  case $1 in
    on) need_key; each on_one && each ready_one ;;
    off) need_key; each off_one ;;
    status) need_key; each show_one ;;
    keepalive) need_key; each keepalive ;;
    *) usage ;;
  esac
}

main "$@"
