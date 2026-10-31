"""Tests for preflight (P3-02): every check's pass and fail paths, with fakes only."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import httpx
import pytest

from rulecam.config import Settings
from rulecam.replay import preflight
from rulecam.replay.preflight import (
    MIN_FREE_BYTES,
    CheckResult,
    PreflightDeps,
    exit_code,
    render_table,
    run_preflight,
    tone_wav,
)

NOW = 1_800_000_000.0
Handler = Callable[[httpx.Request], httpx.Response]


def good_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/models"):
        return httpx.Response(200, json={"data": [{"id": "muse-glimmer-30b"}]})
    if request.url.path.endswith("/chat/completions"):
        message = {"content": "OK", "reasoning_content": "The user wants OK."}
        return httpx.Response(200, json={"choices": [{"message": message}]})
    return httpx.Response(404)


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "meta_api_key": "k",
        "meta_api_base_url": "https://meta.test/v1",
        "baseten_api_key": "b",
        "hf_token": "h",
        "kasa_plug_host": "10.0.0.5",
        "kasa_light_host": "10.0.0.6",
        "rulecam_data_dir": tmp_path,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def make_deps(
    tmp_path: Path,
    handler: Handler = good_handler,
    *,
    run: Callable[[Sequence[str]], int] = lambda argv: 0,
    settings: Settings | None = None,
    **kwargs: object,
) -> PreflightDeps:
    return PreflightDeps(
        settings=settings or make_settings(tmp_path),
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        run=run,
        probe_kasa=kwargs.get("probe_kasa", lambda host: True),  # type: ignore[arg-type]
        free_bytes=kwargs.get("free_bytes", lambda path: MIN_FREE_BYTES),  # type: ignore[arg-type]
        newest_chunk_mtime=kwargs.get("mtime", lambda path: NOW - 2),  # type: ignore[arg-type]
        now=lambda: NOW,
        player="aplay",
    )


def by_name(results: list[CheckResult]) -> dict[str, CheckResult]:
    return {r.name: r for r in results}


def test_all_green(tmp_path: Path) -> None:
    results = run_preflight(make_deps(tmp_path))
    assert all(r.ok for r in results), render_table(results)
    assert exit_code(results) == 0
    assert "✓ Glimmer" in render_table(results)


def test_missing_env_keys_fail_and_name_only(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, meta_api_key=None, hf_token=None)
    result = by_name(run_preflight(make_deps(tmp_path, settings=settings)))["env keys"]
    assert not result.ok
    assert result.detail == "missing: META_API_KEY, HF_TOKEN"


@pytest.mark.parametrize(
    ("status", "ok", "text"),
    [
        (200, True, "HTTP 200"),
        (404, True, "credits"),
        (401, False, "rejected"),
        (403, False, "rejected"),
        (503, False, "HTTP 503"),
    ],
)
def test_meta_api_status(tmp_path: Path, status: int, ok: bool, text: str) -> None:
    deps = make_deps(tmp_path, lambda r: httpx.Response(status))
    result = preflight.check_meta_api(deps)
    assert result.ok is ok
    assert text in result.detail


def test_meta_api_unreachable_and_unset(tmp_path: Path) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    assert "unreachable" in preflight.check_meta_api(make_deps(tmp_path, boom)).detail
    unset = make_deps(tmp_path, settings=make_settings(tmp_path, meta_api_base_url=None))
    assert not preflight.check_meta_api(unset).ok


def test_spark_open_needs_reasoning(tmp_path: Path) -> None:
    def no_reasoning(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    assert preflight.check_spark_open(make_deps(tmp_path)).ok
    result = preflight.check_spark_open(make_deps(tmp_path, no_reasoning))
    assert not result.ok
    assert "reasoning_content" in result.detail


def test_spark_open_bad_reply_and_no_key(tmp_path: Path) -> None:
    bad = make_deps(tmp_path, lambda r: httpx.Response(500, text="oops"))
    assert "bad reply" in preflight.check_spark_open(bad).detail
    unset = make_deps(tmp_path, settings=make_settings(tmp_path, baseten_api_key=None))
    assert not preflight.check_spark_open(unset).ok


def test_spark_open_sends_baseten_auth(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return good_handler(request)

    preflight.check_spark_open(make_deps(tmp_path, handler))
    assert seen[0].headers["authorization"] == "Api-Key b"
    assert str(seen[0].url) == "https://inference.baseten.co/v1/chat/completions"


def test_glimmer_paths(tmp_path: Path) -> None:
    assert preflight.check_glimmer(make_deps(tmp_path)).detail == "muse-glimmer-30b"
    empty = make_deps(tmp_path, lambda r: httpx.Response(200, json={"data": []}))
    assert preflight.check_glimmer(empty).detail == "no model loaded"
    down = make_deps(tmp_path, lambda r: httpx.Response(200, text="not json"))
    assert not preflight.check_glimmer(down).ok


@pytest.mark.parametrize(("code", "ok"), [(0, True), (1, False), (127, False)])
def test_modal(tmp_path: Path, code: int, ok: bool) -> None:
    assert preflight.check_modal(make_deps(tmp_path, run=lambda argv: code)).ok is ok


def test_cameras_fresh_stale_missing(tmp_path: Path) -> None:
    assert preflight.check_cameras(make_deps(tmp_path)).ok
    stale = preflight.check_cameras(make_deps(tmp_path, mtime=lambda p: NOW - 120))
    assert not stale.ok
    missing = preflight.check_cameras(make_deps(tmp_path, mtime=lambda p: None))
    assert "no chunks" in missing.detail


def test_kasa_ok_fail_error_and_skipped(tmp_path: Path) -> None:
    assert preflight.check_plug(make_deps(tmp_path)).ok
    assert not preflight.check_plug(make_deps(tmp_path, probe_kasa=lambda h: False)).ok

    def raises(host: str) -> bool:
        raise TimeoutError

    assert "TimeoutError" in preflight.check_light(make_deps(tmp_path, probe_kasa=raises)).detail
    unset = make_deps(tmp_path, settings=make_settings(tmp_path, kasa_plug_host=None))
    skipped = preflight.check_plug(unset)
    assert skipped.ok
    assert skipped.skipped
    assert "- Kasa plug" in render_table([skipped])


def test_speaker_plays_tone_and_failure_is_optional(tmp_path: Path) -> None:
    argvs: list[Sequence[str]] = []

    def run(argv: Sequence[str]) -> int:
        argvs.append(argv)
        return 0 if argv[0] == "aplay" and Path(argv[1]).read_bytes()[:4] == b"RIFF" else 9

    assert preflight.check_speaker(make_deps(tmp_path, run=run)).ok
    failed = preflight.check_speaker(make_deps(tmp_path, run=lambda argv: 1))
    assert not failed.ok
    assert not failed.required
    assert exit_code([failed]) == 0
    assert "! speaker" in render_table([failed])


def test_disk_threshold(tmp_path: Path) -> None:
    low = make_deps(tmp_path, free_bytes=lambda p: MIN_FREE_BYTES - 1)
    assert not preflight.check_disk(low).ok
    assert preflight.check_disk(make_deps(tmp_path)).ok


def test_any_required_failure_exits_non_zero(tmp_path: Path) -> None:
    results = run_preflight(make_deps(tmp_path, lambda r: httpx.Response(503)))
    assert exit_code(results) == 1
    assert "✗ Meta API" in render_table(results)


def test_tone_is_a_wav() -> None:
    assert tone_wav()[:4] == b"RIFF"


def test_main_prints_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    deps = make_deps(tmp_path)
    monkeypatch.setattr(preflight, "PreflightDeps", lambda settings: deps)
    assert preflight.main() == 0
    assert "✓ disk" in capsys.readouterr().out


def test_default_helpers(tmp_path: Path) -> None:
    assert preflight._newest_mtime(tmp_path / "none") is None
    (tmp_path / "c.mp4").write_bytes(b"x")
    assert preflight._newest_mtime(tmp_path) is not None
    assert preflight._free_bytes(tmp_path / "missing") > 0
    assert preflight._run_subprocess(["definitely-not-a-command-xyz"]) == 127
