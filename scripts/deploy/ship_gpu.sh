#!/usr/bin/env bash
# `make ship-gpu`: copy the committed code the serverless GPU workers run onto their RunPod network
# volume through RunPod's S3-compatible API, with no staging pod (docs/deploy.md, "Ship the GPU
# code").
#
#   RUNPOD_S3_ACCESS_KEY_ID=... RUNPOD_S3_SECRET_ACCESS_KEY=... make ship-gpu        # HEAD
#   scripts/deploy/ship_gpu.sh <ref>
#
# Workers see the volume at /runpod-volume and run /runpod-volume/rulecam/src (PYTHONPATH) and
# the start commands in /runpod-volume/rulecam/scripts/{sam_pod,glimmer_pod}; this writes the
# same paths: `git archive <ref>` of SHIP_GPU_PATHS goes to s3://<volume>/rulecam/<path>, objects
# the commit no longer has are deleted, and rulecam/REVISION holds the SHA. Each path is
# uploaded in full (it is small), so a changed file is never skipped by a timestamp comparison.
# Running workers keep the code they started with; the next cold start loads the new code
# (`scripts/ops/warm.sh off` and wait out the 300 s idle timeout to force it). Model weights,
# venvs and sam.env on the volume are not touched.
#
# Environment: RUNPOD_S3_ACCESS_KEY_ID and RUNPOD_S3_SECRET_ACCESS_KEY (a RunPod S3 API key,
# Settings > S3 API Keys; or the usual AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_PROFILE),
# RUNPOD_VOLUME_ID (11ymk65bni), RUNPOD_S3_REGION (us-il-1, the volume's data center),
# RUNPOD_S3_ENDPOINT (https://s3api-<region>.runpod.io), SHIP_GPU_PATHS (src scripts/sam_pod
# scripts/glimmer_pod), DRY_RUN=1 (print the writes, change nothing) and AWS (the aws CLI; by
# default `aws`, else `uvx --from awscli aws`). Keys reach aws through its environment, never
# argv. Exit status: 0 shipped, 1 a copy failed, 2 usage or missing credentials.
set -euo pipefail

VOLUME_ID=${RUNPOD_VOLUME_ID:-11ymk65bni}
REGION=${RUNPOD_S3_REGION:-us-il-1}
ENDPOINT=${RUNPOD_S3_ENDPOINT:-https://s3api-$REGION.runpod.io}
PATHS=${SHIP_GPU_PATHS:-src scripts/sam_pod scripts/glimmer_pod}
PREFIX=rulecam
REF=${1:-HEAD}

log() { printf 'ship-gpu: %s\n' "$*" >&2; }

if [ -n "${RUNPOD_S3_ACCESS_KEY_ID:-}" ] || [ -n "${RUNPOD_S3_SECRET_ACCESS_KEY:-}" ]; then
  export AWS_ACCESS_KEY_ID=${RUNPOD_S3_ACCESS_KEY_ID:-}
  export AWS_SECRET_ACCESS_KEY=${RUNPOD_S3_SECRET_ACCESS_KEY:-}
fi
if [ -z "${AWS_PROFILE:-}" ] && { [ -z "${AWS_ACCESS_KEY_ID:-}" ] || [ -z "${AWS_SECRET_ACCESS_KEY:-}" ]; }; then
  log "set RUNPOD_S3_ACCESS_KEY_ID and RUNPOD_S3_SECRET_ACCESS_KEY (a RunPod S3 API key)"
  exit 2
fi

if [ -n "${AWS:-}" ]; then
  read -r -a aws_cmd <<< "$AWS"
elif command -v aws > /dev/null; then
  aws_cmd=(aws)
elif command -v uvx > /dev/null; then
  aws_cmd=(uvx --from awscli aws)
else
  log "no aws CLI: install it (or uv, for uvx) or set AWS"
  exit 2
fi
s3() { "${aws_cmd[@]}" s3 "$@" --region "$REGION" --endpoint-url "$ENDPOINT" --only-show-errors; }
dry=()
[ "${DRY_RUN:-0}" = 1 ] && dry=(--dryrun)

root=$(git rev-parse --show-toplevel)
SHA=$(git -C "$root" rev-parse --verify "$REF^{commit}")
if [ "$REF" = HEAD ] && [ -n "$(git -C "$root" status --porcelain --untracked-files=no)" ]; then
  log "WARNING: uncommitted changes are not shipped; shipping ${SHA:0:12} as committed"
fi
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
read -r -a paths <<< "$PATHS"
git -C "$root" archive --format=tar "$SHA" -- "${paths[@]}" | tar -x -C "$stage"
printf '%s\n' "$SHA" > "$stage/REVISION"

for path in "${paths[@]}"; do
  dest="s3://$VOLUME_ID/$PREFIX/$path"
  log "copying $path to $dest"
  s3 cp "$stage/$path" "$dest" --recursive ${dry[@]+"${dry[@]}"}
  # Only deletes what the commit no longer has: everything else was just copied, so it is newer.
  s3 sync "$stage/$path" "$dest" --delete --exclude '*__pycache__*' ${dry[@]+"${dry[@]}"}
done
s3 cp "$stage/REVISION" "s3://$VOLUME_ID/$PREFIX/REVISION" ${dry[@]+"${dry[@]}"}
log "shipped ${SHA:0:12} to volume $VOLUME_ID ($REGION); new workers load it on their next start"
