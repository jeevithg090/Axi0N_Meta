#!/usr/bin/env bash
# Ship the web UI (ui/) to the fallback UI pod on RunPod and serve its production build on :3000
# (scripts/ops/ui_server.mjs: `next start` with no-store on pages, because the RunPod proxy caches
# them), reachable at https://<pod-id>-3000.proxy.runpod.net. Used while Vercel cannot deploy.
#
#   RULECAM_UI_SSH_HOST=<ip> RULECAM_UI_SSH_PORT=<port> scripts/ops/ship_ui_runpod.sh [ref]
#
# 1. `git archive <ref> ui scripts/ops/ui_server.mjs` is unpacked into
#    /root/rulecam-ui/releases/<sha12> on the pod.
# 2. On the pod (detached, so a dropped SSH session cannot stop it halfway): Node.js is installed
#    from nodejs.org if missing (checked against its SHASUMS256), then `npm ci` and
#    `npm run build` with NEXT_PUBLIC_RULECAM_API baked in. Earlier builds' hashed files are
#    copied in (pages a proxy still holds keep loading), the running server is replaced and the
#    script waits until the new build answers on http://127.0.0.1:3000/.
# 3. This script polls the result file and prints the remote log. Exit 0 live, 1 failed.
#
# Environment: RULECAM_UI_SSH_HOST and RULECAM_UI_SSH_PORT (required; `runpodctl pod get
# <ui-pod-id>`, the port mapped to 22 changes on every pod restart), RULECAM_UI_SSH_KEY
# (~/.runpod/ssh/runpodctl-ssh-key), RULECAM_API_URL (the live API, the same value as the Vercel
# project's NEXT_PUBLIC_RULECAM_API), RULECAM_PROXY_SECRET (optional; the API's UI_PROXY_SECRET),
# NODE_MAJOR (22), SHIP_TIMEOUT_S (1200). The container disk is wiped when the pod restarts: run
# this script again after a restart.
set -euo pipefail

