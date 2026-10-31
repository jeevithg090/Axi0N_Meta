#!/usr/bin/env bash
# Start (and on first boot, register) the Gitea Actions runner (gitea-runner) on a RunPod CPU pod.
#
# RunPod pods are containers without Docker, so the runner uses host mode: jobs run directly in
# the pod, which this script provisions with git, ffmpeg, Python 3.12 (via uv) and Node.
#
# Run it as the pod's start command, or once in the pod's web terminal:
#   GITEA_RUNNER_TOKEN=<registration token> bash bootstrap.sh
# The token comes from the Gitea repository: Settings → Actions → Runners → Create new runner.
# Or write it to /workspace/.gitea_runner_token. It is only needed on first boot; the registration is kept in $RUNNER_HOME (put it on the pod's
# persistent /workspace volume so restarts reuse it).
set -euo pipefail

GITEA_URL="${GITEA_URL:-https://gitea.com}"
RUNNER_HOME="${RUNNER_HOME:-/workspace/gitea-runner}"
RUNNER_NAME="${RUNNER_NAME:-rulecam-runpod}"
RUNNER_LABELS="${RUNNER_LABELS:-rulecam:host}"
RUNNER_VERSION="${RUNNER_VERSION:-5.0.0}"
RUNNER_CAPACITY="${RUNNER_CAPACITY:-3}"
NODE_VERSION="${NODE_VERSION:-20.18.0}"  # JavaScript actions such as actions/checkout need Node 20

case "$(uname -m)" in x86_64) arch=amd64 node_arch=x64 ;; aarch64) arch=arm64 node_arch=arm64 ;;
  *) echo "unsupported arch $(uname -m)"; exit 1 ;; esac

export DEBIAN_FRONTEND=noninteractive
if ! command -v ffmpeg >/dev/null || ! command -v git >/dev/null || ! command -v xz >/dev/null; then
  apt-get update -qq
  apt-get install -y -qq --no-install-recommends ca-certificates curl git ffmpeg xz-utils >/dev/null
fi
if ! node --version 2>/dev/null | grep -q "^v${NODE_VERSION%%.*}\."; then
  curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${node_arch}.tar.xz" \
    | tar -xJ -C /usr/local --strip-components=1
fi
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
fi
uv python install 3.12
ln -sf "$(uv python find 3.12)" /usr/local/bin/python3

mkdir -p "$RUNNER_HOME"
cd "$RUNNER_HOME"
runner="./gitea-runner-${RUNNER_VERSION}"
if [ ! -x "$runner" ]; then
  curl -fsSL -o "$runner" \
    "https://gitea.com/gitea/runner/releases/download/v${RUNNER_VERSION}/gitea-runner-${RUNNER_VERSION}-linux-${arch}"
  chmod +x "$runner"
fi
if [ ! -f config.yaml ]; then
  "$runner" config init
  "$runner" config set runner.capacity "$RUNNER_CAPACITY"
  "$runner" config set runner.labels "$RUNNER_LABELS"
fi
if [ ! -f .runner ]; then
  token_file="${GITEA_RUNNER_TOKEN_FILE:-/workspace/.gitea_runner_token}"
  if [ -z "${GITEA_RUNNER_TOKEN:-}" ] && [ -f "$token_file" ]; then
    GITEA_RUNNER_TOKEN="$(tr -d '[:space:]' < "$token_file")"
  fi
  : "${GITEA_RUNNER_TOKEN:?set GITEA_RUNNER_TOKEN or write it to $token_file}"
  "$runner" -c config.yaml register --no-interactive \
    --instance "$GITEA_URL" --token "$GITEA_RUNNER_TOKEN" --name "$RUNNER_NAME"
fi
# Shared caches survive between jobs (and restarts, on /workspace).
export UV_CACHE_DIR="${UV_CACHE_DIR:-/workspace/.uv-cache}"
exec "$runner" -c config.yaml daemon
