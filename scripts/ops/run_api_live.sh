#!/usr/bin/env bash
# Start the Rulecam API on this Mac with the live settings in .env.live (docs/deploy.md,
# "Meta API only"). The login service (scripts/ops/mac_services.py) runs it, and it works by hand:
#
#   bash scripts/ops/run_api_live.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
set -a
# shellcheck disable=SC1091 # the live settings, kept out of git
. ./.env.live
set +a
ulimit -n 65536 2>/dev/null || ulimit -n 10240
exec uv run uvicorn rulecam.api.app:app --host 127.0.0.1 --port 8000
