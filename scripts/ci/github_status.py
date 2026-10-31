"""Report a CI job's status to the matching GitHub commit, so GitHub PRs show checks.

Code and pull requests live on GitHub; CI runs on Gitea Actions (`.gitea/workflows/ci.yml`). Every
job calls this script first (state `pending`) and last (state from `$CI_JOB_STATUS`). The status
appears on the GitHub PR as `<CI_STATUS_PREFIX>/<job name>`, i.e. `gitea/check`.

Usage (stdlib only, so it runs before dependencies are installed):

    python3 scripts/ci/github_status.py pending
    python3 scripts/ci/github_status.py from-job     # maps $CI_JOB_STATUS

Required variables: `GITHUB_REPO` (owner/name) and `GITHUB_STATUS_TOKEN` (a fine-grained
GitHub token with "Commit statuses: read and write" on that repository, stored masked). When the
token is absent the script skips quietly, so pipelines still run before the bridge is set up.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Literal

GitHubState = Literal["pending", "success", "failure", "error"]

MAX_ATTEMPTS = 3
BACKOFF_S = (1.0, 3.0)

DEFAULT_PREFIX = "gitea"

# Gitea/GitHub Actions `job.status` values, plus GitLab's `$CI_JOB_STATUS` spellings.
_JOB_TO_GITHUB: dict[str, GitHubState] = {
    "success": "success",
    "failure": "failure",
    "failed": "failure",
    "cancelled": "error",
    "canceled": "error",
    "skipped": "error",
}
_DESCRIPTIONS: dict[GitHubState, str] = {
    "pending": "Running in CI",
    "success": "Passed in CI",
    "failure": "Failed in CI",
    "error": "Did not complete in CI",
}


def map_job_status(job_status: str) -> GitHubState:
    """Map the job's final status to a GitHub commit-status state.

    Args:
        job_status: The value of `$CI_JOB_STATUS` (`success`, `failure`, `cancelled`, ...).

    Returns:
        The GitHub state; unknown values map to `error`.
    """
    return _JOB_TO_GITHUB.get(job_status, "error")


def context(env: Mapping[str, str]) -> str:
    """Return the GitHub status context for this job, e.g. `gitea/check`."""
    return f"{env.get('CI_STATUS_PREFIX') or DEFAULT_PREFIX}/{env.get('CI_JOB_NAME', 'job')}"


def build_request(state: GitHubState, env: Mapping[str, str]) -> urllib.request.Request:
    """Build the GitHub commit-status API request for the current job.

    Args:
        state: The GitHub state to report.
        env: The CI environment (needs `GITHUB_REPO`, `GITHUB_STATUS_TOKEN`, `CI_COMMIT_SHA`,
            `CI_JOB_NAME`, `CI_JOB_URL`; optional `CI_STATUS_PREFIX`).

    Returns:
        A POST request; the token is only placed in the Authorization header.
    """
    url = f"https://api.github.com/repos/{env['GITHUB_REPO']}/statuses/{env['CI_COMMIT_SHA']}"
    body = {
        "state": state,
        "context": context(env),
        "description": _DESCRIPTIONS[state],
        "target_url": env.get("CI_JOB_URL", ""),
    }
    return urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {env['GITHUB_STATUS_TOKEN']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


def is_retryable(exc: OSError) -> bool:
    """Return True for transient failures: network errors, 429 and 5xx replies."""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code == 429 or exc.code >= 500  # noqa: PLR2004 - HTTP status classes
    return True


def send_with_retry(
    request: urllib.request.Request,
    send: Callable[[urllib.request.Request], int],
    sleep: Callable[[float], None],
) -> int:
    """Send, retrying transient failures up to `MAX_ATTEMPTS` times with a short backoff.

    Raises:
        OSError: The last error, when every attempt failed or the error is not retryable.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return send(request)
        except OSError as exc:
            if attempt == MAX_ATTEMPTS or not is_retryable(exc):
                raise
            print(f"github_status: attempt {attempt} failed ({exc}); retrying")
            sleep(BACKOFF_S[attempt - 1])
    msg = "MAX_ATTEMPTS must be at least 1"
    raise ValueError(msg)


def run(
    argv: list[str],
    env: Mapping[str, str],
    send: Callable[[urllib.request.Request], int],
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Report one status; return a process exit code.

    Transient failures (network errors, 429, 5xx) are retried. Reporting never fails the CI
    job: a final error is printed and exit code 0 is returned, because the job's own result is
    what matters.

    Args:
        argv: Command-line arguments; `argv[1]` is `pending` or `from-job`.
        env: The CI environment.
        send: Sends the request and returns the HTTP status code.
        sleep: Backoff sleep, injectable for tests.

    Returns:
        0 on success or skip, 2 on bad usage.
    """
    if len(argv) != 2 or argv[1] not in {"pending", "from-job"}:  # noqa: PLR2004
        print("usage: github_status.py pending|from-job")
        return 2
    if not env.get("GITHUB_STATUS_TOKEN") or not env.get("GITHUB_REPO"):
        print("github_status: GITHUB_STATUS_TOKEN/GITHUB_REPO not set; skipping")
        return 0
    state: GitHubState = (
        "pending" if argv[1] == "pending" else map_job_status(env.get("CI_JOB_STATUS", ""))
    )
    try:
        code = send_with_retry(build_request(state, env), send, sleep)
    except OSError as exc:
        print(f"github_status: could not report {state}: {exc}")
        return 0
    print(f"github_status: reported {state} for {context(env)} (HTTP {code})")
    return 0


def _send(request: urllib.request.Request) -> int:
    with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
        status: int = response.status
        return status


if __name__ == "__main__":
    sys.exit(run(sys.argv, os.environ, _send))
