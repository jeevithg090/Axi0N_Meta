#!/usr/bin/env bash
# Self-hosted SAM 3.1 container entrypoint (deploy/sam.Dockerfile; docs/deploy.md).
#
#   sam-entrypoint.sh [serve]      fill the checkpoint cache once, then run sam_server
#   sam-entrypoint.sh healthcheck  exit 0 when GET /health answers
#
# The image already holds the venv (torch cu128, sam3 at the pinned commit, its undeclared
# deps), so a restart never reinstalls anything. Only the gated facebook/sam3.1 checkpoint is
# fetched, once, into HF_HOME on the pod volume (default /workspace/hf-cache); the server then
# runs with HF_HUB_OFFLINE=1 and never sees the Hugging Face token.
#
# Secrets (environment first, e.g. a RunPod secret, else a mode-600 file; never logged):
#   SAM_API_KEY  or SAM_KEY_FILE   (/workspace/.sam_api_key)  required: the API pod's Bearer key
#   HF_TOKEN     or HF_TOKEN_FILE  (/workspace/.hf_token)     only until the checkpoint is cached
# Engine settings (SAM_MEMORY_GUARD_GB, SAM_DET_THRESH, …) come from the environment or from
# SAM_ENV_FILE (/workspace/sam.env, KEY=value lines, no secrets).
set -euo pipefail
umask 077

ENTRYPOINT_NAME="rulecam-sam"
# shellcheck source-path=SCRIPTDIR
# shellcheck source=entrypoint-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/entrypoint-lib.sh"

SAM_HOME="${SAM_HOME:-/workspace}"
SAM_PORT="${SAM_PORT:-8090}"
SAM_USER="${SAM_USER:-sam}"
SAM_KEY_FILE="${SAM_KEY_FILE:-$SAM_HOME/.sam_api_key}"
HF_TOKEN_FILE="${HF_TOKEN_FILE:-$SAM_HOME/.hf_token}"
SAM_ENV_FILE="${SAM_ENV_FILE:-$SAM_HOME/sam.env}"
export HF_HOME="${HF_HOME:-$SAM_HOME/hf-cache}"
SAM_PYTHON="${SAM_PYTHON:-python3}"
CHECKPOINT_FILES="config.json sam3.1_multiplex.pt"
# sam_worker.REID_WEIGHTS_URL: osnet_ain_x1_0_msmt17.pt (BoxMOT's catalogue, Google Drive)
REID_WEIGHTS_URL="https://drive.google.com/uc?id=1SigwBE6mPdqiJMqhuIY4aqC7--5CsMal"

checkpoint_cached() {
  # shellcheck disable=SC2086  # the file list is split on purpose
  HF_HUB_OFFLINE=1 "$SAM_PYTHON" - $CHECKPOINT_FILES >/dev/null 2>&1 <<'PY'
import sys

from huggingface_hub import hf_hub_download

for name in sys.argv[1:]:
    hf_hub_download("facebook/sam3.1", name)
PY
}

download_checkpoint() {
  if checkpoint_cached; then
    log "checkpoint cached in $HF_HOME"
    return 0
  fi
  [[ -n "${HF_TOKEN:-}" ]] || die "checkpoint not cached and no Hugging Face token (set HF_TOKEN or write $HF_TOKEN_FILE)"
  log "downloading the gated facebook/sam3.1 checkpoint into $HF_HOME"
  # shellcheck disable=SC2086
  "$SAM_PYTHON" - $CHECKPOINT_FILES >/dev/null <<'PY' || die "checkpoint download failed: does the token's account have access to facebook/sam3.1?"
import sys

from huggingface_hub import hf_hub_download

for name in sys.argv[1:]:
    hf_hub_download("facebook/sam3.1", name)
PY
}

download_reid_weights() {  # OSNet-AIN for person re-ID; without it SAM serves boxes only
  local weights="${SAM_REID_WEIGHTS:-$(dirname "$HF_HOME")/reid/osnet_ain_x1_0_msmt17.pt}"
  [[ -s "$weights" || "${SAM_REID:-1}" == "0" ]] && return 0
  mkdir -p "$(dirname "$weights")"
  log "downloading OSNet-AIN re-ID weights into $weights"
  "$SAM_PYTHON" -m gdown --quiet "$REID_WEIGHTS_URL" -O "$weights" \
    || log "WARNING: re-ID weights download failed; people are tracked without appearance"
}

serve() {  # runs as the service user: the cache it fills is its own
  umask 022
  mkdir -p "$HF_HOME"
  download_checkpoint
  download_reid_weights
  unset HF_TOKEN
  export HF_HUB_OFFLINE=1
  log "starting sam_server on :$SAM_PORT"
  exec "$SAM_PYTHON" -m rulecam.perception.sam_server --host 0.0.0.0 --port "$SAM_PORT"
}

main() {
  case "${1:-serve}" in
    healthcheck)
      http_ok "http://127.0.0.1:${SAM_PORT}/health"
      ;;
    serve)
      if [[ -f "$SAM_ENV_FILE" ]]; then
        set -a
        # shellcheck source=/dev/null
        source "$SAM_ENV_FILE"
        set +a
      fi
      load_secret SAM_API_KEY "$SAM_KEY_FILE"
      [[ -n "${SAM_API_KEY:-}" ]] || die "no SAM_API_KEY (set it or write $SAM_KEY_FILE with mode 600)"
      load_secret HF_TOKEN "$HF_TOKEN_FILE"
      mkdir -p "$SAM_HOME"
      drop_privileges "$SAM_USER" "$HF_HOME" "$0" _serve
      ;;
    _serve)
      serve
      ;;
    *)
      die "unknown command: $1 (serve | healthcheck)"
      ;;
  esac
}

main "$@"
