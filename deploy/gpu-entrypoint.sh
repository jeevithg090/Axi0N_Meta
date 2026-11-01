#!/usr/bin/env bash
# One-GPU pod entrypoint (deploy/gpu.Dockerfile; docs/deploy.md "One GPU per site").
#
#   gpu-entrypoint.sh [serve]      read the secrets, then run gpu_supervisor.py, which keeps
#                                  llama-server (Muse Glimmer, :8080) and sam_server (:8090) up
#   gpu-entrypoint.sh healthcheck  exit 0 when both /health answer
#   gpu-entrypoint.sh plan         print both commands (no secrets)
#
# Both servers share the pod volume (GPU_HOME, default /workspace): the GGUF files in
# LLAMA_CACHE (/workspace/llama-cache) and the SAM checkpoint in HF_HOME (/workspace/hf-cache).
#
# Secrets (environment first, e.g. a RunPod secret, else a mode-600 file; never logged):
#   LLAMA_API_KEY or LLAMA_KEY_FILE (/workspace/.llama_api_key)  required: Glimmer's Bearer key
#   SAM_API_KEY   or SAM_KEY_FILE   (/workspace/.sam_api_key)    required: SAM's Bearer key
#   HF_TOKEN      or HF_TOKEN_FILE  (/workspace/.hf_token)       only until the checkpoint is cached
# The supervisor hands each server its own key only. SAM engine settings (SAM_MEMORY_GUARD_GB,
# SAM_DET_THRESH, …) and Glimmer's GLIMMER_CTX / GLIMMER_PARALLEL come from the environment or
# from SAM_ENV_FILE (/workspace/sam.env, KEY=value lines, no secrets).
set -euo pipefail
umask 077

ENTRYPOINT_NAME="rulecam-gpu"
# shellcheck source-path=SCRIPTDIR
# shellcheck source=entrypoint-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/entrypoint-lib.sh"

GPU_HOME="${GPU_HOME:-/workspace}"
GPU_USER="${GPU_USER:-sam}"
GLIMMER_PORT="${GLIMMER_PORT:-8080}"
SAM_PORT="${SAM_PORT:-8090}"
LLAMA_KEY_FILE="${LLAMA_KEY_FILE:-$GPU_HOME/.llama_api_key}"
SAM_KEY_FILE="${SAM_KEY_FILE:-$GPU_HOME/.sam_api_key}"
HF_TOKEN_FILE="${HF_TOKEN_FILE:-$GPU_HOME/.hf_token}"
SAM_ENV_FILE="${SAM_ENV_FILE:-$GPU_HOME/sam.env}"
GPU_PYTHON="${GPU_PYTHON:-python3}"
GPU_SUPERVISOR="${GPU_SUPERVISOR:-$(dirname "${BASH_SOURCE[0]}")/gpu_supervisor.py}"
export GLIMMER_PORT SAM_PORT
export SAM_HOME="$GPU_HOME"  # sam-entrypoint.sh _serve uses the same volume
export HF_HOME="${HF_HOME:-$GPU_HOME/hf-cache}"
export LLAMA_CACHE="${LLAMA_CACHE:-$GPU_HOME/llama-cache}"

main() {
  case "${1:-serve}" in
    healthcheck)
      http_ok "http://127.0.0.1:${GLIMMER_PORT}/health"
      http_ok "http://127.0.0.1:${SAM_PORT}/health"
      ;;
    plan)
      exec "$GPU_PYTHON" "$GPU_SUPERVISOR" --plan
      ;;
    serve)
      if [[ -f "$SAM_ENV_FILE" ]]; then
        set -a
        # shellcheck source=/dev/null
        source "$SAM_ENV_FILE"
        set +a
      fi
      load_secret LLAMA_API_KEY "$LLAMA_KEY_FILE"
      [[ -n "${LLAMA_API_KEY:-}" ]] || die "no LLAMA_API_KEY (set it or write $LLAMA_KEY_FILE with mode 600)"
      load_secret SAM_API_KEY "$SAM_KEY_FILE"
      [[ -n "${SAM_API_KEY:-}" ]] || die "no SAM_API_KEY (set it or write $SAM_KEY_FILE with mode 600)"
      load_secret HF_TOKEN "$HF_TOKEN_FILE"
      mkdir -p "$GPU_HOME" "$HF_HOME" "$LLAMA_CACHE"
      if [[ "$(id -u)" == "0" ]] && id "$GPU_USER" >/dev/null 2>&1; then
        chown "$GPU_USER:" "$HF_HOME" "$LLAMA_CACHE" 2>/dev/null || true  # caches made just now
      fi
      log "starting the supervisor (glimmer :$GLIMMER_PORT, sam :$SAM_PORT)"
      drop_privileges "$GPU_USER" "$GPU_HOME" "$GPU_PYTHON" "$GPU_SUPERVISOR"
      ;;
    *)
      die "unknown command: $1 (serve | healthcheck | plan)"
      ;;
  esac
}

main "$@"
