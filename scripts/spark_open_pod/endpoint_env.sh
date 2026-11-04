#!/usr/bin/env bash
# Print the two env vars that carry the Muse Spark Open worker's code (docs/deploy.md,
# "Serverless Muse Spark Open"), as a JSON object to merge into the endpoint's env:
#   SPARK_OPEN_BOOT_GZ_B64  scripts/spark_open_pod/serverless.sh, gzip + base64
#   SPARK_OPEN_EDGE_GZ_B64  src/rulecam/clients/open_weights/llama_edge.py, gzip + base64
# The endpoint's entrypoint unpacks and runs the first; the first unpacks the second. Re-run and
# update the endpoint's env after changing either file; workers pick it up on their next start.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
blob() { gzip -9 -n -c "$1" | base64 | tr -d '\n'; }
boot="$(blob "$root/scripts/spark_open_pod/serverless.sh")"
edge="$(blob "$root/src/rulecam/clients/open_weights/llama_edge.py")"
printf '{"SPARK_OPEN_BOOT_GZ_B64": "%s", "SPARK_OPEN_EDGE_GZ_B64": "%s"}\n' "$boot" "$edge"
