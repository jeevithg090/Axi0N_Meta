#!/usr/bin/env bash
# Run `scripts/ops/warm.sh keepalive` once a day so RunPod never shrinks the idle GPU endpoints.
# Meant for the API pod, started detached so it outlives the SSH session:
#
#   setsid nohup bash /workspace/rulecam/scripts/ops/keepalive_loop.sh \
#     >> /workspace/keepalive.log 2>&1 < /dev/null &
#
# The RunPod key comes from RUNPOD_API_KEY, else from the file RUNPOD_API_KEY_FILE (default
# /workspace/rulecam/.runpod_api_key, mode 600); it is never printed. INTERVAL_S (86400) is the
# wait between runs and DAYS (16, past the Nov 9 finals) how many runs before it exits.
# A failed run (a loud warning in the log) does not stop the loop.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INTERVAL_S=${INTERVAL_S:-86400}
DAYS=${DAYS:-16}
KEY_FILE=${RUNPOD_API_KEY_FILE:-/workspace/rulecam/.runpod_api_key}

if [ -z "${RUNPOD_API_KEY:-}" ]; then
  if [ ! -r "$KEY_FILE" ]; then
    echo "keepalive_loop: set RUNPOD_API_KEY or RUNPOD_API_KEY_FILE ($KEY_FILE is not readable)" >&2
    exit 2
  fi
  RUNPOD_API_KEY=$(tr -d '[:space:]' < "$KEY_FILE")
fi
export RUNPOD_API_KEY

for day in $(seq 1 "$DAYS"); do
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) keepalive $day/$DAYS"
  bash "$here/warm.sh" keepalive || echo "keepalive $day/$DAYS failed (exit $?)"
  if [ "$day" -lt "$DAYS" ]; then sleep "$INTERVAL_S"; fi
done
