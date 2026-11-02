#!/usr/bin/env bash
# `make ship-api`: deploy a commit to the API pod and wait until GET /health reports it
# (docs/deploy.md, "Ship the API").
#
#   RULECAM_API_SSH_HOST=<ip> RULECAM_API_SSH_PORT=<port> make ship-api            # HEAD
#   RULECAM_API_SSH_HOST=<ip> RULECAM_API_SSH_PORT=<port> scripts/deploy/ship_api.sh <ref>
#
# 1. `git archive <ref>` is unpacked into /workspace/releases/<sha12> on the pod (into a hidden
#    directory first, renamed when complete, so a dropped upload leaves nothing half-written),
#    with a REVISION file holding the full SHA. An upload that already exists is reused.
# 2. scripts/deploy/ship_api_remote.sh starts on the pod, detached (setsid nohup), from inside
#    the new release: it links the untracked state (.env, the key files, start.sh) into the
#    release, builds its venv, switches /workspace/rulecam to it, restarts the API detached and
#    rolls back to the previous release when /health does not report the new revision in time.
# 3. This script polls the result file over short SSH calls (a dropped SSH session is retried),
#    then prints the remote log. Exit status: 0 live, 1 failed or rolled back, 2 usage.
#
# Environment: RULECAM_API_SSH_HOST and RULECAM_API_SSH_PORT (required: the pod's public IP and
# the port mapped to 22, from `runpodctl pod get 7nv0ifjo2tmk00`; the port changes on every pod
# restart), RULECAM_API_SSH_USER (root), RULECAM_API_SSH_KEY (~/.runpod/ssh/runpodctl-ssh-key),
# SHIP_TIMEOUT_S (900, the whole remote run including the venv build), and RELEASES, LIVE,
# SHARED, RESTART, HEALTH_WAIT_S, KEEP, which are passed through to ship_api_remote.sh.
set -euo pipefail

HOST=${RULECAM_API_SSH_HOST:-}
PORT=${RULECAM_API_SSH_PORT:-}
USER_NAME=${RULECAM_API_SSH_USER:-root}
KEY=${RULECAM_API_SSH_KEY:-$HOME/.runpod/ssh/runpodctl-ssh-key}
SHIP_TIMEOUT_S=${SHIP_TIMEOUT_S:-900}
RELEASES=${RELEASES:-/workspace/releases}
SSH_TRIES=${SSH_TRIES:-5}
POLL_S=${POLL_S:-5}
REF=${1:-HEAD}

log() { printf 'ship-api: %s\n' "$*" >&2; }

if [ -z "$HOST" ] || [ -z "$PORT" ]; then
  log "set RULECAM_API_SSH_HOST and RULECAM_API_SSH_PORT (runpodctl pod get 7nv0ifjo2tmk00)"
  exit 2
fi
[ -f "$KEY" ] || { log "no SSH key at $KEY (set RULECAM_API_SSH_KEY)"; exit 2; }

root=$(git rev-parse --show-toplevel)
SHA=$(git -C "$root" rev-parse --verify "$REF^{commit}")
SHORT=${SHA:0:12}
REL="$RELEASES/$SHORT"
if [ "$REF" = HEAD ] && [ -n "$(git -C "$root" status --porcelain --untracked-files=no)" ]; then
  log "WARNING: uncommitted changes are not shipped; shipping $SHORT as committed"
fi

# The settings the remote script reads, quoted for the remote shell.
remote_env=""
for name in RELEASES LIVE SHARED RESTART HEALTH_URL HEALTH_WAIT_S KEEP SHARED_DIRS; do
  if [ -n "${!name:-}" ]; then
    remote_env+="$name=$(printf '%q' "${!name}") "
  fi
done

SSH=(ssh -i "$KEY" -p "$PORT" -o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=15
  -o ServerAliveCountMax=4 -o StrictHostKeyChecking=accept-new "$USER_NAME@$HOST")

# remote CMD: run CMD on the pod, retrying when SSH itself fails (exit 255).
remote() {
  local try status
  for ((try = 1; try <= SSH_TRIES; try++)); do
    status=0
    "${SSH[@]}" "$1" || status=$?
    [ "$status" -ne 255 ] && return "$status"
    log "SSH dropped (try $try of $SSH_TRIES); retrying"
    sleep 2
  done
  return 255
}

# upload: unpack the archive into a hidden directory, then rename it into place.
upload() {
  local try status incoming="$RELEASES/.incoming-$SHORT"
  for ((try = 1; try <= SSH_TRIES; try++)); do
    status=0
    git -C "$root" archive --format=tar "$SHA" | "${SSH[@]}" "set -e
      if [ -f $REL/REVISION ]; then cat > /dev/null; exit 0; fi
      rm -rf $incoming && mkdir -p $incoming
      tar -x -C $incoming
      printf '%s\n' $SHA > $incoming/REVISION
      mv $incoming $REL" || status=$?
    [ "$status" -eq 0 ] && return 0
    log "upload failed (exit $status, try $try of $SSH_TRIES); retrying"
    sleep 2
  done
  return 1
}

log "uploading $SHORT to $USER_NAME@$HOST:$REL"
upload
log "starting the switch on the pod (detached; log $RELEASES/ship-$SHORT.log)"
remote "rm -f $RELEASES/ship-$SHORT.status
  ${remote_env}setsid nohup bash $REL/scripts/deploy/ship_api_remote.sh $SHA \
    >> $RELEASES/ship-$SHORT.log 2>&1 < /dev/null &"

deadline=$(($(date +%s) + SHIP_TIMEOUT_S))
state=""
while [ "$(date +%s)" -lt "$deadline" ]; do
  sleep "$POLL_S"
  state=$(remote "cat $RELEASES/ship-$SHORT.status 2> /dev/null || true") || state=""
  case $state in ok | rolled-back | failed) break ;; esac
done
remote "tail -n 40 $RELEASES/ship-$SHORT.log" >&2 || true
case $state in
  ok)
    log "live: $SHA"
    exit 0
    ;;
  rolled-back) log "rolled back: $SHORT never reported healthy; the previous release is live" ;;
  failed) log "failed before the switch; the live API is unchanged" ;;
  *) log "no result after ${SHIP_TIMEOUT_S}s (last: ${state:-none}); check $RELEASES/ship-$SHORT.log" ;;
esac
exit 1
