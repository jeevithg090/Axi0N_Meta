#!/usr/bin/env bash
# Activates one release ON the API pod; `make ship-api` (scripts/deploy/ship_api.sh) uploads the
# release and starts this detached from inside it, so a dropped SSH session cannot stop it halfway:
#
#   setsid nohup bash /workspace/releases/<sha12>/scripts/deploy/ship_api_remote.sh <sha> &
#
# Layout on the volume:
#   /workspace/releases/<sha12>/  the code (`git archive`), its REVISION file and its own .venv
#   /workspace/rulecam -> the live release (a symlink; start.sh and restart_api.sh run from it)
#   /workspace/rulecam-shared/    state every release links to: .env, the key files, start.sh,
#                                 plans/, evidence/, data/, reports/ and anything else untracked
#
# The first run moves the hand-made /workspace/rulecam directory to /workspace/rulecam-shared and
# links /workspace/rulecam to it, so the old code stays the first rollback target. Then, in order:
# link the shared state into the release, build its venv (`uv sync --frozen $UV_SYNC_ARGS`), switch the
# symlink, restart the API (`/workspace/restart_api.sh`, detached), and wait for GET /health to
# report this revision. If it does not within HEALTH_WAIT_S, the symlink goes back to the previous
# release and the API is restarted again. Nothing touches the live API before the switch, so a
# failed upload or venv build leaves it as it was. Keeps the KEEP newest releases (plus the live
# and the previous one). Result: `ok`, `rolled-back` or `failed` in
# /workspace/releases/ship-<sha12>.status; the log is ship-<sha12>.log next to it.
set -Eeuo pipefail

SHA=${1:?usage: ship_api_remote.sh <git sha>}
SHORT=${SHA:0:12}
RELEASES=${RELEASES:-/workspace/releases}
LIVE=${LIVE:-/workspace/rulecam}
SHARED=${SHARED:-/workspace/rulecam-shared}
RESTART=${RESTART:-/workspace/restart_api.sh}
HEALTH_URL=${HEALTH_URL:-http://127.0.0.1:8000/health}
HEALTH_WAIT_S=${HEALTH_WAIT_S:-300}
HEALTH_SETTLE_S=${HEALTH_SETTLE_S:-5}  # let the old process go before the first poll
HEALTH_POLL_S=${HEALTH_POLL_S:-3}
KEEP=${KEEP:-3}
SHARED_DIRS=${SHARED_DIRS:-plans evidence data reports}  # tracked, but the API writes into them
# The live pod needs the reid (BoT-SORT) and ml (onnxruntime for RTMPose) extras.
UV_SYNC_ARGS=${UV_SYNC_ARGS:---no-dev --extra reid --extra ml}
mkdir -p "$RELEASES"
RELEASES=$(cd "$RELEASES" && pwd -P)
REL="$RELEASES/$SHORT"
STATUS="$RELEASES/ship-$SHORT.status"
# uv's Python and cache on the volume, like deploy/api-boot.sh: the container disk is wiped on
# restart.
export UV_CACHE_DIR=${UV_CACHE_DIR:-/workspace/uv-cache}
export UV_PYTHON_INSTALL_DIR=${UV_PYTHON_INSTALL_DIR:-/workspace/uv-python}

log() { printf '%s ship %s: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$SHORT" "$*"; }
set_status() { printf '%s\n' "$1" > "$STATUS"; }
# resolve PATH: the physical path (symlinks followed), or nothing when it does not exist.
resolve() { (cd -P "$1" 2> /dev/null && pwd -P) || true; }
# detach CMD...: run CMD in its own session, so it outlives this script and its SSH session.
detach() {
  if command -v setsid > /dev/null; then setsid "$@"; else nohup "$@"; fi
}

exec 9> "$RELEASES/.ship.lock"
if command -v flock > /dev/null && ! flock -n 9; then
  log "another ship is running; not starting a second one"
  exit 1
fi
set_status running
trap 'set_status failed; log "failed at line $LINENO; the live API was not switched"' ERR

migrate() {
  if [ -L "$LIVE" ]; then
    [ -d "$SHARED" ] || { log "$LIVE is a symlink but $SHARED is missing"; return 1; }
    return 0
  fi
  [ ! -e "$SHARED" ] || { log "$SHARED exists but $LIVE is not a symlink: fix by hand"; return 1; }
  mv "$LIVE" "$SHARED"
  ln -s "$SHARED" "$LIVE"
  log "moved $LIVE to $SHARED (the first rollback target) and linked $LIVE to it"
}

is_shared_dir() {
  case " $SHARED_DIRS " in *" $1 "*) return 0 ;; *) return 1 ;; esac
}

