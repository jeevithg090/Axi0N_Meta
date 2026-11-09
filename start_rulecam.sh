#!/usr/bin/env bash
# Local launcher for Rulecam on macOS / Linux (the bash twin of start_rulecam.ps1).
#
# Starts the FastAPI backend on http://localhost:8000 and the Next.js site on
# http://localhost:3000, seeded with hand-made SAMPLE data (scripts/seed_live_backend.py).
# This is NOT a live run: never record a demo video or quote a number from what it shows.
# For a recorded live run with no keys, open http://localhost:3000/demo?demo=1.
#
#   ./start_rulecam.sh            # install if needed, seed, start both, Ctrl+C stops both
#   ./start_rulecam.sh --no-seed  # skip the sample data
#
# Needs: uv (Python 3.12), Node 20+, ffmpeg. Logs go to data/logs/local-{seed,api,ui}.log.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

SEED=1
for arg in "$@"; do
  case "$arg" in
    --no-seed) SEED=0 ;;
    -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

API_PORT=8000
UI_PORT=3000
API_URL="http://localhost:${API_PORT}"
UI_URL="http://localhost:${UI_PORT}"
LOG_DIR="$ROOT/data/logs"
mkdir -p "$LOG_DIR"

say() { printf '\033[36m==> %s\033[0m\n' "$*"; }
ok()  { printf '\033[32m==> %s\033[0m\n' "$*"; }
die() { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# --- 1. Prerequisites -------------------------------------------------------------------------
say "[1/5] Checking prerequisites..."
command -v uv >/dev/null     || die "uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
command -v node >/dev/null   || die "Node not found. Install Node 20+ (https://nodejs.org or 'brew install node')."
command -v npm >/dev/null    || die "npm not found (comes with Node)."
command -v ffmpeg >/dev/null || die "ffmpeg not found. Install: 'brew install ffmpeg' or 'sudo apt install ffmpeg'."
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
[ "$NODE_MAJOR" -ge 20 ] || die "Node $NODE_MAJOR found; Rulecam's site needs Node 20+."
[ -f .env ] || { cp .env.example .env; ok "Created .env from .env.example."; }

# --- 2. Dependencies (first run only) ---------------------------------------------------------
say "[2/5] Installing dependencies if needed..."
[ -d .venv ] || uv sync --group dev
[ -d ui/node_modules ] || (cd ui && npm ci)

up() { curl -fs -o /dev/null --max-time 3 "$1"; }
wait_for() {  # url, seconds, name, log
  for _ in $(seq "$2"); do up "$1" && return 0; sleep 1; done
  die "$3 did not come up within $2 s. See $4"
}

PIDS=()
kill_tree() {  # npm -> next -> next-server: stop the children first, then the process
  local child
  for child in $(pgrep -P "$1" 2>/dev/null); do kill_tree "$child"; done
  kill "$1" 2>/dev/null || true
}
cleanup() {
  trap - EXIT INT TERM
  [ "${#PIDS[@]}" -eq 0 ] && return
  echo; say "Stopping Rulecam..."
  for pid in "${PIDS[@]}"; do kill_tree "$pid"; done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# --- 3. Sample data ---------------------------------------------------------------------------
if [ "$SEED" -eq 1 ]; then
  say "[3/5] Seeding hand-made SAMPLE data (not a live run)..."
  PYTHONIOENCODING=utf-8 uv run python scripts/seed_live_backend.py >"$LOG_DIR/local-seed.log" 2>&1 \
    || die "Seeding failed. See data/logs/local-seed.log"
else
  say "[3/5] Skipping sample data (--no-seed)."
fi

# --- 4. API -----------------------------------------------------------------------------------
export RULECAM_RUNTIME=true PYTHONIOENCODING=utf-8
if up "$API_URL/health"; then
  ok "[4/5] API already running at $API_URL."
else
  say "[4/5] Starting the API at $API_URL (log: data/logs/local-api.log)..."
  uv run uvicorn rulecam.api.app:app --host 127.0.0.1 --port "$API_PORT" \
    >"$LOG_DIR/local-api.log" 2>&1 &
  PIDS+=($!)
  wait_for "$API_URL/health" 40 "The API" "data/logs/local-api.log"
fi

# The API keeps plans in memory: register the plans on disk and approve the sample master plan.
if [ "$SEED" -eq 1 ]; then
  uv run python - "$API_URL" <<'PY' || echo "    (plan registration skipped; see the API log)"
import sys
from pathlib import Path

import httpx
import yaml

api = sys.argv[1]
for p in sorted(Path("plans").glob("*.plan.yaml")):
    httpx.post(f"{api}/plans", json=yaml.safe_load(p.read_text(encoding="utf-8")), timeout=4.0)
httpx.post(f"{api}/plans/plan-c914e72a03f5/approve", timeout=4.0)
PY
fi

# --- 5. Site ----------------------------------------------------------------------------------
if up "$UI_URL"; then
  ok "[5/5] Site already running at $UI_URL."
else
  say "[5/5] Starting the site at $UI_URL (log: data/logs/local-ui.log; first load compiles)..."
  # Point the site at the local API, overriding any tunnel URL in ui/.env.local.
  (cd ui && NEXT_PUBLIC_RULECAM_API="$API_URL" npm run dev -- --port "$UI_PORT") \
    >"$LOG_DIR/local-ui.log" 2>&1 &
  PIDS+=($!)
  wait_for "$UI_URL" 90 "The site" "data/logs/local-ui.log"
fi

ok "Rulecam is running locally on SAMPLE data (not a live run)."
cat <<EOF
    Replay (recorded live run) : $UI_URL/demo?demo=1
    Cockpit                    : $UI_URL/monitor
    Camera fleet               : $UI_URL/streams
    Rule planner               : $UI_URL/planner
    Merkle audit               : $UI_URL/audit
    API health                 : $API_URL/health
EOF

if [ "${#PIDS[@]}" -eq 0 ]; then
  echo "Both were already running; nothing to stop."
  exit 0
fi
echo "Press Ctrl+C to stop."
wait
