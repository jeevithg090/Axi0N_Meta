#!/usr/bin/env bash
# Print the two env vars that carry the Llama Guard 4 worker's code (docs/deploy.md,
# "Serverless Llama Guard 4"), as a JSON object to merge into the endpoint's env:
#   LLAMA_GUARD_BOOT_GZ_B64  scripts/llama_guard_pod/serverless.sh, gzip + base64
#   LLAMA_GUARD_EDGE_GZ_B64  src/rulecam/clients/open_weights/llama_edge.py, gzip + base64
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
blob() { gzip -9 -n -c "$1" | base64 | tr -d '\n'; }
boot="$(blob "$root/scripts/llama_guard_pod/serverless.sh")"
edge="$(blob "$root/src/rulecam/clients/open_weights/llama_edge.py")"
printf '{"LLAMA_GUARD_BOOT_GZ_B64": "%s", "LLAMA_GUARD_EDGE_GZ_B64": "%s"}\n' "$boot" "$edge"
