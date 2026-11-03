#!/usr/bin/env bash
# Add MoGe-2 (add-on 3.5, `POST /geometry`) to the SAM 3.1 GPU venv and cache its weights.
#
# Run once where the SAM venv lives, after scripts/sam_pod/setup.sh. For the serverless endpoint,
# from a pod that mounts the network volume at /runpod-volume (no GPU needed):
#
#   SAM_VENV=/runpod-volume/sam-venv HF_HOME=/runpod-volume/hf-cache \
#     scripts/sam_pod/install_moge.sh
#
# then copy the new src onto the volume and let workers restart (docs/deploy.md). The server
# serves /geometry only when `moge` imports, and /health reports it under `geometry`.
#
# MoGe is pinned to the last MoGe-2 commit before MoGe-3 (whose package needs numpy >= 2, which
# sam3 cannot use). Installed with --no-deps so pip never touches torch, numpy or OpenCV in the
# SAM venv; the only extra runtime dependencies are utils3d (pinned as MoGe pins it) and scipy.
# Weights: Ruicheng/moge-2-vitl-normal model.pt (1,323,815,904 bytes, MIT), public, no token.
set -euo pipefail

SAM_VENV="${SAM_VENV:-/workspace/sam-venv}"
export HF_HOME="${HF_HOME:-/workspace/hf-cache}"
PY="$SAM_VENV/bin/python"
MOGE_COMMIT="b942f00bdc2a2a23ebb474fbe034d487e6dcceec"
UTILS3D_COMMIT="3fab839f0be9931dac7c8488eb0e1600c236e183"
MOGE_REPO="Ruicheng/moge-2-vitl-normal"
MOGE_SHA256="280741fd09bc3f403ccff9967784c2a391b52d2c0742ae3efdb21d9f90cc1a01"

log() { printf '%s install_moge: %s\n' "$(date -u +%FT%TZ)" "$*"; }
[[ -x "$PY" ]] || { log "no venv at $SAM_VENV (run scripts/sam_pod/setup.sh first)"; exit 1; }
command -v uv >/dev/null 2>&1 || python3 -m pip install --quiet --upgrade uv

log "installing moge@$MOGE_COMMIT and utils3d@$UTILS3D_COMMIT into $SAM_VENV"
uv pip install --quiet --python "$PY" --no-deps \
  "moge @ git+https://github.com/microsoft/MoGe.git@$MOGE_COMMIT" \
  "utils3d @ git+https://github.com/EasternJournalist/utils3d.git@$UTILS3D_COMMIT"
uv pip install --quiet --python "$PY" scipy

log "caching $MOGE_REPO model.pt into $HF_HOME"
path="$("$PY" -c "from huggingface_hub import hf_hub_download as d; print(d('$MOGE_REPO', 'model.pt'))")"
actual="$(sha256sum "$path" | cut -d' ' -f1)"
[[ "$actual" == "$MOGE_SHA256" ]] || { log "model.pt sha256 $actual, expected $MOGE_SHA256"; exit 1; }

"$PY" -c "from moge.model.v2 import MoGeModel; import numpy; assert numpy.__version__ < '2'" \
  || { log "moge does not import, or numpy was upgraded past 1.x"; exit 1; }
log "MoGe-2 ready: /geometry will be served after the next sam_server restart"
