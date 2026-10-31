"""Tests for the replay cache (P3-02)."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rulecam.bus import Bus, Topic
from rulecam.clients.transport import RECORDED_LATENCY_HEADER
from rulecam.config import Settings
from rulecam.replay import cache as cache_module
from rulecam.replay.cache import (
    CacheEntry,
    ModeChanged,
    ReplayCache,
    atomic_write_text,
    badge_for,
    cache_key,
    cached_call,
    canonical_json,
    configure,
    get_cache,
    using,
)
from rulecam.replay.errors import ReplayCorruptError, ReplayMiss
from tests.fakes import FakeClock

REQUEST: dict[str, object] = {"method": "POST", "path": "/responses", "json": {"b": 1, "a": [1, 2]}}
REPLY: dict[str, object] = {"status": 200, "headers": {}, "json": {"text": "ok"}}


class CountingCall:
    """A fake model call that counts invocations."""

    def __init__(self, reply: Mapping[str, object] = REPLY) -> None:
        self.calls = 0
        self.reply = reply

    def __call__(self) -> Mapping[str, object]:
        self.calls += 1
        return self.reply


def make_cache(tmp_path: Path, mode: str, bus: Bus | None = None) -> ReplayCache:
    return ReplayCache(tmp_path, mode, clock=FakeClock(), bus=bus)  # type: ignore[arg-type]


def test_live_calls_and_stores_nothing(tmp_path: Path) -> None:
    call = CountingCall()
    entry = make_cache(tmp_path, "live").call("meta", "sam-3.1", REQUEST, call)
    assert call.calls == 1
    assert entry.response == REPLY
    assert not list(tmp_path.rglob("*.json"))


def test_record_calls_and_stores_with_metadata(tmp_path: Path) -> None:
    call = CountingCall()
    cache = make_cache(tmp_path, "record")
    entry = cache.call("meta", "sam-3.1", REQUEST, call)
    stored = json.loads(cache.entry_path("meta", entry.key).read_text())
    assert call.calls == 1
    assert stored["response"] == REPLY
    assert stored["latency_ms"] >= 0
    assert stored["recorded_at"].startswith("2026-10-30T12:00:00")


def test_replay_serves_recorded_reply_without_calling(tmp_path: Path) -> None:
    make_cache(tmp_path, "record").call("meta", "sam-3.1", REQUEST, CountingCall())
    call = CountingCall({"status": 500})
    entry = make_cache(tmp_path, "replay").call("meta", "sam-3.1", REQUEST, call)
    assert call.calls == 0
    assert entry.response == REPLY


def test_replay_miss_raises_and_never_calls(tmp_path: Path) -> None:
    call = CountingCall()
    with pytest.raises(ReplayMiss) as info:
        make_cache(tmp_path, "replay").call("meta", "sam-3.1", REQUEST, call)
    assert call.calls == 0
    assert info.value.provider == "meta"
    assert info.value.key == cache_key("meta", "sam-3.1", REQUEST)


def test_corrupt_entry_raises(tmp_path: Path) -> None:
    cache = make_cache(tmp_path, "replay")
    path = cache.entry_path("meta", cache_key("meta", "m", REQUEST))
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    with pytest.raises(ReplayCorruptError):
        cache.lookup("meta", "m", REQUEST)


def test_key_depends_on_provider_and_model() -> None:
    keys = {
        cache_key("meta", "a", REQUEST),
        cache_key("meta", "b", REQUEST),
        cache_key("baseten", "a", REQUEST),
    }
    assert len(keys) == 3


def test_bytes_are_keyed_by_their_hash() -> None:
    small = {"file": {"content": b"abc"}}
    text = canonical_json(small)
    assert "abc" not in text
    assert "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad" in text
    assert cache_key("p", "m", small) != cache_key("p", "m", {"file": {"content": b"abd"}})


@given(st.dictionaries(st.text(max_size=8), st.integers() | st.text(max_size=8), max_size=8))
def test_canonical_key_ignores_dict_order(mapping: dict[str, object]) -> None:
    reversed_mapping = dict(reversed(list(mapping.items())))
    nested_a = {"outer": mapping, "x": 1}
    nested_b = {"x": 1, "outer": reversed_mapping}
    assert cache_key("p", "m", nested_a) == cache_key("p", "m", nested_b)


def test_concurrent_writers_leave_a_valid_entry(tmp_path: Path) -> None:
    cache = make_cache(tmp_path, "record")
    barrier = threading.Barrier(8)
    errors: list[BaseException] = []

    def writer(index: int) -> None:
        reply = {"status": 200, "json": {"writer": index, "pad": "x" * 50_000}}
        barrier.wait()
        try:
            for _ in range(10):
                cache.store("meta", "m", REQUEST, reply, latency_ms=index)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    entry = cache.lookup("meta", "m", REQUEST)
    assert entry.response["json"] == {"writer": entry.latency_ms, "pad": "x" * 50_000}
    assert [p.name for p in (tmp_path / "meta").iterdir()] == [f"{entry.key}.json"]


async def test_set_mode_publishes_mode_changed(tmp_path: Path) -> None:
    bus = Bus()
    cache = make_cache(tmp_path, "replay", bus=bus)
    stream = bus.subscribe(Topic.MODE_CHANGED)
    pending = asyncio.ensure_future(stream.__anext__())
    await asyncio.sleep(0)  # let the generator register its queue
    cache.set_mode("record")
    message = await asyncio.wait_for(pending, timeout=2)
    await stream.aclose()
    assert message.payload == ModeChanged(mode="record", badge="LIVE")
    assert cache.mode == "record"


def test_badges() -> None:
    assert badge_for("replay") == "REPLAY"
    assert badge_for("live") == "LIVE"
    assert badge_for("record") == "LIVE"


def test_wrap_transport_goes_through_cache(tmp_path: Path) -> None:
    seen: list[Mapping[str, object]] = []

    def transport(request: Mapping[str, object]) -> Mapping[str, object]:
        seen.append(request)
        return REPLY

    cache = make_cache(tmp_path, "record")
    wrapped = cache.wrap_transport("meta", "sam-3.1", transport)
    assert wrapped(REQUEST) == REPLY
    cache.set_mode("replay")
    replayed = wrapped(REQUEST)
    assert replayed["json"] == REPLY["json"]
    assert replayed["headers"] == {RECORDED_LATENCY_HEADER: "0"}
    assert len(seen) == 1


def test_from_settings_uses_mode_and_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RULECAM_MODE", "record")
    monkeypatch.setenv("RULECAM_REPLAY_DIR", str(tmp_path))
    cache = ReplayCache.from_settings(Settings(_env_file=None), clock=FakeClock())
    assert cache.mode == "record"
    assert cache.directory == tmp_path


def test_cached_call_uses_configured_cache(tmp_path: Path) -> None:
    configure(make_cache(tmp_path, "record"))
    try:
        assert cached_call("meta", "m", REQUEST, CountingCall()) == REPLY
        assert get_cache().lookup("meta", "m", REQUEST).response == REPLY
    finally:
        configure(None)


def test_get_cache_builds_from_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cache_module, "_default_cache", None)
    monkeypatch.setattr(
        "rulecam.config.get_settings",
        lambda: Settings(_env_file=None, rulecam_mode="replay", rulecam_replay_dir=tmp_path),
    )
    try:
        cache = get_cache()
        assert cache.mode == "replay"
        assert get_cache() is cache
    finally:
        configure(None)


def test_entry_round_trips(tmp_path: Path) -> None:
    entry = make_cache(tmp_path, "record").store("meta", "m", REQUEST, REPLY, latency_ms=12)
    assert CacheEntry.model_validate_json(entry.model_dump_json()) == entry


def scripted(
    *replies: Mapping[str, object],
) -> Callable[[Mapping[str, object]], Mapping[str, object]]:
    queue = list(replies)

    def transport(request: Mapping[str, object]) -> Mapping[str, object]:
        return queue.pop(0)

    return transport


RATE_LIMITED: dict[str, object] = {
    "status": 429,
    "headers": {"x-ratelimit-remaining-requests": "0"},
    "json": {"error": "slow down"},
}


def test_wrap_transport_never_records_failures(tmp_path: Path) -> None:
    cache = make_cache(tmp_path, "record")
    wrapped = cache.wrap_transport("meta", "sam-3.1", scripted(RATE_LIMITED, {"status": 503}))
    assert wrapped(REQUEST)["status"] == 429
    assert wrapped(REQUEST)["status"] == 503
    assert not list(tmp_path.rglob("*.json"))
    cache.set_mode("replay")
    with pytest.raises(ReplayMiss):
        wrapped(REQUEST)


def test_wrap_transport_keeps_the_success_after_a_retry(tmp_path: Path) -> None:
    cache = make_cache(tmp_path, "record")
    wrapped = cache.wrap_transport("meta", "sam-3.1", scripted(RATE_LIMITED, REPLY))
    wrapped(REQUEST)
    wrapped(REQUEST)
    (entry,) = tmp_path.rglob("*.json")
    assert json.loads(entry.read_text())["response"]["status"] == 200
    cache.set_mode("replay")
    replayed = wrapped(REQUEST)
    assert replayed["status"] == 200
    assert "x-ratelimit-remaining-requests" not in replayed["headers"]  # type: ignore[operator]


def test_wrap_transport_live_stores_nothing(tmp_path: Path) -> None:
    wrapped = make_cache(tmp_path, "live").wrap_transport("meta", "sam-3.1", scripted(REPLY))
    assert wrapped(REQUEST) == REPLY
    assert not list(tmp_path.rglob("*.json"))


@pytest.mark.parametrize(
    ("provider", "model"),
    [
        ("", "m"),
        (".", "m"),
        ("..", "m"),
        ("a|b", "m"),
        ("a/b", "m"),
        ("a\\b", "m"),
        ("p", ""),
        ("p", "a|b"),
    ],
)
def test_ambiguous_or_unsafe_names_are_rejected(tmp_path: Path, provider: str, model: str) -> None:
    with pytest.raises(ValueError, match="invalid"):
        cache_key(provider, model, {})
    with pytest.raises(ValueError, match="invalid"):
        make_cache(tmp_path, "record").wrap_transport(provider, model, scripted())


def test_model_ids_may_contain_slashes() -> None:
    assert len(cache_key("baseten", "meta-models/muse-spark-open-1026", {})) == 64


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nan_and_infinity_are_rejected(value: float) -> None:
    with pytest.raises(ValueError, match="JSON compliant"):
        canonical_json({"x": value})


def test_using_restores_the_previous_cache(tmp_path: Path) -> None:
    outer = make_cache(tmp_path / "outer", "replay")
    inner = make_cache(tmp_path / "inner", "live")
    configure(outer)
    try:
        with using(inner):
            assert get_cache() is inner
        assert get_cache() is outer
    finally:
        configure(None)


def test_atomic_write_text_replaces_whole_file(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "f.json"
    atomic_write_text(path, "one")
    atomic_write_text(path, "two")
    assert path.read_text() == "two"
    assert [p.name for p in path.parent.iterdir()] == ["f.json"]
