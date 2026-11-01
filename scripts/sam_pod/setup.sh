#!/usr/bin/env bash
# Self-hosted SAM 3.1 on our own RunPod GPU pod (docs/deploy.md, "Self-hosted SAM 3.1").
#
# Idempotent: installs the GPU venv once (sam3 at the commit pinned in sam_worker.py), downloads
# the gated facebook/sam3.1 checkpoint once, then starts rulecam.perception.sam_server on port
# 8090 unless it already answers /health. `--restart` restarts a running server (after a code
# update). Secrets are read from mode-600 files and never printed or put in argv:
#
#   /workspace/.hf_token      Hugging Face token with access to facebook/sam3.1 (setup only)
#   /workspace/.sam_api_key   Bearer key the API pod sends as SAM_API_KEY
#
# Paths can be overridden with RULECAM_DIR, SAM_VENV, HF_HOME, SAM_LOG, SAM_PORT. Engine settings
# (SAM_MEMORY_GUARD_GB, SAM_COMPILE, SAM_DET_THRESH, SAM_NEW_DET_THRESH, SAM_HOTSTART_*; see
# docs/deploy.md) come from the environment or from /workspace/sam.env (KEY=value lines, no
# secrets), which also survives a reboot through boot.sh.
set -euo pipefail
umask 077

RULECAM_DIR="${RULECAM_DIR:-/workspace/rulecam}"
SAM_VENV="${SAM_VENV:-/workspace/sam-venv}"
export HF_HOME="${HF_HOME:-/workspace/hf-cache}"
# uv's managed Python must live on the volume too: the venv symlinks to it, and the container disk
# (where uv puts it by default) is wiped when the pod stops.
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-/workspace/uv-python}"
SAM_LOG="${SAM_LOG:-/workspace/sam.log}"
SAM_PORT="${SAM_PORT:-8090}"
HF_TOKEN_FILE="${HF_TOKEN_FILE:-/workspace/.hf_token}"
SAM_KEY_FILE="${SAM_KEY_FILE:-/workspace/.sam_api_key}"
SAM_ENV_FILE="${SAM_ENV_FILE:-/workspace/sam.env}"
if [[ -f "$SAM_ENV_FILE" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$SAM_ENV_FILE"
  set +a
fi
WORKER_PY="$RULECAM_DIR/src/rulecam/perception/sam_worker.py"
PY="$SAM_VENV/bin/python"
HEALTH_URL="http://127.0.0.1:$SAM_PORT/health"

log() { printf '%s sam_pod: %s\n' "$(date -u +%FT%TZ)" "$*"; }
die() { log "ERROR: $*"; exit 1; }

secret_file() {  # secret_file <path>: must exist, be non-empty and have mode 600
  [[ -s "$1" ]] || die "$1 is missing or empty (write it with mode 600)"
  [[ "$(stat -c %a "$1")" == "600" ]] && return 0
  chmod 600 "$1" 2>/dev/null || true
  [[ "$(stat -c %a "$1")" == "600" ]] && return 0
  # RunPod network volumes (MooseFS) ignore chmod, so a mode check can never pass there. The
  # pod runs as root only; say so loudly instead of refusing to start.
  local fs
  fs="$(stat -f -c %T "$1" 2>/dev/null || echo unknown)"
  case "$fs" in
    fuseblk | fuse | fuse.* | moosefs | mfs | nfs*)
      log "WARNING: $1 is on a $fs volume that ignores file modes; keep the pod single-user" ;;
    *) die "$1 must have mode 600 (chmod 600 $1)" ;;
  esac
}

pinned() {  # pinned <NAME>: a string constant from sam_worker.py, the single source of truth
  sed -n "s/^$1 = \"\(.*\)\"\$/\1/p" "$WORKER_PY"
}

healthy() { curl -fsS --max-time 3 "$HEALTH_URL" >/dev/null 2>&1; }

