#!/usr/bin/env bash
# RunPod start hook for the SAM GPU pod. Set the pod's dockerStartCmd to
#   bash -c "(/workspace/rulecam/scripts/sam_pod/boot.sh &) ; exec /start.sh"
# so SSH (/start.sh) comes up at once while SAM installs (first boot) or loads in the background.
set -euo pipefail
exec "$(dirname "$0")/setup.sh" "$@" >>"${SAM_SETUP_LOG:-/workspace/sam-setup.log}" 2>&1
