from __future__ import annotations

import importlib.util
import json
import sys
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
    "CI_JOB_URL": "https://gitlab.com/x/y/-/jobs/1",
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
    [("success", "success"), ("failed", "failure"), ("canceled", "error"), ("weird", "error")],
)
def test_maps_gitlab_job_status(job_status: str, expected: str) -> None:
    assert load_script().map_job_status(job_status) == expected


def test_request_targets_the_commit_with_a_gitlab_context() -> None:
    request = load_script().build_request("success", ENV)
    body = json.loads(request.data)
    assert request.full_url == (
        "https://api.github.com/repos/jeevithg090/Axi0N_Meta/statuses/abc123"
    )
    assert request.get_method() == "POST"
    assert body["context"] == "gitlab/check"
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

    assert load_script().run(["x", "pending"], ENV, send) == 0


def test_bad_usage_returns_2() -> None:
    assert load_script().run(["x", "done"], ENV, lambda r: 201) == 2
