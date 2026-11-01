#!/usr/bin/env bash
# Boot hook of the archive fallback API pod (runpod/base image + `git archive` on the volume;
# `runpod_up.sh --api-source archive`). The pod's start command is
#   bash -c "(/workspace/rulecam/deploy/api-boot.sh &) ; exec /start.sh"
# so SSH comes up at once while this syncs the locked deps and (re)starts the API. Idempotent:
# safe on every boot and after each code upload. Log: /workspace/api.log.
set -euo pipefail

export RULECAM_HOME="${RULECAM_HOME:-/workspace/rulecam}"
# uv's Python, cache and the venv must live on the volume: the container disk is wiped on stop.
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-/workspace/uv-python}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/workspace/uv-cache}"
export UV_PROJECT_ENVIRONMENT="$RULECAM_HOME/.venv"
LOG="${RULECAM_API_LOG:-/workspace/api.log}"

exec >>"$LOG" 2>&1
cd "$RULECAM_HOME" 2>/dev/null || { echo "api-boot: no code in $RULECAM_HOME yet"; exit 0; }
[[ -f pyproject.toml ]] || { echo "api-boot: no code in $RULECAM_HOME yet"; exit 0; }
command -v uv >/dev/null 2>&1 || python3 -m pip install --quiet --upgrade uv
uv sync --frozen --no-dev
pkill -f "[u]vicorn rulecam.api.app" || true
sleep 2
export PATH="$UV_PROJECT_ENVIRONMENT/bin:$PATH"
exec setsid "$RULECAM_HOME/deploy/api-entrypoint.sh" serve
