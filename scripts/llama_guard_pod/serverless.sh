#!/usr/bin/env bash
# Start command of a Llama Guard 4 (12B) worker behind a RunPod serverless load balancer
# (docs/deploy.md, "Serverless Llama Guard 4"): the plan-time screen that refuses rules about who
# people are (`rulecam.clients.open_weights.llama_guard`). The image is the stock vLLM OpenAI
# server (vllm/vllm-openai:v0.32.0); the FP8 checkpoint RedHatAI/Llama-Guard-4-12B-FP8-dynamic
# (15 GB, ungated, a quantisation of meta-llama/Llama-Guard-4-12B) downloads to the container disk
# on a cold start, so the endpoint needs no network volume and can run in any data center.
#
# This script reaches the worker as the gzip+base64 env var LLAMA_GUARD_BOOT_GZ_B64 and the edge
# proxy (rulecam.clients.open_weights.llama_edge, standard library only) as LLAMA_GUARD_EDGE_GZ_B64
# (scripts/llama_guard_pod/endpoint_env.sh prints both). llama_edge answers RunPod's /ping (204
# while vLLM loads, 200 once its /health answers) and checks LLAMA_GUARD_API_KEY in X-Rulecam-Key.
set -euo pipefail

PORT="${PORT:-8080}"
UPSTREAM_PORT=8081
MODEL="${LLAMA_GUARD_CHECKPOINT:-RedHatAI/Llama-Guard-4-12B-FP8-dynamic}"
WORK=/tmp/llama-guard

log() { printf '%s llama_guard: %s\n' "$(date -u +%FT%TZ)" "$*"; }

[[ -n "${LLAMA_GUARD_API_KEY:-}" ]] || { log "LLAMA_GUARD_API_KEY is not set"; exit 1; }
mkdir -p "$WORK"
printf '%s' "${LLAMA_GUARD_EDGE_GZ_B64:-}" | base64 -d | gunzip > "$WORK/llama_edge.py"
GLIMMER_API_KEY="$LLAMA_GUARD_API_KEY" python3 "$WORK/llama_edge.py" \
  --port "$PORT" --upstream "http://127.0.0.1:$UPSTREAM_PORT" &
log "llama_edge on :$PORT (pid $!)"

# shellcheck disable=SC2086  # LLAMA_GUARD_EXTRA_ARGS is split on purpose
vllm serve "$MODEL" --served-model-name llama-guard-4-12b \
  --host 127.0.0.1 --port "$UPSTREAM_PORT" --max-model-len "${LLAMA_GUARD_MAX_LEN:-4096}" \
  --gpu-memory-utilization "${LLAMA_GUARD_GPU_UTIL:-0.92}" --max-num-seqs 8 --enforce-eager \
  ${LLAMA_GUARD_EXTRA_ARGS:-} &
log "vllm starting $MODEL (pid $!)"

wait -n
log "a process exited; stopping the worker"
exit 1