link_shared() {
  local path name
  for name in $SHARED_DIRS; do
    if [ ! -e "$SHARED/$name" ]; then
      if [ -d "$REL/$name" ]; then cp -a "$REL/$name" "$SHARED/$name"; else mkdir -p "$SHARED/$name"; fi
    fi
  done
  for path in "$SHARED"/* "$SHARED"/.[!.]*; do
    [ -e "$path" ] || [ -L "$path" ] || continue
    name=$(basename "$path")
    case $name in .venv | REVISION) continue ;; esac
    if is_shared_dir "$name"; then
      rm -rf "${REL:?}/$name"
    elif [ -e "$REL/$name" ] || [ -L "$REL/$name" ]; then
      continue  # tracked code: the release's own copy
    fi
    ln -s "$path" "$REL/$name"
  done
  log "linked the shared state into $REL"
}

build_venv() {
  command -v uv > /dev/null || { log "uv is not on PATH"; return 1; }
  local args
  read -r -a args <<< "$UV_SYNC_ARGS"
  (cd "$REL" && UV_PROJECT_ENVIRONMENT="$REL/.venv" uv sync --frozen "${args[@]}")
  log "venv ready"
}

# switch_to DIR: point the live symlink at DIR in one rename(2), so it is never missing.
switch_to() {
  ln -sfn "$1" "$LIVE.next"
  python3 -c 'import os, sys; os.replace(sys.argv[1], sys.argv[2])' "$LIVE.next" "$LIVE"
  log "$LIVE -> $1"
}

restart() {
  detach bash "$RESTART" >> "$RELEASES/restart-$SHORT.log" 2>&1 < /dev/null &
  log "restarting the API with $RESTART"
}

# health_revision: the revision GET /health reports, `none` without one, empty while it is down.
health_revision() {
  curl -fsS -m 10 "$HEALTH_URL" 2> /dev/null \
    | python3 -c 'import json, sys; b = json.load(sys.stdin); print(b.get("revision") or "none" if b.get("ok") else "")' \
    2> /dev/null || true
}

# wait_health EXPECTED: wait until /health reports EXPECTED (any healthy answer when `any`).
wait_health() {
  local expected=$1 deadline seen
  deadline=$(($(date +%s) + HEALTH_WAIT_S))
  sleep "$HEALTH_SETTLE_S"
  while [ "$(date +%s)" -lt "$deadline" ]; do
    seen=$(health_revision)
    if [ -n "$seen" ] && { [ "$expected" = any ] || [ "$seen" = "$expected" ]; }; then
      log "/health ok, revision $seen"
      return 0
    fi
    sleep "$HEALTH_POLL_S"
  done
  log "/health did not report revision $expected within $HEALTH_WAIT_S s (last: ${seen:-down})"
  return 1
}

# prune KEEP_TOO: delete all but the KEEP newest releases, never the live one or KEEP_TOO.
prune() {
  local dir live
  live=$(resolve "$LIVE")
  # shellcheck disable=SC2012  # release names are 12 hex digits: nothing for ls to mangle
  ls -1dt "$RELEASES"/????????????/ 2> /dev/null | sed 's:/$::' | awk -v keep="$KEEP" 'NR > keep' \
    | while read -r dir; do
      if [ "$dir" != "$live" ] && [ "$dir" != "$1" ]; then
        rm -rf "$dir"
        log "pruned $dir"
      fi
    done
}

fail() {
  log "$*"
  set_status failed
  exit 1
}
[ -f "$RESTART" ] || fail "$RESTART is missing"
[ -f "$REL/REVISION" ] || fail "$REL has no REVISION: upload it with ship_api.sh"
migrate
prev=$(resolve "$LIVE")
[ "$prev" != "$REL" ] || { log "$REL is already live"; set_status ok; exit 0; }
link_shared
build_venv
trap - ERR
switch_to "$REL"
restart
if wait_health "$SHA"; then
  set_status ok
  prune "$prev"
  log "live: $SHA (previous: $prev)"
  exit 0
fi
log "rolling back to $prev"
switch_to "$prev"
restart
wait_health any || log "WARNING: the previous release is not healthy either: check $RESTART"
set_status rolled-back
exit 1
