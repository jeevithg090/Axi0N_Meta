#!/usr/bin/env bash
# Print the env vars that carry the standby Glimmer worker's code (scripts/glimmer_pod/anywhere.sh
# and llama_edge, gzip + base64) as a JSON object to merge into that endpoint's env.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
blob() { gzip -9 -n -c "$1" | base64 | tr -d '\n'; }
boot="$(blob "$root/scripts/glimmer_pod/anywhere.sh")"
edge="$(blob "$root/src/rulecam/clients/open_weights/llama_edge.py")"
printf '{"GLIMMER_BOOT_GZ_B64": "%s", "GLIMMER_EDGE_GZ_B64": "%s"}\n' "$boot" "$edge"
