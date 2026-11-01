#!/usr/bin/env bash
# Restart test for the API pod (eval/checks.yaml `survives_restart`). Run it ON the API pod.
#
# Stops the API process and starts it again through the pod's boot path (`start.sh`, which
# `boot.sh` runs when the container starts), keeping everything under the data dir. Then it
# checks that the API is healthy, the runtime loop runs, and the approved plan was restored
# (`active_plan_restored` in the log, same plan id). It does not stop or start the pod itself.
# Prints one JSON line (the evidence) and exits 1 when any check fails.
#
#   bash scripts/ops/restart_check.sh            # defaults below; override with env vars
set -euo pipefail

API=${API:-http://localhost:8000}
RULECAM_DIR=${RULECAM_DIR:-/workspace/rulecam}
DATA_DIR=${DATA_DIR:-/workspace/data}
API_LOG=${API_LOG:-/workspace/api.log}
WAIT_S=${WAIT_S:-120}

plan_file="$DATA_DIR/runtime/active_plan.json"
plan_before=""
if [ -s "$plan_file" ]; then
  plan_before=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["plan_id"])' "$plan_file")
fi
mark=$(wc -l < "$API_LOG")
started=$(date -u +%Y-%m-%dT%H:%M:%SZ)

pkill -f 'uvicorn rulecam.api.app' || true
for _ in $(seq 1 30); do pgrep -f 'uvicorn rulecam.api.app' > /dev/null || break; sleep 1; done
stopped=$(pgrep -f 'uvicorn rulecam.api.app' > /dev/null && echo false || echo true)

cd "$RULECAM_DIR"
setsid nohup ./start.sh >> "$API_LOG" 2>&1 < /dev/null &

up_after_s=-1
for i in $(seq 1 "$WAIT_S"); do
  if curl -fs -m 3 "$API/health" > /dev/null; then up_after_s=$i; break; fi
  sleep 1
done
health=$(curl -fs -m 5 "$API/health" || echo '{}')
metrics_ok=$(curl -fs -m 5 -o /dev/null -w '%{http_code}' "$API/runtime/metrics" || echo 000)
restored=$(tail -n +"$((mark + 1))" "$API_LOG" | grep active_plan_restored | python3 -c '
import json, sys
for line in sys.stdin:
    try:
        print(json.loads(line).get("plan_id", "")); break
    except ValueError:
        pass
' || true)

python3 - "$started" "$stopped" "$up_after_s" "$health" "$metrics_ok" "$plan_before" "$restored" <<'PY'
import json
import sys

started, stopped, up_s, health, metrics, before, restored = sys.argv[1:]
body = json.loads(health or "{}")
checks = {
    "process_stopped": stopped == "true",
    "healthy": body.get("ok") is True,
    "runtime": body.get("runtime") is True,
    "runtime_metrics_200": metrics == "200",
    "plan_restored": (restored == before) if before else None,
}
passed = all(v is not False for v in checks.values()) and checks["healthy"]
print(json.dumps({
    "check": "survives_restart",
    "passed": passed,
    "started_at": started,
    "up_after_s": int(up_s),
    "mode": body.get("mode"),
    "plan_before": before or None,
    "plan_restored": restored or None,
    "checks": checks,
    "note": "API process restarted through start.sh (the boot path); the pod was not restarted",
}))
sys.exit(0 if passed else 1)
PY
