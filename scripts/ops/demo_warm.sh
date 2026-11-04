#!/usr/bin/env bash
# Bring every Meta model Rulecam serves on RunPod to a warm worker before a demo, hold it there,
# and let it go afterwards (docs/demo-day.md).
#
#   scripts/ops/demo_warm.sh up            # warm now, wait until every /ping is 200, hold, release
#   scripts/ops/demo_warm.sh at 16:30      # same, starting LEAD_MIN minutes before 16:30 UTC
#   scripts/ops/demo_warm.sh down          # workers.min=0 everywhere (nothing bills when idle)
#   scripts/ops/demo_warm.sh status        # min / max / workers by status, per endpoint
#
# Endpoints and what a warm worker costs (RunPod serverless prices read on 4 Nov 2026):
#   sam          imz4qu1vzwp4z6  SAM 3.1 + DINOv2 + OSNet, 1 x RTX 4090           $1.34/h
#   glimmer      z000o8uch2wj4i  Muse Glimmer 30B (llama.cpp), 1 x RTX 4090       $1.34/h
#   llama_guard  vzi5ij5gclndjc  Llama Guard 4 12B (vLLM FP8), 1 x 24-48 GB GPU   $0.75-1.75/h
#   spark_open   hljmhlgjyaacyv  Muse Spark Open 816B-A42B (llama.cpp), 8 x H100  $43.12/h
# SPARK=0 leaves Spark Open out (it is the expensive one, and its cold start is the longest:
# about 10 min to load 470 GB of weights, so warm it at least 20 min ahead).
#
# While holding, every PING_EVERY_S each endpoint gets one authenticated /ping and workers.min=1 is
# re-asserted; three failed pings in a row print a loud warning with the fallback to use. After
# HOLD_MIN minutes (default 120; 0 = hold until Ctrl-C) every min goes back to 0, also on Ctrl-C.
#
# Needs RUNPOD_API_KEY (never printed; curl reads it from stdin). Overrides: SAM_ENDPOINT_ID,
# GLIMMER_ENDPOINT_ID, LLAMA_GUARD_ENDPOINT_ID, SPARK_OPEN_ENDPOINT_ID, LEAD_MIN (30), HOLD_MIN,
# WAIT_S (1500), PING_EVERY_S (60), RUNPOD_REST. Exit: 0 ok, 1 an endpoint never got warm, 2 usage.
set -euo pipefail

REST=${RUNPOD_REST:-https://api.runpod.io/v2}
SPARK=${SPARK:-1}
LEAD_MIN=${LEAD_MIN:-30}
HOLD_MIN=${HOLD_MIN:-120}
WAIT_S=${WAIT_S:-1500}
PING_EVERY_S=${PING_EVERY_S:-60}
USER_AGENT="rulecam-ops/1.0"
PING_TIMEOUT_S=150

ENDPOINTS=(
  "sam ${SAM_ENDPOINT_ID:-imz4qu1vzwp4z6} 1.34"
  "glimmer ${GLIMMER_ENDPOINT_ID:-z000o8uch2wj4i} 1.34"
  "llama_guard ${LLAMA_GUARD_ENDPOINT_ID:-vzi5ij5gclndjc} 1.75"
)
[ "$SPARK" = 1 ] && ENDPOINTS+=("spark_open ${SPARK_OPEN_ENDPOINT_ID:-hljmhlgjyaacyv} 43.12")

log() { printf '%s demo_warm: %s\n' "$(date -u +%H:%M:%S)" "$*"; }
usage() { sed -n '2,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2; exit 2; }

[ -n "${RUNPOD_API_KEY:-}" ] || { echo "demo_warm.sh: RUNPOD_API_KEY is not set" >&2; exit 2; }

rest() {  # rest METHOD PATH [JSON]
  local args=(-fsS -m 30 -X "$1" -A "$USER_AGENT" -H @- -H "Accept: application/json")
  if [ -n "${3:-}" ]; then args+=(-H "Content-Type: application/json" --data "$3"); fi
  printf 'Authorization: Bearer %s\n' "$RUNPOD_API_KEY" | curl "${args[@]}" "$REST$2"
}

ping_code() {  # ping_code ENDPOINT_ID [TIMEOUT_S]
  printf 'Authorization: Bearer %s\n' "$RUNPOD_API_KEY" \
    | curl -s -o /dev/null -w '%{http_code}' -m "${2:-$PING_TIMEOUT_S}" -A "$USER_AGENT" -H @- \
      "https://$1.api.runpod.ai/ping" || true
}

set_min() {  # set_min ID N
  rest PATCH "/serverless/$1" "{\"workers\": {\"min\": $2}}" > /dev/null
}

fallback_hint() {
  case $1 in
    sam) echo "the API falls back to hosted SAM 3.1 by itself (watch sam_fallbacks in /runtime/metrics)" ;;
    glimmer) echo "unverified Tier 1 goes to the human queue; switch GLIMMER_BASE_URL to the hosted fallback (docs/demo-day.md)" ;;
    llama_guard) echo "the plan-time screen is skipped; the planner's own guardrails still decline identity rules" ;;
    spark_open) echo "Tier 2 / plan audit / Ask review are skipped (Tier 1 decides alone, unsure goes to a person)" ;;
  esac
}