install_venv() {
  local commit marker
  commit="$(pinned SAM3_COMMIT)"
  [[ -n "$commit" ]] || die "SAM3_COMMIT not found in $WORKER_PY"
  marker="$SAM_VENV/.rulecam-sam3-$commit"
  if [[ -f "$marker" ]] && "$PY" -c "import sys" >/dev/null 2>&1; then
    log "venv ready (sam3 $commit)"
    return
  fi
  if [[ -e "$SAM_VENV" ]] && ! "$PY" -c "import sys" >/dev/null 2>&1; then
    # e.g. its interpreter lived on the container disk and the pod was restarted
    log "venv at $SAM_VENV has no working Python; rebuilding it"
    rm -rf "$SAM_VENV"
  fi
  command -v uv >/dev/null 2>&1 || python3 -m pip install --quiet --upgrade uv
  [[ -x "$PY" ]] || uv venv --quiet --python 3.12 "$SAM_VENV"
  log "installing torch (cu128) and sam3 $commit into $SAM_VENV"
  uv pip install --quiet --python "$PY" "torch==2.10.0" torchvision \
    --index-url https://download.pytorch.org/whl/cu128
  # sam_worker imports rulecam.perception.masks -> rulecam.contracts, which needs pydantic.
  # sam3 does not declare einops, pycocotools or psutil but imports them (found on a RunPod L4).
  uv pip install --quiet --python "$PY" \
    "sam3 @ git+https://github.com/facebookresearch/sam3.git@$commit" \
    opencv-python-headless "setuptools<81" "numpy<2" "pydantic>=2.7" huggingface_hub \
    einops pycocotools psutil
  PYTHONPATH="$RULECAM_DIR/src" "$PY" -c \
    "import sam3.model_builder, torch, rulecam.perception.sam_server; assert torch.cuda.is_available()" \
    || die "the venv does not import sam3/rulecam or sees no CUDA GPU"
  rm -f "$SAM_VENV"/.rulecam-sam3-*
  touch "$marker"
}

download_checkpoint() {
  if HF_HUB_OFFLINE=1 "$PY" -c \
    "from huggingface_hub import hf_hub_download as d; d('facebook/sam3.1', 'config.json'); d('facebook/sam3.1', 'sam3.1_multiplex.pt')" \
    >/dev/null 2>&1; then
    log "checkpoint cached in $HF_HOME"
    return
  fi
  secret_file "$HF_TOKEN_FILE"
  log "downloading the gated facebook/sam3.1 checkpoint into $HF_HOME"
  (
    HF_TOKEN="$(cat "$HF_TOKEN_FILE")"
    export HF_TOKEN
    "$PY" -c "from huggingface_hub import hf_hub_download as d; d('facebook/sam3.1', 'config.json'); d('facebook/sam3.1', 'sam3.1_multiplex.pt')" >/dev/null
  ) || die "checkpoint download failed: does the token's account have access to facebook/sam3.1?"
}

download_appearance_model() {  # DINOv2 ViT-S/14 for /embed (public, Apache-2.0)
  local fetch="import timm; timm.create_model('vit_small_patch14_dinov2.lvd142m', pretrained=True)"
  if HF_HUB_OFFLINE=1 "$PY" -c "$fetch" >/dev/null 2>&1; then
    log "DINOv2 cached in $HF_HOME"
    return
  fi
  log "downloading DINOv2 (vit_small_patch14_dinov2.lvd142m) into $HF_HOME"
  "$PY" -c "$fetch" >/dev/null || log "WARNING: DINOv2 download failed; /embed will fail"
}

start_server() {
  local restart="$1"
  if [[ "$restart" == "1" ]]; then
    pkill -f "[r]ulecam.perception.sam_server" || true
    sleep 2
  elif healthy; then
    log "server already healthy on :$SAM_PORT"
    return
  fi
  if pgrep -f "[r]ulecam.perception.sam_server" >/dev/null; then
    log "server is still loading; waiting for it"
  else
    secret_file "$SAM_KEY_FILE"
    log "starting sam_server on :$SAM_PORT (log: $SAM_LOG)"
    # The key goes through the environment, never argv. The checkpoint is cached, so the server
    # runs offline and never sees HF_TOKEN.
    SAM_API_KEY="$(cat "$SAM_KEY_FILE")" HF_HUB_OFFLINE=1 PYTHONPATH="$RULECAM_DIR/src" \
      setsid nohup "$PY" -m rulecam.perception.sam_server --host 0.0.0.0 --port "$SAM_PORT" \
      >>"$SAM_LOG" 2>&1 </dev/null &
  fi
  for _ in $(seq 1 120); do  # loading SAM 3.1 takes about a minute
    if healthy; then
      log "server healthy on :$SAM_PORT"
      return
    fi
    sleep 5
  done
  die "server did not become healthy in 10 minutes; see $SAM_LOG"
}

main() {
  local restart=0
  [[ "${1:-}" == "--restart" ]] && restart=1
  [[ -f "$WORKER_PY" ]] || die "no rulecam checkout at $RULECAM_DIR (git archive main there first)"
  command -v nvidia-smi >/dev/null 2>&1 || die "no NVIDIA GPU visible (nvidia-smi missing)"
  install_venv
  download_checkpoint
  download_appearance_model
  start_server "$restart"
}

main "$@"
