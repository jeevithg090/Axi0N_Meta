#!/usr/bin/env bash
# Claude Code PreToolUse hook (.claude/settings.json): before a Bash call that runs `git push`,
# run `make lint type` in the project and block the push (exit 2, reason on stderr) when it
# fails. Every other command passes straight through (a `git` command merely mentioning `push`
# also runs the check: harmless). Reads the hook's JSON on stdin.
set -uo pipefail

command=$(python3 -c 'import json, sys; print(json.load(sys.stdin).get("tool_input", {}).get("command", ""))' 2> /dev/null) || exit 0
if ! printf '%s\n' "$command" | grep -Eq '(^|[;&|([:space:]])git[[:space:]]+([^;&|]*[[:space:]])?push([[:space:];&|)]|$)'; then
  exit 0
fi
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
if output=$(make lint type 2>&1); then
  exit 0
fi
{
  echo "git push blocked: \`make lint type\` failed. Fix it (\`make fmt\` fixes formatting), then push again."
  printf '%s\n' "$output" | tail -n 30
} >&2
exit 2