HOST=${RULECAM_UI_SSH_HOST:-}
PORT=${RULECAM_UI_SSH_PORT:-}
KEY=${RULECAM_UI_SSH_KEY:-$HOME/.runpod/ssh/runpodctl-ssh-key}
API_URL=${RULECAM_API_URL:-https://7nv0ifjo2tmk00-8000.proxy.runpod.net}
PROXY_SECRET=${RULECAM_PROXY_SECRET:-}
NODE_MAJOR=${NODE_MAJOR:-22}
SHIP_TIMEOUT_S=${SHIP_TIMEOUT_S:-1200}
REF=${1:-HEAD}
ROOT_DIR=/root/rulecam-ui

log() { printf 'ship-ui: %s\n' "$*" >&2; }

if [ -z "$HOST" ] || [ -z "$PORT" ]; then
  log "set RULECAM_UI_SSH_HOST and RULECAM_UI_SSH_PORT (runpodctl pod get <ui-pod-id>)"
  exit 2
fi
[ -f "$KEY" ] || { log "no SSH key at $KEY (set RULECAM_UI_SSH_KEY)"; exit 2; }

root=$(git rev-parse --show-toplevel)
SHA=$(git -C "$root" rev-parse --verify "$REF^{commit}")
SHORT=${SHA:0:12}
REL="$ROOT_DIR/releases/$SHORT"

SSH=(ssh -i "$KEY" -p "$PORT" -o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=15
  -o ServerAliveCountMax=4 -o StrictHostKeyChecking=accept-new "root@$HOST")

remote() {
  local try status
  for try in 1 2 3 4 5; do
    status=0
    "${SSH[@]}" "$1" || status=$?
    [ "$status" -ne 255 ] && return "$status"
    log "SSH dropped (try $try of 5); retrying"
    sleep 2
  done
  return 255
}

log "uploading ui/ at $SHORT to root@$HOST:$REL"
git -C "$root" archive --format=tar "$SHA" ui scripts/ops/ui_server.mjs | "${SSH[@]}" "set -e
  mkdir -p $ROOT_DIR/releases
  if [ -f $REL/REVISION ]; then cat > /dev/null; exit 0; fi
  rm -rf $REL.incoming && mkdir -p $REL.incoming
  tar -x -C $REL.incoming
  printf '%s\n' $SHA > $REL.incoming/REVISION
  mv $REL.incoming $REL"

# The remote half: install Node if needed, build, swap the server, wait for it.
remote_script=$(cat << 'EOF'
set -euo pipefail
REL=$1 API_URL=$2 NODE_MAJOR=$3 ROOT_DIR=$4
STATUS=$REL/ship.status
echo running > "$STATUS"
trap 'echo failed > "$STATUS"' ERR
if ! command -v node > /dev/null || [ "$(node -p 'process.versions.node.split(".")[0]')" != "$NODE_MAJOR" ]; then
  base=https://nodejs.org/dist/latest-v$NODE_MAJOR.x
  tarball=$(curl -fsSL "$base/SHASUMS256.txt" | awk '/linux-x64\.tar\.xz$/ {print $2}')
  want=$(curl -fsSL "$base/SHASUMS256.txt" | awk '/linux-x64\.tar\.xz$/ {print $1}')
  curl -fsSL "$base/$tarball" -o /tmp/node.tar.xz
  echo "$want  /tmp/node.tar.xz" | sha256sum -c -
  tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1
  rm -f /tmp/node.tar.xz
fi
echo "node $(node --version), npm $(npm --version)"
cd "$REL/ui"
npm ci --no-audit --no-fund
NEXT_PUBLIC_RULECAM_API=$API_URL NEXT_TELEMETRY_DISABLED=1 npm run build
for old in "$ROOT_DIR"/releases/*/ui/.next/static; do
  [ "$old" = "$REL/ui/.next/static" ] || cp -rn "$old/." "$REL/ui/.next/static/" || true
done
cp "$REL/scripts/ops/ui_server.mjs" "$REL/ui/ui_server.mjs"
# Stop the running server (`next start` renames itself `next-server`), wait for :3000 to be free.
SERVERS="ui_server.mjs|next-server|next start"
pkill -f "$SERVERS" || true
for _ in $(seq 1 30); do
  pgrep -f "$SERVERS" > /dev/null || break
  sleep 1
done
pkill -9 -f "$SERVERS" || true
ln -sfn "$REL" "$ROOT_DIR/current"
cd "$ROOT_DIR/current/ui"
set -a
# shellcheck disable=SC1091
[ -f "$ROOT_DIR/ui.env" ] && . "$ROOT_DIR/ui.env"
set +a
NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0 \
  setsid nohup node ui_server.mjs >> "$ROOT_DIR/next.log" 2>&1 < /dev/null &
want=$(cd "$ROOT_DIR/current/ui" && pwd -P)
for _ in $(seq 1 60); do
  if curl -fsS -o /dev/null -m 5 http://127.0.0.1:3000/; then
    # Live only when the server answering is the new build (its working directory).
    for pid in $(pgrep -f "ui_server.mjs" || true); do
      if [ "$(readlink "/proc/$pid/cwd")" = "$want" ]; then echo ok > "$STATUS"; exit 0; fi
    done
  fi
  sleep 2
done
echo failed > "$STATUS"
exit 1
EOF
)

if [ -n "$PROXY_SECRET" ]; then
  log "writing RULECAM_PROXY_SECRET to $ROOT_DIR/ui.env (mode 600)"
  printf 'RULECAM_PROXY_SECRET=%s\n' "$PROXY_SECRET" \
    | "${SSH[@]}" "umask 077; cat > $ROOT_DIR/ui.env"
fi
printf '%s\n' "$remote_script" | "${SSH[@]}" "cat > $REL/ship_remote.sh"
log "building on the pod (detached; log $REL/ship.log)"
remote "setsid nohup bash $REL/ship_remote.sh $REL $API_URL $NODE_MAJOR $ROOT_DIR \
  > $REL/ship.log 2>&1 < /dev/null &"

deadline=$(($(date +%s) + SHIP_TIMEOUT_S))
state=""
while [ "$(date +%s)" -lt "$deadline" ]; do
  sleep 10
  state=$(remote "cat $REL/ship.status 2> /dev/null || true") || state=""
  case $state in ok | failed) break ;; esac
done
remote "tail -n 25 $REL/ship.log" >&2 || true
if [ "$state" = ok ]; then
  log "live: $SHA (ui_server.mjs on :3000)"
  exit 0
fi
log "not live (state: ${state:-none}); see $REL/ship.log on the pod"
exit 1
