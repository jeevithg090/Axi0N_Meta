#!/usr/bin/env bash
# Stage Muse Glimmer on a RunPod network volume for serverless workers (docs/deploy.md,
# "Serverless Glimmer"). Run it on any pod that mounts the volume at /runpod-volume (no GPU
# needed), after copying the rulecam code to /runpod-volume/rulecam. Idempotent: it downloads
# the public GGUF weights and the vision projector once (checked by size), and installs a
# standalone Python 3.12 for llama_edge unless one is already there (the SAM staging installs
# the same one).
set -euo pipefail

VOLUME="${VOLUME:-/runpod-volume}"
MODELS="$VOLUME/glimmer"
REPO="https://huggingface.co/meta-models/Muse-Glimmer-30B-GGUF/resolve/main"
FILES="Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf mmproj-Muse-Glimmer-30B-Q4_K_M.gguf"

log() { printf '%s glimmer_stage: %s\n' "$(date -u +%FT%TZ)" "$*"; }

mkdir -p "$MODELS"
for name in $FILES; do
  url="$REPO/$name"
  size="$(curl -fsSIL "$url" | tr -d '\r' | awk 'tolower($1) == "content-length:" {n = $2} END {print n}')"
  if [[ -f "$MODELS/$name" && "$(stat -c %s "$MODELS/$name")" == "$size" ]]; then
    log "$name already staged ($size bytes)"
    continue
  fi
  log "downloading $name ($size bytes)"
  curl -fL --retry 3 -o "$MODELS/$name.part" "$url"
  [[ "$(stat -c %s "$MODELS/$name.part")" == "$size" ]] || { log "ERROR: $name is incomplete"; exit 1; }
  mv "$MODELS/$name.part" "$MODELS/$name"
done

if ! find "$VOLUME/uv-python" -path '*/bin/python3.12' -type f 2>/dev/null | grep -q .; then
  command -v uv >/dev/null 2>&1 || python3 -m pip install --quiet --upgrade uv
  log "installing a standalone Python 3.12 into $VOLUME/uv-python"
  UV_PYTHON_INSTALL_DIR="$VOLUME/uv-python" uv python install 3.12
fi
log "staged in $MODELS"
