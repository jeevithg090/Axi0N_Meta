# shellcheck shell=bash
# Shared helpers for the Rulecam container entrypoints (api-entrypoint.sh, sam-entrypoint.sh).
# Sourced, never run. Portable to bash 3.2 (macOS runs the entrypoint tests) and GNU/BSD stat.
#
# Secrets are read from files or the environment into the environment only: never echoed, never
# put in argv. Log lines name a secret's variable and source, never its value.

ENTRYPOINT_NAME="${ENTRYPOINT_NAME:-rulecam-entrypoint}"

log() { printf '%s %s: %s\n' "$(date -u +%FT%TZ)" "$ENTRYPOINT_NAME" "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

file_mode() {  # file_mode <path>: octal permission bits (GNU or BSD stat)
  stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1"
}

file_uid() {  # file_uid <path>: numeric owner (GNU or BSD stat)
  stat -c %u "$1" 2>/dev/null || stat -f %u "$1"
}

upper() { printf '%s' "$1" | tr '[:lower:]' '[:upper:]'; }

is_placeholder() {  # an unresolved RunPod secret reference, e.g. "{{ RUNPOD_SECRET_x }}"
  case "$1" in
    *"{{"*RUNPOD_SECRET_*"}}"*) return 0 ;;
    *) return 1 ;;
  esac
}

check_secret_mode() {  # check_secret_mode <path>: want 600; tighten it, or warn if the FS ignores it
  [[ "$(file_mode "$1")" == "600" ]] && return 0
  chmod 600 "$1" 2>/dev/null || true
  [[ "$(file_mode "$1")" == "600" ]] && return 0
  # RunPod network volumes (MooseFS) ignore chmod, so the mode can never be 600 there.
  log "WARNING: $1 has mode $(file_mode "$1") and cannot be made 600 (a volume that ignores modes); keep the pod single-user"
}

load_secret() {  # load_secret <VAR> <file>: export VAR from the environment, else from the file
  local var="$1" path="$2" current value
  current="${!var:-}"
  if [[ -n "$current" ]] && ! is_placeholder "$current"; then
    log "secret $var from the environment"
    return 0
  fi
  if [[ -n "$current" ]]; then
    log "WARNING: $var is an unresolved RunPod secret reference; ignoring it"
    unset "$var"
  fi
  [[ -f "$path" ]] || return 0
  if [[ ! -s "$path" ]]; then
    log "WARNING: $path is empty; $var not set"
    return 0
  fi
  check_secret_mode "$path"
  value="$(<"$path")"
  value="${value%"${value##*[![:space:]]}"}"  # drop trailing whitespace (a trailing newline, CR)
  export "$var=$value"
  log "secret $var from $path"
}

drop_privileges() {  # drop_privileges <user> <dir> <cmd...>: exec cmd as user when root
  local user="$1" dir="$2"
  shift 2
  if [[ "$(id -u)" != "0" ]] || ! id "$user" >/dev/null 2>&1; then
    exec "$@"
  fi
  local uid
  uid="$(id -u "$user")"
  mkdir -p "$dir"
  if [[ "$(file_uid "$dir")" != "$uid" ]]; then
    chown -R "$user:" "$dir" 2>/dev/null || true
  fi
  if setpriv --reuid="$user" --regid="$user" --init-groups test -w "$dir" 2>/dev/null; then
    HOME="$(getent passwd "$user" | cut -d: -f6)" \
      exec setpriv --reuid="$user" --regid="$user" --init-groups "$@"
  fi
  log "WARNING: $dir is not writable by $user (a volume that ignores ownership); running as root"
  exec "$@"
}

http_ok() {  # http_ok <url>: exit 0 when the URL answers 2xx (python stdlib; no curl needed)
  python3 - "$1" <<'PY'
import sys
import urllib.request

try:
    with urllib.request.urlopen(sys.argv[1], timeout=4) as response:  # noqa: S310 - loopback
        sys.exit(0 if 200 <= response.status < 300 else 1)
except Exception:  # noqa: BLE001 - any failure means unhealthy
    sys.exit(1)
PY
}
