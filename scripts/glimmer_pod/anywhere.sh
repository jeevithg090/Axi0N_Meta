#!/usr/bin/env bash
# Start command of the standby Muse Glimmer endpoint (docs/deploy.md, "Standby Glimmer"): the
# same llama.cpp build and weights as scripts/glimmer_pod/serverless.sh, but with no network
# volume, so RunPod can place it in any data center and on any 24-80 GB GPU that has stock. The
# weights (17 GB GGUF + 1.4 GB vision projector, public) download to the container disk on each
# cold start, about 2-4 minutes. The image is ghcr.io/ggml-org/llama.cpp:full-cuda-b11669 (the
# `full` image has the python3 that llama_edge needs).
#
# This script reaches the worker as the gzip+base64 env var GLIMMER_BOOT_GZ_B64 and llama_edge
# as GLIMMER_EDGE_GZ_B64 (scripts/glimmer_pod/anywhere_env.sh prints both). GLIMMER_API_KEY (the
# RunPod secret) is checked in X-Rulecam-Key, as on the primary endpoint.
set -euo pipefail

PORT="${PORT:-8080}"
UPSTREAM_PORT=8081
DIR="${GLIMMER_DIR:-/models}"
REPO="https://huggingface.co/meta-models/Muse-Glimmer-30B-GGUF/resolve/main"
MODEL="Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf"
MMPROJ="mmproj-Muse-Glimmer-30B-Q4_K_M.gguf"

log() { printf '%s glimmer_anywhere: %s\n' "$(date -u +%FT%TZ)" "$*"; }

[[ -n "${GLIMMER_API_KEY:-}" ]] || { log "GLIMMER_API_KEY is not set"; exit 1; }
mkdir -p "$DIR"
printf '%s' "${GLIMMER_EDGE_GZ_B64:-}" | base64 -d | gunzip > "$DIR/llama_edge.py"
python3 "$DIR/llama_edge.py" --port "$PORT" --upstream "http://127.0.0.1:$UPSTREAM_PORT" &
log "llama_edge on :$PORT (pid $!)"

fetch() {  # fetch <file>: download until the file has the size the CDN reports
  local name="$1" url size have attempt
  url="$REPO/$name"
  size="$(curl -fsSIL "$url" | tr -d '\r' | awk 'tolower($1) == "content-length:" {n = $2} END {print n}')"
  for attempt in $(seq 1 20); do
    have="$(stat -c %s "$DIR/$name" 2>/dev/null || echo 0)"
    [[ "$have" == "$size" ]] && return 0
    log "$name: $have of $size bytes (attempt $attempt)"
    # A stalled transfer (under 2 MB/s for 30 s) is dropped and resumed with a fresh CDN link.
    curl -fsSL --retry 3 --speed-limit 2000000 --speed-time 30 -C - -o "$DIR/$name" "$url" \
      || sleep 2
  done
  log "ERROR: $name is incomplete"
  return 1
}

started=$(date +%s)
# Hugging Face's Xet client fetches chunks in parallel (several times faster than one curl);
# plain curl is the fallback when it cannot be installed.
if pip install -q --root-user-action=ignore "huggingface_hub[hf_xet]>=0.34" 2>/dev/null \
  && HF_XET_HIGH_PERFORMANCE=1 hf download meta-models/Muse-Glimmer-30B-GGUF \
    "$MODEL" "$MMPROJ" --local-dir "$DIR" > /dev/null; then
  log "downloaded with hf"
else
  fetch "$MMPROJ" &
  fetch "$MODEL"
  wait
fi
log "weights ready in $(( $(date +%s) - started )) s"

exec /app/llama-server -m "$DIR/$MODEL" --mmproj "$DIR/$MMPROJ" \
  -a muse-glimmer-30b -ngl 99 -c "${GLIMMER_CTX:-32768}" -np "${GLIMMER_PARALLEL:-2}" \
  --host 127.0.0.1 --port "$UPSTREAM_PORT" --jinja --temp 1.0 --top-p 0.95 --top-k 64
