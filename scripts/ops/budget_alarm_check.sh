#!/usr/bin/env bash
# Budget-alarm test for the API pod (eval/checks.yaml `budget_alarm`). Run it ON the API pod.
#
# Sets RULECAM_BUDGET_USD_PER_CAMERA_HOUR to a test value below the GPU share of one camera,
# restarts the API (scripts/ops/restart_check.sh: the approved plan is restored), runs the given
# command while it polls /runtime/metrics for `cost.alarm`, then puts the .env back exactly as it
# was, restarts again and checks the budget in /runtime/metrics is the original one. The alarm
# needs an active camera (a rule of the active plan, a chunk in the last minute), so the command
# must feed one, e.g. an eval run:
#
#   bash scripts/ops/budget_alarm_check.sh uv run --no-sync rulecam eval run --clip warehouse_45848
#
# Prints one JSON line (the evidence); exits 1 when the alarm was not seen or the restore failed.
set -euo pipefail

API=${API:-http://localhost:8000}
RULECAM_DIR=${RULECAM_DIR:-/workspace/rulecam}
TEST_BUDGET=${TEST_BUDGET:-0.5}
WAIT_S=${WAIT_S:-900}
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
env_file="$RULECAM_DIR/.env"
backup="$env_file.budget-check.bak"
started=$(date -u +%Y-%m-%dT%H:%M:%SZ)

budget_now() {
  curl -fs -m 5 "$API/runtime/metrics" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["cost"]["budget_usd_per_camera_hour"])'
}

original_budget=$(budget_now)
cp "$env_file" "$backup"
restore() {
  cp "$backup" "$env_file" && rm -f "$backup"
}
trap restore EXIT

grep -v '^RULECAM_BUDGET_USD_PER_CAMERA_HOUR=' "$backup" > "$env_file"
echo "RULECAM_BUDGET_USD_PER_CAMERA_HOUR=$TEST_BUDGET" >> "$env_file"
bash "$here/restart_check.sh" > /dev/null

"$@" > /dev/null 2>&1 &
feeder=$!

alarm_seen=false
over_budget="[]"
for _ in $(seq 1 "$((WAIT_S / 2))"); do
  sample=$(curl -fs -m 5 "$API/runtime/metrics" || echo '{}')
  if python3 -c 'import json,sys; sys.exit(0 if json.loads(sys.argv[1]).get("cost",{}).get("alarm") else 1)' "$sample"; then
    alarm_seen=true
    over_budget=$(python3 -c 'import json,sys; print(json.dumps(json.loads(sys.argv[1])["cost"]["over_budget"]))' "$sample")
    break
  fi
  kill -0 "$feeder" 2> /dev/null || break
  sleep 2
done
wait "$feeder" || true

restore
trap - EXIT
bash "$here/restart_check.sh" > /dev/null
restored_budget=$(budget_now)

python3 - "$started" "$TEST_BUDGET" "$alarm_seen" "$over_budget" "$original_budget" "$restored_budget" <<'PY'
import json
import sys

started, test_budget, seen, over, original, restored = sys.argv[1:]
passed = seen == "true" and float(restored) == float(original)
print(json.dumps({
    "check": "budget_alarm",
    "passed": passed,
    "started_at": started,
    "test_budget": float(test_budget),
    "alarm_seen": seen == "true",
    "over_budget": json.loads(over),
    "original_budget": float(original),
    "restored_budget": float(restored),
}))
sys.exit(0 if passed else 1)
PY
