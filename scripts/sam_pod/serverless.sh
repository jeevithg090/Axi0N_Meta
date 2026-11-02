#!/usr/bin/env bash
# Start command of a SAM 3.1 serverless worker behind a RunPod load balancer (docs/deploy.md,
# "Serverless SAM").
#
# Everything runs from the endpoint's network volume, which workers see at /runpod-volume: the
# code, the venv and the cached checkpoints (SAM 3.1 and DINOv2). Stage them once from a pod
# that mounts the same volume at the same path (no GPU needed):
#
#   RULECAM_DIR=/runpod-volume/rulecam SAM_VENV=/runpod-volume/sam-venv \
#     HF_HOME=/runpod-volume/hf-cache UV_PYTHON_INSTALL_DIR=/runpod-volume/uv-python \
#     HF_TOKEN_FILE=/runpod-volume/.hf_token scripts/sam_pod/setup.sh --stage
#
# The worker runs offline and never sees a Hugging Face token. SAM_API_KEY comes from a RunPod
# secret; RunPod sets PORT. Engine settings can go in /runpod-volume/sam.env (no secrets).
set -euo pipefail

VOLUME="${VOLUME:-/runpod-volume}"
export HF_HOME="$VOLUME/hf-cache" HF_HUB_OFFLINE=1 PYTHONPATH="$VOLUME/rulecam/src"

[[ -n "${SAM_API_KEY:-}" ]] || { echo "serverless.sh: SAM_API_KEY is not set" >&2; exit 1; }
if [[ -f "$VOLUME/sam.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$VOLUME/sam.env"
  set +a
fi
exec "$VOLUME/sam-venv/bin/python" -m rulecam.perception.sam_server \
  --host 0.0.0.0 --port "${PORT:-8090}"
