#!/usr/bin/env bash
# Start command of a Muse Spark Open worker behind a RunPod serverless load balancer
# (docs/deploy.md, "Serverless Muse Spark Open"). Muse Spark Open is Meta's 816B-parameter
# mixture-of-experts model (256 experts, about 42B active per token); the 4-bit GGUF
# (ggml-org/Muse-Spark-Open-816B-A42B-GGUF, Q4_K_S, 470 GB in 22 parts) runs on one worker with
# 8 x H100 80 GB under the stock llama.cpp image (ghcr.io/ggml-org/llama.cpp:full-cuda-b12004;
# the first build with the `muse-spark` architecture is b11681, and the `full` image carries the
# python3 that llama_edge needs).
#
# The endpoint has no code volume to read from, so this script reaches the worker as the
# gzip+base64 env var SPARK_OPEN_BOOT_GZ_B64, and the edge proxy (rulecam.clients.open_weights.llama_edge,
# standard library only) as the gzip+base64 env var SPARK_OPEN_EDGE_GZ_B64; see
# scripts/spark_open_pod/endpoint_env.sh, which prints both.
#
# What it does, in order:
# 1. starts llama_edge on $PORT at once, so RunPod's /ping gets 204 (loading) during steps 2-3;
# 2. downloads the weights onto the network volume the first time (resumable, 8 in parallel,
#    each part checked by size; later cold starts skip this);
# 3. starts llama-server on localhost; llama_edge answers 200 once it is ready.
# The worker stops (and RunPod replaces it) when either process exits.
#
# Env: SPARK_OPEN_API_KEY (RunPod secret; every route but /ping needs it as X-Rulecam-Key),
# SPARK_OPEN_QUANT (Q4_K_S), SPARK_OPEN_CTX (131072, shared by the slots),
# SPARK_OPEN_PARALLEL (4), SPARK_OPEN_MMPROJ (0; 1 loads the vision projector),
# SPARK_OPEN_EXTRA_ARGS (more llama-server flags).
set -euo pipefail

VOLUME="${VOLUME:-/runpod-volume}"
DIR="$VOLUME/spark-open"
QUANT="${SPARK_OPEN_QUANT:-Q4_K_S}"
PARTS=22
STEM="Muse-Spark-Open-816B-A42B-$QUANT"
MMPROJ="mmproj-Muse-Spark-Open-816B-A42B-Q8_0.gguf"
REPO="https://huggingface.co/ggml-org/Muse-Spark-Open-816B-A42B-GGUF/resolve/main"
PORT="${PORT:-8080}"
UPSTREAM_PORT=8081

log() { printf '%s spark_open: %s\n' "$(date -u +%FT%TZ)" "$*"; }

[[ -n "${SPARK_OPEN_API_KEY:-}" ]] || { log "SPARK_OPEN_API_KEY is not set"; exit 1; }
mkdir -p "$DIR"

if [[ -n "${SPARK_OPEN_EDGE_GZ_B64:-}" ]]; then
  printf '%s' "$SPARK_OPEN_EDGE_GZ_B64" | base64 -d | gunzip > "$DIR/llama_edge.py"
fi
[[ -s "$DIR/llama_edge.py" ]] || { log "no llama_edge.py (set SPARK_OPEN_EDGE_GZ_B64)"; exit 1; }
GLIMMER_API_KEY="$SPARK_OPEN_API_KEY" python3 "$DIR/llama_edge.py" \
  --port "$PORT" --upstream "http://127.0.0.1:$UPSTREAM_PORT" &
log "llama_edge on :$PORT (pid $!)"

# shellcheck disable=SC2329  # called through xargs below
fetch() {  # fetch <file name>: download one file into $DIR unless it is there in full
  local name="$1" url size
  url="$REPO/$name"
  size="$(curl -fsSIL "$url" | tr -d '\r' | awk 'tolower($1) == "content-length:" {n = $2} END {print n}')"
  if [[ -f "$DIR/$name" && "$(stat -c %s "$DIR/$name")" == "$size" ]]; then
    return 0
  fi
  log "downloading $name ($size bytes)"
  curl -fsSL --retry 5 --retry-delay 5 -C - -o "$DIR/$name.part" "$url"
  [[ "$(stat -c %s "$DIR/$name.part")" == "$size" ]] || { log "ERROR: $name is incomplete"; return 1; }
  mv "$DIR/$name.part" "$DIR/$name"
}
export -f fetch log
export DIR REPO

if [[ ! -f "$DIR/.complete-$QUANT" ]]; then
  started=$(date +%s)
  names=("$MMPROJ")
  for i in $(seq 1 "$PARTS"); do names+=("$(printf '%s-%05d-of-%05d.gguf' "$STEM" "$i" "$PARTS")"); done
  printf '%s\n' "${names[@]}" | xargs -P 8 -I{} bash -c 'fetch "$@"' _ {}
  touch "$DIR/.complete-$QUANT"
  log "weights staged in $(( $(date +%s) - started )) s ($(du -sh "$DIR" | cut -f1))"
fi

mm=()
[[ "${SPARK_OPEN_MMPROJ:-0}" == "1" ]] && mm=(--mmproj "$DIR/$MMPROJ")
# shellcheck disable=SC2086  # SPARK_OPEN_EXTRA_ARGS is split on purpose
/app/llama-server \
  -m "$DIR/$(printf '%s-%05d-of-%05d.gguf' "$STEM" 1 "$PARTS")" "${mm[@]}" \
  -a muse-spark-open -ngl 999 -c "${SPARK_OPEN_CTX:-131072}" -np "${SPARK_OPEN_PARALLEL:-4}" \
  --host 127.0.0.1 --port "$UPSTREAM_PORT" --jinja --temp 1.0 --top-p 1.0 --top-k 64 \
  ${SPARK_OPEN_EXTRA_ARGS:-} &
log "llama-server starting (pid $!)"

wait -n
log "a process exited; stopping the worker"
exit 1
