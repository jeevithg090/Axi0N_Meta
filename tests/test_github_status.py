from __future__ import annotations

import importlib.util
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ci" / "github_status.py"

ENV = {
    "GITHUB_REPO": "jeevithg090/Axi0N_Meta",
    "GITHUB_STATUS_TOKEN": "token-value",
    "CI_COMMIT_SHA": "abc123",
    "CI_JOB_NAME": "check",
    "CI_JOB_URL": "https://gitea.com/x/y/actions/runs/1",
}


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("github_status", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("job_status", "expected"),
    [
        ("success", "success"),
        ("failure", "failure"),
        ("failed", "failure"),
        ("cancelled", "error"),
        ("canceled", "error"),
        ("weird", "error"),
    ],
)
def test_maps_job_status(job_status: str, expected: str) -> None:
    assert load_script().map_job_status(job_status) == expected


def test_request_targets_the_commit_with_a_gitea_context() -> None:
    request = load_script().build_request("success", ENV)
    body = json.loads(request.data)
    assert request.full_url == (
        "https://api.github.com/repos/jeevithg090/Axi0N_Meta/statuses/abc123"
    )
    assert request.get_method() == "POST"
    assert body["context"] == "gitea/check"
    assert body["state"] == "success"
    assert body["target_url"] == ENV["CI_JOB_URL"]
    assert "token-value" not in request.data.decode()


def test_from_job_reports_the_mapped_state() -> None:
    sent: list[urllib.request.Request] = []

    def send(request: urllib.request.Request) -> int:
        sent.append(request)
        return 201

    code = load_script().run(["x", "from-job"], {**ENV, "CI_JOB_STATUS": "failed"}, send)
    assert code == 0
    payload = sent[0].data
    assert isinstance(payload, bytes)
    assert json.loads(payload)["state"] == "failure"


def test_skips_quietly_without_a_token() -> None:
    def send(request: urllib.request.Request) -> int:
        raise AssertionError("must not send")

    env = {k: v for k, v in ENV.items() if k != "GITHUB_STATUS_TOKEN"}
    assert load_script().run(["x", "pending"], env, send) == 0


def test_network_errors_never_fail_the_job() -> None:
    def send(request: urllib.request.Request) -> int:
        raise OSError("connection refused")

    assert load_script().run(["x", "pending"], ENV, send, lambda s: None) == 0


def test_bad_usage_returns_2() -> None:
    assert load_script().run(["x", "done"], ENV, lambda r: 201) == 2


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://api.github.com", code, "err", {}, None)  # type: ignore[arg-type]


def test_retries_a_github_500_then_reports() -> None:
    attempts: list[int] = []
    sleeps: list[float] = []

    def send(request: urllib.request.Request) -> int:
        attempts.append(1)
        if len(attempts) < 3:
            raise _http_error(500)
        return 201

    code = load_script().run(
        ["x", "from-job"], {**ENV, "CI_JOB_STATUS": "success"}, send, sleeps.append
    )
    assert code == 0
    assert len(attempts) == 3
    assert sleeps == [1.0, 3.0]


def test_does_not_retry_a_client_error() -> None:
    attempts: list[int] = []

    def send(request: urllib.request.Request) -> int:
        attempts.append(1)
        raise _http_error(422)

    assert load_script().run(["x", "pending"], ENV, send, lambda s: None) == 0
    assert len(attempts) == 1


def test_gives_up_after_three_attempts_without_failing_the_job() -> None:
    attempts: list[int] = []

    def send(request: urllib.request.Request) -> int:
        attempts.append(1)
        raise OSError("connection reset")

    assert load_script().run(["x", "pending"], ENV, send, lambda s: None) == 0
    assert len(attempts) == 3


def test_retries_rate_limits() -> None:
    assert load_script().is_retryable(_http_error(429))
    assert not load_script().is_retryable(_http_error(404))


def test_status_prefix_is_configurable() -> None:
    assert load_script().context({**ENV, "CI_STATUS_PREFIX": "ci"}) == "ci/check"
