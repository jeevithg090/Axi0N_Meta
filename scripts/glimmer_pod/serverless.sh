#!/usr/bin/env bash
# Start command of a Muse Glimmer serverless worker behind a RunPod load balancer (docs/deploy.md,
# "Serverless Glimmer"). The image is the stock llama.cpp server
# (ghcr.io/ggml-org/llama.cpp:server-cuda-b11669, llama-server at /app); the GGUF weights, the
# vision projector, the rulecam code and a standalone Python live on the endpoint's network
# volume (/runpod-volume), staged once with scripts/glimmer_pod/stage.sh.
#
# llama-server listens on localhost only and runs without --api-key. In front of it,
# rulecam.clients.open_weights.llama_edge answers the load balancer's /ping and checks
# GLIMMER_API_KEY (a RunPod secret). RunPod sets PORT; GLIMMER_CTX and GLIMMER_PARALLEL override
# the context and slot count.
set -euo pipefail

VOLUME="${VOLUME:-/runpod-volume}"
MODELS="$VOLUME/glimmer"
PORT="${PORT:-8080}"
UPSTREAM_PORT="${GLIMMER_UPSTREAM_PORT:-8081}"

[[ -n "${GLIMMER_API_KEY:-}" ]] || { echo "serverless.sh: GLIMMER_API_KEY is not set" >&2; exit 1; }
python="$(find "$VOLUME/uv-python" -path '*/bin/python3.12' -type f | head -n 1)"
[[ -n "$python" ]] || { echo "serverless.sh: no Python 3.12 under $VOLUME/uv-python" >&2; exit 1; }

/app/llama-server \
  -m "$MODELS/Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf" \
  --mmproj "$MODELS/mmproj-Muse-Glimmer-30B-Q4_K_M.gguf" \
  -a muse-glimmer-30b -ngl 99 -c "${GLIMMER_CTX:-32768}" -np "${GLIMMER_PARALLEL:-2}" \
  --host 127.0.0.1 --port "$UPSTREAM_PORT" --jinja --temp 1.0 --top-p 0.95 --top-k 64 &

exec env PYTHONPATH="$VOLUME/rulecam/src" "$python" -m rulecam.clients.open_weights.llama_edge \
  --port "$PORT" --upstream "http://127.0.0.1:$UPSTREAM_PORT"
