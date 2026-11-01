#!/usr/bin/env bash
# Rulecam on RunPod: `down`. See scripts/deploy/runpod.py and docs/deploy.md "One-command deploy";
# `runpod_down.sh --help` lists the options (`--dry-run` prints the writes instead of sending).
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$here/runpod.py" down "$@"
