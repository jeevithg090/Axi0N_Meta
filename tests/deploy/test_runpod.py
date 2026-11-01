"""`scripts/deploy/runpod.py` (WS5): planning, idempotency and stock-out failover, against a
fake RunPod API on localhost. Nothing here reaches the network."""

from __future__ import annotations

import importlib.util
import io
import sys
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.deploy.fake_runpod import FakeAccount, FakeRunPod, stocked_account

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "deploy" / "runpod.py"
API_KEY = "rp-test-api-key-000"
GLIMMER = "glimmer-key-abcdef123"
SAM = "sam-key-abcdef456789"
META = "meta-key-abcdef9876"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("runpod_deploy", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rp = load_script()


@pytest.fixture
def secrets_dir(tmp_path: Path) -> Path:
    path = tmp_path / "keys"
    path.mkdir()
    for name, value in (
        ("rulecam-glimmer.key", GLIMMER),
        ("rulecam-sam.key", SAM),
        ("rulecam-meta.key", META),
    ):
        (path / name).write_text(value + "\n")
        (path / name).chmod(0o600)
    return path


@pytest.fixture
def account() -> FakeAccount:
    return stocked_account()


@pytest.fixture
def fake(account: FakeAccount) -> Iterator[FakeRunPod]:
    server = FakeRunPod(account)
    yield server
    server.close()


def client(fake: FakeRunPod, *, dry_run: bool = False) -> tuple[Any, io.StringIO]:
    out = io.StringIO()
    reporter = rp.Reporter(out=out)
    return rp.RunPod(rp.http_transport(fake.url, API_KEY), reporter, dry_run=dry_run), out


def opts(secrets_dir: Path, **changes: Any) -> Any:
    return replace(rp.Options(secrets_dir=secrets_dir, wait=False), **changes)


def test_dry_run_reads_only_and_redacts(
    fake: FakeRunPod, account: FakeAccount, secrets_dir: Path
) -> None:
    runpod, out = client(fake, dry_run=True)
    stack = rp.up(runpod, opts(secrets_dir, dry_run=True))
    assert account.writes() == []
    assert {c.method for c in account.calls} == {"GET"}
    assert all(c.user_agent.startswith("rulecam-deploy/") for c in account.calls)
    assert all(c.authorization == f"Bearer {API_KEY}" for c in account.calls)
    text = out.getvalue()
    for secret in (API_KEY, GLIMMER, SAM, META):
        assert secret not in text
    assert "would call POST /v2/account/secrets" in text
    assert '"value": "<redacted>"' in text
    assert "would call POST /v2/network-volumes" in text
    assert '"dataCenter": "EU-RO-1"' in text  # US-TX-3 has CPUs but no network volumes
    assert "{{ RUNPOD_SECRET_rulecam_glimmer_api_key }}" in text
    # The first stocked candidate: A6000 in EU-RO-1 (US-KS-2 has none of it).
    assert "would place NVIDIA RTX A6000 in EU-RO-1" in text
    assert set(stack.urls) == {"glimmer", "sam", "api"}


def test_up_on_a_clean_account_fails_over_and_wires_the_api(
    fake: FakeRunPod, account: FakeAccount, secrets_dir: Path
) -> None:
    account.out_of_stock = {"NVIDIA RTX A6000", "cpu3c"}
    runpod, out = client(fake)
    stack = rp.up(runpod, opts(secrets_dir))
    assert account.secret_values == {
        "rulecam_glimmer_api_key": GLIMMER,
        "rulecam_sam_api_key": SAM,
        "rulecam_meta_api_key": META,
    }
    [volume] = account.volumes
    assert volume["dataCenter"] == "EU-RO-1"
    glimmer, sam, api = (stack.pods[r] for r in ("glimmer", "sam", "api"))
    # A6000 is out of stock: L40S, most stock first (EU-RO-1 HIGH before US-KS-2 MEDIUM).
    assert glimmer["gpu"]["id"] == "NVIDIA L40S"
    assert glimmer["dataCenterIds"] == ["EU-RO-1"]
    assert glimmer["image"] == rp.GLIMMER_IMAGE
    assert glimmer["env"]["LLAMA_API_KEY"] == "{{ RUNPOD_SECRET_rulecam_glimmer_api_key }}"
    assert "--api-key" not in glimmer["cmd"]  # the key is env only, never argv
    assert sam["image"] == "ghcr.io/jeevithg090/rulecam-sam:latest"
    assert "registry" not in sam
    assert sam["ports"] == ["8090/http"]
    assert api["cpu"] == {"id": "cpu5c", "vcpuCount": 8}
    assert api["dataCenterIds"] == ["EU-RO-1"]
    assert api["mounts"] == {"network": [{"volumeId": volume["id"], "path": "/workspace"}]}
    env = api["env"]
    assert env["GLIMMER_BASE_URL"] == f"https://{glimmer['id']}-8080.proxy.runpod.net/v1"
    assert env["SAM_BASE_URL"] == f"https://{sam['id']}-8090.proxy.runpod.net"
    assert env["META_API_KEY"] == "{{ RUNPOD_SECRET_rulecam_meta_api_key }}"
    assert env["RULECAM_MODE"] == "live"
    assert stack.urls["api"] == f"https://{api['id']}-8000.proxy.runpod.net"
    text = out.getvalue()
    assert "no placement for NVIDIA RTX A6000 in EU-RO-1 (400)" in text
    for secret in (GLIMMER, SAM, META):
        assert secret not in text


def test_second_up_changes_nothing(
    fake: FakeRunPod, account: FakeAccount, secrets_dir: Path
) -> None:
    runpod, _ = client(fake)
    first = rp.up(runpod, opts(secrets_dir))
    account.calls.clear()
    second = rp.up(runpod, opts(secrets_dir))
    assert account.writes() == []
    assert {r: p["id"] for r, p in second.pods.items()} == {
        r: p["id"] for r, p in first.pods.items()
    }
    assert len(account.pods) == 3


def test_stopped_pods_start_and_a_new_tag_patches_the_images(
    fake: FakeRunPod, account: FakeAccount, secrets_dir: Path
) -> None:
    runpod, out = client(fake)
    rp.up(runpod, opts(secrets_dir))
    rp.down(runpod, rp.Options(), rp.ROLES, terminate=False)
    assert {p["status"] for p in account.pods} == {"EXITED"}
    account.calls.clear()
    rp.up(runpod, opts(secrets_dir, image_tag="sha-abc"))
    writes = [(c.method, c.path, c.body) for c in account.writes()]
    patched = {path: body for method, path, body in writes if method == "PATCH"}
    assert sorted(b["image"] for b in patched.values()) == [
        "ghcr.io/jeevithg090/rulecam-api:sha-abc",
        "ghcr.io/jeevithg090/rulecam-sam:sha-abc",
    ]
    assert sum(1 for m, p, b in writes if p.endswith("/action") and b == {"action": "start"}) == 3
    assert {p["status"] for p in account.pods} == {"RUNNING"}
    assert "rulecam-api: updating image" in out.getvalue()


def test_env_drift_is_patched_keeping_injected_vars(
    fake: FakeRunPod, account: FakeAccount, secrets_dir: Path
) -> None:
    runpod, _ = client(fake)
    stack = rp.up(runpod, opts(secrets_dir, with_sam=False, with_glimmer=False))
    api = account.pod(stack.pods["api"]["id"])
    assert api is not None
    assert "SAM_BASE_URL" not in api["env"]
    api["env"]["PUBLIC_KEY"] = "ssh-ed25519 AAAA"  # RunPod injects this
    rp.up(runpod, opts(secrets_dir, with_glimmer=False))
    assert api["env"]["SAM_BASE_URL"].endswith("-8090.proxy.runpod.net")
    assert api["env"]["PUBLIC_KEY"] == "ssh-ed25519 AAAA"


def test_a_pod_that_cannot_start_says_what_to_do(
    fake: FakeRunPod, account: FakeAccount, secrets_dir: Path
) -> None:
    runpod, _ = client(fake)
    rp.up(runpod, opts(secrets_dir, with_glimmer=False))
    rp.down(runpod, rp.Options(), ("sam",), terminate=False)
    account.refuse_start = True
    with pytest.raises(rp.DeployError, match=r"could not start .* --only sam --terminate"):
        rp.up(runpod, opts(secrets_dir, with_glimmer=False))


def test_pods_in_error_are_reported(
    fake: FakeRunPod, account: FakeAccount, secrets_dir: Path
) -> None:
    runpod, _ = client(fake)
    rp.up(runpod, opts(secrets_dir, with_glimmer=False, with_sam=False))
    account.pods[0]["status"] = "ERROR"
    with pytest.raises(rp.DeployError, match="is in ERROR"):
        rp.up(runpod, opts(secrets_dir, with_glimmer=False, with_sam=False))


def test_nothing_in_stock_lists_every_attempt(
    fake: FakeRunPod, account: FakeAccount, secrets_dir: Path
) -> None:
    account.out_of_stock = {"NVIDIA RTX A6000", "NVIDIA L40S"}
    runpod, _ = client(fake)
    with pytest.raises(rp.DeployError, match=r"(?s)rulecam-glimmer: no GPU.*L40S in US-KS-2"):
        rp.up(runpod, opts(secrets_dir))


def test_sync_secrets_rotates_and_missing_required_keys_stop(
    fake: FakeRunPod, account: FakeAccount, secrets_dir: Path
) -> None:
    runpod, _ = client(fake)
    rp.ensure_secrets(runpod, opts(secrets_dir))
    (secrets_dir / "rulecam-sam.key").write_text("rotated-sam-key-0001")
    rp.ensure_secrets(runpod, opts(secrets_dir, sync_secrets=True))
    assert account.secret_values["rulecam_sam_api_key"] == "rotated-sam-key-0001"
    (secrets_dir / "rulecam-glimmer.key").unlink()
    with pytest.raises(rp.DeployError, match=r"rulecam-glimmer\.key"):
        rp.ensure_secrets(runpod, opts(secrets_dir))


def test_inline_secrets_never_reach_the_output(
    fake: FakeRunPod, account: FakeAccount, secrets_dir: Path
) -> None:
    runpod, out = client(fake, dry_run=True)
    rp.up(runpod, opts(secrets_dir, inline_secrets=True, dry_run=True))
    text = out.getvalue()
    assert "/v2/account/secrets" not in text
    for secret in (GLIMMER, SAM, META):
        assert secret not in text
    assert '"LLAMA_API_KEY": "<redacted>"' in text


def test_archive_source_uses_the_base_image_and_boot_hook(
    fake: FakeRunPod, account: FakeAccount, secrets_dir: Path
) -> None:
    runpod, out = client(fake, dry_run=True)
    options = opts(secrets_dir, api_source="archive", dry_run=True, with_sam=False)
    stack = rp.up(runpod, options)
    rp.upload_archive(runpod, stack.pods["api"]["id"], options)
    text = out.getvalue()
    assert '"image": "runpod/base:1.4.0-ubuntu2204"' in text
    assert "deploy/api-boot.sh" in text
    assert "git archive HEAD" in text


def test_archive_upload_needs_direct_ssh(
    fake: FakeRunPod, account: FakeAccount, secrets_dir: Path
) -> None:
    runpod, _ = client(fake)
    stack = rp.up(runpod, opts(secrets_dir, api_source="archive", with_sam=False))
    with pytest.raises(rp.DeployError, match="no direct SSH endpoint"):
        rp.upload_archive(runpod, stack.pods["api"]["id"], opts(secrets_dir))


def test_down_stops_or_terminates_and_keeps_the_volume(
    fake: FakeRunPod, account: FakeAccount, secrets_dir: Path
) -> None:
    runpod, out = client(fake)
    rp.up(runpod, opts(secrets_dir))
    rp.down(runpod, rp.Options(), ("glimmer",), terminate=False)
    rp.down(runpod, rp.Options(), ("glimmer",), terminate=False)
    assert "already EXITED" in out.getvalue()
    rp.down(runpod, rp.Options(), ("sam", "api"), terminate=True)
    assert [c.method for c in account.writes()][-2:] == ["DELETE", "DELETE"]
    assert len(account.volumes) == 1
    rp.down(runpod, rp.Options(), ("sam",), terminate=False)
    assert "rulecam-sam: not found" in out.getvalue()


def test_status_rows_and_exit_code(
    fake: FakeRunPod, account: FakeAccount, secrets_dir: Path
) -> None:
    runpod, _ = client(fake)
    rp.up(runpod, opts(secrets_dir, with_sam=False))
    rows = list(rp.status_rows(runpod, rp.Options(), probe=lambda url: 200))
    assert [(r[0], r[2], r[4]) for r in rows] == [
        ("rulecam-api", "RUNNING", "ok"),
        ("rulecam-glimmer", "RUNNING", "ok"),
        ("rulecam-sam", "absent", "-"),
        ("rulecam-gpu", "absent", "-"),
    ]
    assert rows[0][3] == "$0.060"
    down = list(rp.status_rows(runpod, rp.Options(), probe=lambda url: 503))
    assert down[0][4] == "down (503)"
    assert rp.print_status(runpod, rp.Options()) == 1  # health unreachable


def test_main_dry_run_end_to_end(  # noqa: PLR0917 - pytest fixtures
    fake: FakeRunPod,
    account: FakeAccount,
    secrets_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(f'apikey = "{API_KEY}"\napiurl = "https://api.runpod.io/graphql"\n')
    monkeypatch.setenv("RUNPOD_REST_URL", fake.url)
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    code = rp.main(
        ["up", "--dry-run", "--config", str(config), "--secrets-dir", str(secrets_dir), "--vercel"]
    )
    text = capsys.readouterr().out
    assert code == 0
    assert account.writes() == []
    assert "DRY RUN" in text
    assert "would run: vercel env add NEXT_PUBLIC_RULECAM_API production <<< https://" in text
    assert API_KEY not in text
    assert rp.main(["down", "--terminate", "--config", str(config)]) == 2
    assert rp.main(["down", "--dry-run", "--config", str(config)]) == 0
    assert rp.main(["status", "--dry-run", "--config", str(config)]) == 1


def test_main_reports_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    assert rp.main(["status", "--config", str(tmp_path / "missing.toml")]) == 1
    assert "ERROR: no RUNPOD_API_KEY" in capsys.readouterr().out


def test_offline_dry_run_plans_against_an_empty_account(
    secrets_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    out = io.StringIO()
    reporter = rp.Reporter(out=out)
    args = rp.parser().parse_args(["up", "--dry-run", "--config", str(tmp_path / "none.toml")])
    runpod = rp.make_client(args, reporter)
    stack = rp.up(runpod, opts(secrets_dir, dry_run=True))
    text = out.getvalue()
    assert "planning against an empty account" in text
    assert "<data-center>" in text
    assert "would place NVIDIA RTX A6000 in any data center" in text
    assert set(stack.urls) == {"glimmer", "sam", "api"}
    with pytest.raises(rp.DeployError):
        rp.offline_transport("POST", "/v2/pods", {})


def test_read_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "config.toml"
    config.write_text('apikey = " k1 "\n')
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    assert rp.read_api_key(config) == "k1"
    monkeypatch.setenv("RUNPOD_API_KEY", "k2")
    assert rp.read_api_key(config) == "k2"
    monkeypatch.delenv("RUNPOD_API_KEY")
    config.write_text('apiurl = "x"\n')
    with pytest.raises(rp.DeployError, match="has no apikey"):
        rp.read_api_key(config)
    config.write_text("not toml [")
    with pytest.raises(rp.DeployError, match="unreadable"):
        rp.read_api_key(config)


def test_gpu_attempts_order_and_fallback() -> None:
    catalog = stocked_account().gpus
    assert rp.gpu_attempts(catalog, ("NVIDIA L4", "NVIDIA L40S", "NVIDIA RTX A6000")) == [
        ("NVIDIA L40S", "EU-RO-1"),
        ("NVIDIA L40S", "US-KS-2"),
        ("NVIDIA RTX A6000", "EU-RO-1"),
    ]
    assert rp.gpu_attempts(catalog, ("NVIDIA L40S",), limit=1) == [("NVIDIA L40S", "EU-RO-1")]
    assert rp.gpu_attempts([], ("A", "B")) == [("A", None), ("B", None)]


def test_cpu_datacenters_prefer_the_owner_and_need_volumes() -> None:
    catalog = stocked_account().cpus
    assert rp.cpu_datacenters(
        catalog, ("cpu3c", "cpu5c"), with_volumes={"EU-RO-1"}, preferred=()
    ) == ["EU-RO-1"]
    assert rp.cpu_datacenters(
        catalog, ("cpu3c", "cpu5c"), with_volumes=None, preferred=("EU-RO-1",)
    ) == ["EU-RO-1", "US-TX-3"]
    assert rp.cpu_datacenters([], ("cpu3c",), with_volumes=None, preferred=("X",)) == ["X"]


def test_api_errors_carry_the_detail(fake: FakeRunPod) -> None:
    send = rp.http_transport(fake.url, API_KEY)
    with pytest.raises(rp.ApiError) as raised:
        send("GET", "/v2/nope?x=1", None)
    assert raised.value.status == 404
    assert str(raised.value) == "GET /v2/nope -> 404: /v2/nope"
    with pytest.raises(rp.DeployError, match=r"refused|Errno"):
        rp.http_transport("http://127.0.0.1:9", API_KEY, timeout_s=2)("GET", "/v2/pods", None)


def test_non_placement_errors_are_not_retried(fake: FakeRunPod) -> None:
    def unauthorized(method: str, path: str, body: Any) -> Any:
        raise rp.ApiError(method, path, 401, "bad key")

    runpod = rp.RunPod(unauthorized, rp.Reporter(out=io.StringIO()), dry_run=False)
    attempts = [({"gpu": {"id": "A"}}, None), ({"gpu": {"id": "B"}}, None)]
    with pytest.raises(rp.ApiError, match="401"):
        rp.place(runpod, "x", {"name": "x"}, attempts)


def test_catalog_failure_falls_back_to_the_scheduler() -> None:
    def broken(method: str, path: str, body: Any) -> Any:
        raise rp.DeployError("down")

    runpod = rp.RunPod(broken, rp.Reporter(out=io.StringIO()), dry_run=True)
    assert runpod.catalog("gpus") == []
    assert runpod.datacenters_with_volumes() is None


def test_health_probe_and_waiting(fake: FakeRunPod) -> None:
    assert rp.http_status(f"{fake.url}/health") == 200
    assert rp.http_status(f"{fake.url}/v2/nope") == 404
    assert rp.http_status("http://127.0.0.1:9/health", timeout_s=1) is None
    calls = iter([False, False, True])
    sleeps: list[float] = []
    assert rp.wait_until(lambda: next(calls), timeout_s=60, sleep=sleeps.append)
    assert len(sleeps) == 2
    assert not rp.wait_until(lambda: False, timeout_s=0, sleep=sleeps.append)


def test_wait_healthy_polls_each_pod(
    fake: FakeRunPod, secrets_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runpod, out = client(fake)
    stack = rp.up(runpod, opts(secrets_dir, with_sam=False))
    monkeypatch.setattr(rp, "http_status", lambda url: 200 if "8000" in url else 503)
    healthy = rp.wait_healthy(runpod, stack, opts(secrets_dir, wait_timeout_s=0))
    assert healthy == {"glimmer": False, "api": True}
    assert "glimmer: NOT healthy yet" in out.getvalue()


def test_vercel_root_and_commands(tmp_path: Path) -> None:
    (tmp_path / ".vercel").mkdir()
    (tmp_path / ".vercel" / "project.json").write_text("{}")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert rp.vercel_root(nested) == tmp_path
    assert rp.vercel_root(Path("/")) is None
    [rm, add, deploy] = rp.vercel_commands("https://p-8000.proxy.runpod.net")
    assert add == (
        ["vercel", "env", "add", "NEXT_PUBLIC_RULECAM_API", "production"],
        "https://p-8000.proxy.runpod.net\n",
    )
    assert deploy[0][-2:] == ["--prod", "--yes"]
    assert rm[1] is None


def test_update_vercel_needs_the_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rp.shutil, "which", lambda name: None)
    with pytest.raises(rp.DeployError, match="vercel CLI"):
        rp.update_vercel(rp.Reporter(out=io.StringIO()), "https://x", dry_run=False)


def test_redact_body() -> None:
    body = {
        "name": "n",
        "value": "v",
        "env": {"A_KEY": "x", "MODE": "live", "B_TOKEN": "{{ RUNPOD_SECRET_b }}"},
    }
    assert rp.redact_body(body) == {
        "name": "n",
        "value": "<redacted>",
        "env": {"A_KEY": "<redacted>", "MODE": "live", "B_TOKEN": "{{ RUNPOD_SECRET_b }}"},
    }


def test_registry_credential_is_passed_for_ghcr_images(secrets_dir: Path) -> None:
    options = opts(secrets_dir, registry="reg123")
    assert rp.sam_body(options, rp.SecretRefs())["registry"] == "reg123"
    assert rp.api_body(options, {}, "vol")["registry"] == "reg123"
    assert "registry" not in rp.glimmer_body(options, rp.SecretRefs())


# ---------------------------------------------------------------------------------------------
# --one-gpu (WS3/WS5): Glimmer and SAM image mode on one 48 GB pod


def test_one_gpu_dry_run_plans_a_single_pod_and_wires_both_urls(
    fake: FakeRunPod, account: FakeAccount, secrets_dir: Path
) -> None:
    runpod, out = client(fake, dry_run=True)
    stack = rp.up(runpod, opts(secrets_dir, dry_run=True, one_gpu=True))
    assert account.writes() == []
    assert set(stack.pods) == {"gpu", "api"}
    assert set(stack.urls) == {"glimmer", "sam", "api"}
    gpu, api = stack.pods["gpu"], stack.pods["api"]
    assert gpu["name"] == "rulecam-gpu"
    assert gpu["image"] == "ghcr.io/jeevithg090/rulecam-gpu:latest"
    assert gpu["ports"] == ["8080/http", "8090/http"]
    assert gpu["env"] == {
        "LLAMA_API_KEY": "{{ RUNPOD_SECRET_rulecam_glimmer_api_key }}",
        "SAM_API_KEY": "{{ RUNPOD_SECRET_rulecam_sam_api_key }}",
    }
    assert gpu["gpu"]["id"] == "NVIDIA RTX A6000"  # the first 48 GB card with stock
    assert stack.urls["glimmer"] == f"https://{gpu['id']}-8080.proxy.runpod.net"
    assert stack.urls["sam"] == f"https://{gpu['id']}-8090.proxy.runpod.net"
    env = api["env"]
    assert env["GLIMMER_BASE_URL"] == stack.urls["glimmer"] + "/v1"
    assert env["SAM_BASE_URL"] == stack.urls["sam"]
    assert env["RULECAM_GPU_USD_PER_HOUR"] == rp.PRICE_PLACEHOLDER  # no pod yet, no price
    assert env["RULECAM_IDLE_STOP_GPU_PODS"] == "rulecam-gpu"
    text = out.getvalue()
    assert "would place NVIDIA RTX A6000 in EU-RO-1" in text
    assert "rulecam-glimmer: creating" not in text
    assert "rulecam-sam: creating" not in text
    for secret in (API_KEY, GLIMMER, SAM, META):
        assert secret not in text


def test_one_gpu_falls_back_across_48_gb_cards_and_prices_the_api(
    fake: FakeRunPod, account: FakeAccount, secrets_dir: Path
) -> None:
    account.out_of_stock = {"NVIDIA RTX A6000"}
    (secrets_dir / "rulecam-hf.token").write_text("hf-token-abc123\n")
    runpod, out = client(fake)
    stack = rp.up(runpod, opts(secrets_dir, one_gpu=True))
    gpu, api = stack.pods["gpu"], stack.pods["api"]
    # A6000 has no stock, A40 is not in the catalog: L40S, most stock first.
    assert (gpu["gpu"]["id"], gpu["dataCenterIds"]) == ("NVIDIA L40S", ["EU-RO-1"])
    assert gpu["env"]["HF_TOKEN"] == rp.HF_TOKEN.placeholder()
    assert api["env"]["RULECAM_GPU_USD_PER_HOUR"] == "0.5"  # the fake's GPU pod price
    assert api["env"]["SAM_BASE_URL"] == f"https://{gpu['id']}-8090.proxy.runpod.net"
    assert len(account.pods) == 2
    assert "hf-token-abc123" not in out.getvalue()
    account.calls.clear()
    rp.up(runpod, opts(secrets_dir, one_gpu=True))
    assert account.writes() == []  # idempotent


def test_one_gpu_warns_about_the_two_pods_it_replaces(
    fake: FakeRunPod, account: FakeAccount, secrets_dir: Path
) -> None:
    runpod, out = client(fake)
    rp.up(runpod, opts(secrets_dir))
    rp.up(runpod, opts(secrets_dir, one_gpu=True))
    text = out.getvalue()
    assert "WARNING: rulecam-glimmer" in text
    assert "still runs at $0.50/h and is no longer used" in text
    assert "runpod_down.sh --only sam" in text
    api = next(p for p in account.pods if p["name"] == "rulecam-api")
    gpu = next(p for p in account.pods if p["name"] == "rulecam-gpu")
    assert api["env"]["GLIMMER_BASE_URL"] == f"https://{gpu['id']}-8080.proxy.runpod.net/v1"
    rp.up(runpod, opts(secrets_dir))  # and back: the gpu pod is the one left over
    assert "WARNING: rulecam-gpu" in out.getvalue()


def test_one_gpu_needs_both_services(secrets_dir: Path) -> None:
    runpod = rp.RunPod(rp.offline_transport, rp.Reporter(out=io.StringIO()), dry_run=True)
    with pytest.raises(rp.DeployError, match="drop --no-sam"):
        rp.up(runpod, opts(secrets_dir, dry_run=True, one_gpu=True, with_sam=False))


def test_gpu_price_override_reread_and_unknown(secrets_dir: Path) -> None:
    reads: list[str] = []

    def transport(method: str, path: str, body: Any) -> Any:
        reads.append(path)
        return {"id": "p1", "costPerHr": 0.44} if path == "/v2/pods/p1" else {"id": "p2"}

    out = io.StringIO()
    runpod = rp.RunPod(transport, rp.Reporter(out=out), dry_run=False)
    stack = rp.Stack(pods={"gpu": {"id": "p1"}})
    base = opts(secrets_dir)
    assert rp.gpu_price(runpod, stack, base) == "0.44"  # re-read after a create without it
    assert reads == ["/v2/pods/p1"]
    assert rp.gpu_price(runpod, stack, replace(base, gpu_usd_per_hour=0.39)) == "0.39"
    assert rp.gpu_price(runpod, rp.Stack(), base) is None  # no GPU pods
    two = rp.Stack(pods={"glimmer": {"id": "a", "cost": 0.59}, "sam": {"id": "b", "cost": 0.39}})
    assert rp.gpu_price(runpod, two, base) == "0.98"
    unknown = rp.Stack(pods={"gpu": {"id": "p2", "cost": 0}})
    assert rp.gpu_price(runpod, unknown, base) is None
    assert "rulecam-gpu reports no price" in out.getvalue()
    assert rp.pod_price({"adjustedCostPerHr": 0.5}) == 0.5
    assert rp.pod_price({"cost": "0.5"}) is None


def test_one_gpu_status_needs_both_servers(
    fake: FakeRunPod, account: FakeAccount, secrets_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runpod, out = client(fake)
    stack = rp.up(runpod, opts(secrets_dir, one_gpu=True))
    gpu_id = stack.pods["gpu"]["id"]
    rows = {r[0]: r for r in rp.status_rows(runpod, rp.Options(), probe=lambda url: 200)}
    assert rows["rulecam-gpu"][4] == "ok"
    assert rows["rulecam-glimmer"][2] == "absent"

    def sam_down(url: str) -> int:
        return 503 if "-8090." in url else 200

    rows = {r[0]: r for r in rp.status_rows(runpod, rp.Options(), probe=sam_down)}
    assert rows["rulecam-gpu"][4] == "down (200,503)"
    monkeypatch.setattr(rp, "http_status", lambda url, **_: 200)
    assert rp.print_status(runpod, rp.Options()) == 0  # glimmer and sam absent: not a failure
    assert f"https://{gpu_id}-8080.proxy.runpod.net https://{gpu_id}-8090" in out.getvalue()
    monkeypatch.setattr(rp, "http_status", lambda url, **_: 503 if "-8090." in url else 200)
    assert rp.print_status(runpod, rp.Options()) == 1


def test_one_gpu_waits_for_both_servers_on_the_one_pod(
    fake: FakeRunPod, account: FakeAccount, secrets_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runpod, out = client(fake)
    probed: list[str] = []

    def status(url: str, **_: Any) -> int:
        probed.append(url)
        return 200

    monkeypatch.setattr(rp, "http_status", status)
    code = rp.run_up(runpod, opts(secrets_dir, one_gpu=True, wait=True))
    assert code == 0
    gpu = next(p for p in account.pods if p["name"] == "rulecam-gpu")
    assert f"https://{gpu['id']}-8080.proxy.runpod.net/health" in probed
    assert f"https://{gpu['id']}-8090.proxy.runpod.net/health" in probed
    text = out.getvalue()
    assert "glimmer: healthy" in text
    assert "sam: healthy" in text
    assert "GPU $0.50/h per camera-hour: 1 camera $0.500, 4 cameras $0.125" in text


def test_one_gpu_command_line(
    fake: FakeRunPod,
    account: FakeAccount,
    secrets_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = rp.parser().parse_args(
        ["up", "--one-gpu", "--one-gpu-gpus", "NVIDIA A40,NVIDIA L40S", "--gpu-usd-per-hour", "0.4"]
    )
    options = rp.options(args)
    assert options.one_gpu
    assert options.one_gpu_gpus == ("NVIDIA A40", "NVIDIA L40S")
    assert options.gpu_usd_per_hour == 0.4
    assert rp.Options().one_gpu_gpus == (
        "NVIDIA RTX A6000",
        "NVIDIA A40",
        "NVIDIA L40S",
        "NVIDIA RTX 6000 Ada Generation",
    )
    config = tmp_path / "config.toml"
    config.write_text(f'apikey = "{API_KEY}"\n')
    monkeypatch.setenv("RUNPOD_REST_URL", fake.url)
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    argv = ["up", "--one-gpu", "--dry-run", "--config", str(config)]
    assert rp.main([*argv, "--secrets-dir", str(secrets_dir)]) == 0
    assert account.writes() == []
    assert rp.main(["up", "--one-gpu", "--no-sam", "--dry-run", "--config", str(config)]) == 1
