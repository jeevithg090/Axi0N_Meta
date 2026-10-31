#!/usr/bin/env bash
# Rulecam API container entrypoint (deploy/api.Dockerfile; docs/deploy.md "One-command deploy").
#
#   api-entrypoint.sh [serve]      run uvicorn with the documented flags (the default)
#   api-entrypoint.sh healthcheck  exit 0 when GET /health answers (Docker HEALTHCHECK)
#   api-entrypoint.sh <cmd...>     run any command with the same secrets and working directory,
#                                  e.g. `python -m rulecam.replay.preflight`
#
# Idempotent and stateless: everything it needs lives in RULECAM_HOME (default
# /workspace/rulecam, on the network volume), so a fresh container on the same volume (a pod
# restart wipes the container disk) comes up the same way every time.
#
# Secrets, like start.sh on the manual pod: each variable comes from the environment when set
# there (e.g. a RunPod secret `{{ RUNPOD_SECRET_... }}`), else from RULECAM_HOME/.<name>
# (mode 600): .meta_api_key, .glimmer_api_key, .sam_api_key, .baseten_api_key, .hf_token,
# .whatsapp_token, .modal_token_id, .modal_token_secret. Values are never logged or put in argv.
#
# Started as root (as on RunPod), it starts sshd when RunPod passes PUBLIC_KEY (owner actions
# need a loopback client, e.g. `ssh … curl -X POST localhost:8000/pair/token`), then runs the
# server as the unprivileged `rulecam` user. Non-secret settings come from the environment or
# RULECAM_HOME/.env (pydantic-settings reads it from the working directory).
set -euo pipefail
umask 077

ENTRYPOINT_NAME="rulecam-api"
# shellcheck source-path=SCRIPTDIR
# shellcheck source=entrypoint-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/entrypoint-lib.sh"

RULECAM_HOME="${RULECAM_HOME:-/workspace/rulecam}"
RULECAM_PORT="${RULECAM_PORT:-8000}"
RULECAM_USER="${RULECAM_USER:-rulecam}"
# RunPod's HTTPS proxy reaches the pod from 100.64.0.0/10; trusting it is what makes phones on
# the proxy count as https (Pair-a-Phone refuses plain http). Never "*".
RULECAM_FORWARDED_ALLOW_IPS="${RULECAM_FORWARDED_ALLOW_IPS:-100.64.0.0/10}"
SECRET_NAMES="meta_api_key glimmer_api_key sam_api_key baseten_api_key hf_token whatsapp_token modal_token_id modal_token_secret"

load_secrets() {
  local name
  for name in $SECRET_NAMES; do
    load_secret "$(upper "$name")" "$RULECAM_HOME/.$name"
  done
}

default_settings() {
  # The pod's own public URL, for Pair-a-Phone links; RunPod sets RUNPOD_POD_ID in every pod.
  if [[ -z "${PUBLIC_TUNNEL_URL:-}" && -n "${RUNPOD_POD_ID:-}" ]]; then
    export PUBLIC_TUNNEL_URL="https://${RUNPOD_POD_ID}-${RULECAM_PORT}.proxy.runpod.net"
    log "PUBLIC_TUNNEL_URL=$PUBLIC_TUNNEL_URL (from RUNPOD_POD_ID)"
  fi
}

start_sshd() {
  [[ "$(id -u)" == "0" && -n "${PUBLIC_KEY:-}" && -x /usr/sbin/sshd ]] || return 0
  mkdir -p /root/.ssh /run/sshd
  chmod 700 /root/.ssh
  printf '%s\n' "$PUBLIC_KEY" >/root/.ssh/authorized_keys
  chmod 600 /root/.ssh/authorized_keys
  ssh-keygen -A >/dev/null
  /usr/sbin/sshd -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no \
    -o PermitRootLogin=prohibit-password
  log "sshd started (key-only)"
}

prepare() {
  [[ "$RULECAM_FORWARDED_ALLOW_IPS" != *"*"* ]] || die "RULECAM_FORWARDED_ALLOW_IPS must not trust every forwarder (*)"
  mkdir -p "$RULECAM_HOME"
  cd "$RULECAM_HOME"
  load_secrets
  default_settings
  start_sshd
}

main() {
  local command="${1:-serve}"
  case "$command" in
    healthcheck)
      http_ok "http://127.0.0.1:${RULECAM_PORT}/health"
      ;;
    serve)
      prepare
      log "starting uvicorn on :$RULECAM_PORT (home $RULECAM_HOME)"
      # --timeout-graceful-shutdown: without it a restart hangs while public /ws viewers stay
      # connected and the new process cannot take the port.
      drop_privileges "$RULECAM_USER" "$RULECAM_HOME" \
        uvicorn rulecam.api.app:app --host 0.0.0.0 --port "$RULECAM_PORT" \
        --timeout-graceful-shutdown 5 --forwarded-allow-ips "$RULECAM_FORWARDED_ALLOW_IPS"
      ;;
    *)
      prepare
      drop_privileges "$RULECAM_USER" "$RULECAM_HOME" "$@"
      ;;
  esac
}

main "$@"