status_all() {
  local entry name id rate endpoint workers
  for entry in "${ENDPOINTS[@]}"; do
    read -r name id rate <<< "$entry"
    endpoint=$(rest GET "/serverless/$id") || { echo "$name: endpoint unreadable"; continue; }
    workers=$(rest GET "/serverless/$id/workers") || workers='{}'
    python3 - "$name" "$rate" "$endpoint" "$workers" <<'PY'
import json
import sys

name, rate, endpoint, workers = sys.argv[1], sys.argv[2], json.loads(sys.argv[3]), json.loads(sys.argv[4])
w = endpoint.get("workers") or {}
s = workers.get("summary") or {}
counts = " ".join(f"{k}={s.get(k, 0)}" for k in ("running", "idle", "initializing", "throttled", "unhealthy"))
print(f"{name:12s} min={w.get('min')} max={w.get('max')} idle={w.get('idleTimeout')}s {counts}  (${rate}/h per warm worker)")
PY
  done
}

release() {
  local entry name id rate
  for entry in "${ENDPOINTS[@]}"; do
    read -r name id rate <<< "$entry"
    if set_min "$id" 0; then log "$name: workers.min=0"; fi
  done
}

up() {
  local entry name id rate total started code pending=() failures=0
  total=$(printf '%s\n' "${ENDPOINTS[@]}" | awk '{s += $3} END {printf "%.2f", s}')
  log "warming ${#ENDPOINTS[@]} endpoints; while held they bill about \$$total/h in all"
  for entry in "${ENDPOINTS[@]}"; do
    read -r name id rate <<< "$entry"
    if set_min "$id" 1; then log "$name: workers.min=1"; fi
    pending+=("$entry")
  done
  started=$(date +%s)
  while [ ${#pending[@]} -gt 0 ]; do
    local still=()
    for entry in "${pending[@]}"; do
      read -r name id rate <<< "$entry"
      code=$(ping_code "$id" 20)
      if [ "$code" = 200 ]; then
        log "$name: /ping 200 after $(($(date +%s) - started)) s"
      else
        still+=("$entry")
      fi
    done
    pending=(${still[@]+"${still[@]}"})
    [ ${#pending[@]} -eq 0 ] && break
    if [ $(($(date +%s) - started)) -ge "$WAIT_S" ]; then
      for entry in "${pending[@]}"; do
        read -r name id rate <<< "$entry"
        log "!!! $name ($id) not warm after $WAIT_S s: $(fallback_hint "$name")"
      done
      failures=1
      break
    fi
    sleep 10
  done
  status_all
  return "$failures"
}

hold() {
  local until_s=$1 name id rate code i
  local bad=()  # consecutive failed pings, by position in ENDPOINTS (bash 3 has no maps)
  trap 'log "stopping: releasing workers"; release; exit 0' INT TERM
  log "holding warm workers$([ "$until_s" -eq 0 ] || echo " until $(date -u -r "$until_s" +%H:%M 2>/dev/null || date -u -d "@$until_s" +%H:%M) UTC") (Ctrl-C releases them)"
  while [ "$until_s" -eq 0 ] || [ "$(date +%s)" -lt "$until_s" ]; do
    for i in "${!ENDPOINTS[@]}"; do
      read -r name id rate <<< "${ENDPOINTS[$i]}"
      set_min "$id" 1 || true
      code=$(ping_code "$id" 30)
      if [ "$code" = 200 ]; then
        bad[i]=0
      else
        bad[i]=$(( ${bad[i]:-0} + 1 ))
        log "$name: /ping $code (${bad[i]} in a row)"
        if [ "${bad[i]}" -ge 3 ]; then log "!!! $name is down: $(fallback_hint "$name")"; fi
      fi
    done
    sleep "$PING_EVERY_S"
  done
  release
}

until_epoch() {  # until_epoch HH:MM -> epoch seconds of the next HH:MM UTC
  python3 - "$1" <<'PY'
import datetime as dt
import sys

h, m = map(int, sys.argv[1].split(":"))
now = dt.datetime.now(dt.timezone.utc)
t = now.replace(hour=h, minute=m, second=0, microsecond=0)
if t <= now:
    t += dt.timedelta(days=1)
print(int(t.timestamp()))
PY
}

main() {
  [ $# -ge 1 ] || usage
  case $1 in
    up)
      up || true
      if [ "$HOLD_MIN" -eq 0 ]; then hold 0; else hold $(($(date +%s) + HOLD_MIN * 60)); fi
      ;;
    at)
      [ $# -eq 2 ] || usage
      local demo start
      demo=$(until_epoch "$2")
      start=$((demo - LEAD_MIN * 60))
      log "demo at $2 UTC: warming at $(date -u -r "$start" +%H:%M 2>/dev/null || date -u -d "@$start" +%H:%M) UTC"
      if [ "$(date +%s)" -lt "$start" ]; then sleep $((start - $(date +%s))); fi
      up || true
      hold $((demo + HOLD_MIN * 60))
      ;;
    down) release ;;
    status) status_all ;;
    *) usage ;;
  esac
}

main "$@"
