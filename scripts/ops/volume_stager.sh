#!/usr/bin/env bash
# Ship GPU code onto a RunPod network volume when there is neither an S3 API key (make ship-gpu)
# nor pod stock in the volume's data center (US-IL-1 often has none): a throwaway serverless
# worker that mounts the volume receives a bundle over `runpodctl send` (croc) and swaps it in.
#
# On your machine (docs/demo-day.md, "Ship GPU code without S3 keys"):
#   git archive --format=tar <sha> -- src scripts/sam_pod scripts/glimmer_pod | tar -x -C stage
#   echo <sha> > stage/REVISION; tar -czf rulecam-gpu.tgz -C stage .
#   runpodctl send --code <code> rulecam-gpu.tgz          # waits for the worker
# Then create a LOAD_BALANCER endpoint on the volume's data center with the volume mounted, any
# 1-GPU pool, min 1 / max 1, image runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04,
# env RECEIVE_CODE=<code> and STAGER_GZ_B64=$(gzip -9 -n -c scripts/ops/volume_stager.sh | base64),
# entrypoint ["bash","-c","printf '%s' \"$STAGER_GZ_B64\" | base64 -d | gunzip > /tmp/s.sh &&
# exec bash /tmp/s.sh"]. Read its logs for `stager: done`, then delete the endpoint.
#
# What it changes: /runpod-volume/rulecam/{src,scripts/sam_pod,scripts/glimmer_pod,REVISION}.
# The old copies move to /runpod-volume/rulecam/backup-<UTC time>/ (roll back by moving them
# back). Weights, venvs and sam.env are not touched. Running workers keep the code they loaded;
# the next cold start runs the new code.
set -euo pipefail

VOLUME="${VOLUME:-/runpod-volume}"
ROOT="$VOLUME/rulecam"
PATHS="${STAGER_PATHS:-src scripts/sam_pod scripts/glimmer_pod}"
RUNPODCTL_URL="${RUNPODCTL_URL:-https://github.com/runpod/runpodctl/releases/download/v3.0.1/runpodctl-linux-amd64}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"

log() { printf '%s stager: %s\n' "$(date -u +%FT%TZ)" "$*"; }
hold() { log "holding (delete this endpoint)"; exec sleep infinity; }

[[ -n "${RECEIVE_CODE:-}" ]] || { log "RECEIVE_CODE is not set"; hold; }
work="$(mktemp -d)"
cd "$work"
curl -fsSL -o runpodctl "$RUNPODCTL_URL"
chmod +x runpodctl
log "receiving the bundle"
./runpodctl receive "$RECEIVE_CODE" > receive.log 2>&1 || { cat receive.log; log "receive failed"; hold; }
bundle="$(find "$work" -maxdepth 1 -name '*.tgz' | head -n 1)"
[[ -n "$bundle" ]] || { log "no .tgz received"; hold; }
mkdir new
tar -xzf "$bundle" -C new
log "before: REVISION $(cat "$ROOT/REVISION" 2>/dev/null || echo none)"

backup="$ROOT/backup-$TS"
for path in $PATHS; do
  [[ -d "new/$path" ]] || { log "bundle has no $path; skipped"; continue; }
  cp -a "new/$path" "$ROOT/$path.new-$TS"
  mkdir -p "$backup/$(dirname "$path")"
  [[ -e "$ROOT/$path" ]] && mv "$ROOT/$path" "$backup/$path"
  mv "$ROOT/$path.new-$TS" "$ROOT/$path"
  log "swapped $path"
done
[[ -f "$ROOT/REVISION" ]] && cp "$ROOT/REVISION" "$backup/REVISION"
[[ -f new/REVISION ]] && cp new/REVISION "$ROOT/REVISION"
log "after: REVISION $(cat "$ROOT/REVISION" 2>/dev/null || echo none); backup in $backup"
log "done"
hold
