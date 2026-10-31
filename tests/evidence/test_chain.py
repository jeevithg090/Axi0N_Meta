"""Tests for the hash-chained event log (P3-07a)."""

from __future__ import annotations

import json
import multiprocessing
import tempfile
import threading
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import get_args

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rulecam.contracts import Ack, EventRecord
from rulecam.evidence import (
    GENESIS_HASH,
    ChainError,
    EventChain,
    canonical_json,
    js_number,
    read_chain,
    record_hash,
    verify_chain,
)
from rulecam.evidence.chain import EventKind
from tests.fakes import FakeClock, make_verdict


def build_chain(root: Path, n: int = 5) -> Path:
    clock = FakeClock()
    chain = EventChain(root, "kitchen", clock=clock)
    for i in range(n):
        chain.append("candidate", {"i": i, "score": 0.5 + i / 10, "label": "ñ", "ok": True})
        clock.advance(1)
    return chain.path_for(clock.now().date())


def test_append_links_records(tmp_path: Path) -> None:
    clock = FakeClock()
    chain = EventChain(tmp_path, "kitchen", clock=clock)
    first = chain.append("plan", {"plan_id": "p1"})
    second = chain.append("verdict", make_verdict())
    assert (first.seq, first.prev_hash) == (1, GENESIS_HASH)
    assert (second.seq, second.prev_hash) == (2, first.hash)
    assert second.payload["tier"] == "glimmer"
    body = first.model_dump(mode="json")
    del body["hash"]
    assert first.hash == record_hash(GENESIS_HASH, body)
    path = chain.path_for(clock.now().date())
    assert path == tmp_path / "kitchen" / "2026-10-30.jsonl"
    assert read_chain(path) == [first, second]
    assert verify_chain(path).model_dump() == {
        "ok": True,
        "first_bad_seq": None,
        "n": 2,
        "reason": "",
    }


def test_lines_are_canonical(tmp_path: Path) -> None:
    line = build_chain(tmp_path, 1).read_text(encoding="utf-8").splitlines()[0]
    assert " " not in line
    assert '"ts":"2026-10-30T12:00:00Z"' in line
    assert '"label":"ñ"' in line
    assert canonical_json(json.loads(line)) == line


def test_new_day_starts_a_new_file(tmp_path: Path) -> None:
    clock = FakeClock(datetime(2026, 10, 30, 23, 59, 59, tzinfo=UTC))
    chain = EventChain(tmp_path, "s", clock=clock)
    chain.append("ack", {})
    clock.advance(2)
    record = chain.append("ack", {})
    assert record.seq == 1
    assert chain.path_for(clock.now().date()).name == "2026-10-31.jsonl"


@settings(max_examples=150)
@given(data=st.data())
def test_any_single_byte_tamper_is_caught_at_its_record(data: st.DataObject) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        check_tamper(Path(tmp), data)


def check_tamper(root: Path, data: st.DataObject) -> None:
    path = build_chain(root, 5)
    original = path.read_bytes()
    offset = data.draw(st.integers(0, len(original) - 1))
    new_byte = data.draw(st.integers(0, 255).filter(lambda b: b != original[offset]))
    tampered = bytearray(original)
    tampered[offset] = new_byte
    path.write_bytes(bytes(tampered))
    expected_seq = original[:offset].count(b"\n") + 1
    report = verify_chain(path)
    assert not report.ok
    assert report.first_bad_seq == expected_seq


def test_deleted_and_reordered_records_are_caught(tmp_path: Path) -> None:
    path = build_chain(tmp_path, 4)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    path.write_text("".join(lines[:1] + lines[2:]), encoding="utf-8")
    assert verify_chain(path).first_bad_seq == 2
    path.write_text("".join([lines[1], lines[0], *lines[2:]]), encoding="utf-8")
    assert verify_chain(path).first_bad_seq == 1


def test_rehashed_edit_still_breaks_the_next_link(tmp_path: Path) -> None:
    path = build_chain(tmp_path, 3)
    lines = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[1])
    record["payload"]["i"] = 99
    body = {k: v for k, v in record.items() if k != "hash"}
    record["hash"] = record_hash(record["prev_hash"], body)
    lines[1] = canonical_json(record)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = verify_chain(path)
    assert report.first_bad_seq == 3
    assert "prev_hash" in report.reason


def test_missing_final_newline_and_empty_file(tmp_path: Path) -> None:
    path = build_chain(tmp_path, 2)
    path.write_bytes(path.read_bytes()[:-1])
    assert verify_chain(path).first_bad_seq == 2
    empty = tmp_path / "empty.jsonl"
    empty.write_bytes(b"")
    assert verify_chain(empty).ok


def test_two_concurrent_writers_keep_the_chain_valid(tmp_path: Path) -> None:
    clock = FakeClock()
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def writer(name: str) -> None:
        chain = EventChain(tmp_path, "site", clock=clock)
        barrier.wait()
        try:
            for i in range(50):
                chain.append("action", {"writer": name, "i": i})
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    report = verify_chain(EventChain(tmp_path, "site", clock=clock).path_for(clock.now().date()))
    assert report.ok
    assert report.n == 100


def test_corrupt_last_line_refuses_to_extend(tmp_path: Path) -> None:
    clock = FakeClock()
    chain = EventChain(tmp_path, "s", clock=clock)
    path = chain.path_for(clock.now().date())
    path.parent.mkdir(parents=True)
    path.write_text("garbage\n")
    with pytest.raises(ChainError):
        chain.append("ack", {})


def test_single_line_file_extends(tmp_path: Path) -> None:
    clock = FakeClock()
    chain = EventChain(tmp_path, "s", clock=clock)
    chain.append("ack", {})
    assert chain.append("ack", {}).seq == 2


@pytest.mark.parametrize("site", ["", "a/b", "..", "."])
def test_bad_site_names(tmp_path: Path, site: str) -> None:
    with pytest.raises(ValueError, match="invalid site"):
        EventChain(tmp_path, site, clock=FakeClock())


def test_bad_kind_and_nan_rejected(tmp_path: Path) -> None:
    chain = EventChain(tmp_path, "s", clock=FakeClock())
    with pytest.raises(ValueError, match="unknown event kind"):
        chain.append("nope", {})  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="NaN"):
        chain.append("ack", {"x": float("nan")})


def test_canonical_rejects_non_json() -> None:
    with pytest.raises(TypeError):
        canonical_json({"x": object()})
    assert canonical_json({"b": [None, False, 1], "a": "é"}) == '{"a":"é","b":[null,false,1]}'


# Expected values are what Node's `String(x)` / `JSON.stringify(x)` print.
@pytest.mark.parametrize(
    ("value", "text"),
    [
        (0.0, "0"),
        (-0.0, "0"),
        (1.0, "1"),
        (-2.5, "-2.5"),
        (100.0, "100"),
        (0.1, "0.1"),
        (0.001, "0.001"),
        (1e-6, "0.000001"),
        (1e-7, "1e-7"),
        (1.5e-7, "1.5e-7"),
        (123456789.125, "123456789.125"),
        (1e20, "100000000000000000000"),
        (1e21, "1e+21"),
        (1.25e22, "1.25e+22"),
        (0.30000000000000004, "0.30000000000000004"),
        (5e-324, "5e-324"),
        (1.7976931348623157e308, "1.7976931348623157e+308"),
    ],
)
def test_js_number_matches_javascript(value: float, text: str) -> None:
    assert js_number(value) == text


@given(st.floats(allow_nan=False, allow_infinity=False))
def test_js_number_round_trips(value: float) -> None:
    assert float(js_number(value)) == value


def _process_writer(root: str, name: str) -> None:
    chain = EventChain(Path(root), "site", clock=FakeClock())
    for i in range(25):
        chain.append("action", {"writer": name, "i": i})


def test_two_writer_processes_keep_the_chain_valid(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    procs = [context.Process(target=_process_writer, args=(str(tmp_path), n)) for n in "ab"]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(timeout=60)
    assert [proc.exitcode for proc in procs] == [0, 0]
    report = verify_chain(EventChain(tmp_path, "site", clock=FakeClock()).path_for(EPOCH_DAY))
    assert report.ok
    assert report.n == 50


EPOCH_DAY = FakeClock().now().date()
PLUS_FIVE = timezone(timedelta(hours=5))


def test_payload_datetimes_are_utc_iso(tmp_path: Path) -> None:
    chain = EventChain(tmp_path, "s", clock=FakeClock())
    ack = chain.append("ack", Ack(role="manager", ts=datetime(2026, 1, 1, 5, tzinfo=PLUS_FIVE)))
    assert ack.payload["ts"] == "2026-01-01T00:00:00Z"
    when = datetime(2026, 1, 1, 12, 0, 0, 250000, tzinfo=UTC)
    label = chain.append("label", {"when": when, "nested": [{"at": when}]})
    assert label.payload == {
        "when": "2026-01-01T12:00:00.250000Z",
        "nested": [{"at": "2026-01-01T12:00:00.250000Z"}],
    }
    assert verify_chain(chain.path_for(EPOCH_DAY)).ok


def test_naive_datetimes_and_sets_are_rejected(tmp_path: Path) -> None:
    chain = EventChain(tmp_path, "s", clock=FakeClock())
    with pytest.raises(ValueError, match="timezone-aware"):
        chain.append("label", {"when": datetime(2026, 1, 1)})
    with pytest.raises(TypeError, match="sets"):
        chain.append("label", {"tags": {"a", "b"}})
    naive = EventChain(tmp_path, "n", clock=FakeClock(datetime(2026, 1, 1)))
    with pytest.raises(ValueError, match="timezone-aware"):
        naive.append("ack", {})


def test_clock_in_another_zone_is_written_as_utc(tmp_path: Path) -> None:
    clock = FakeClock(datetime(2026, 10, 31, 1, 30, tzinfo=PLUS_FIVE))
    record = EventChain(tmp_path, "s", clock=clock).append("ack", {})
    assert record.ts == datetime(2026, 10, 30, 20, 30, tzinfo=UTC)
    line = (tmp_path / "s" / "2026-10-30.jsonl").read_text(encoding="utf-8")
    assert '"ts":"2026-10-30T20:30:00Z"' in line


def test_keys_sort_by_utf16_code_units_like_javascript() -> None:
    # JS: Object.keys({"\uff61": 2, "\u{1F600}": 1}).sort() puts the emoji (\ud83d...) first.
    assert (
        canonical_json({"\uff61": 2, "\U0001f600": 1, "a": 0})
        == '{"a":0,"\U0001f600":1,"\uff61":2}'
    )


def test_unsafe_integers_and_lone_surrogates_are_rejected(tmp_path: Path) -> None:
    assert canonical_json(2**53 - 1) == "9007199254740991"
    assert canonical_json(-(2**53 - 1)) == "-9007199254740991"
    with pytest.raises(ValueError, match="safe range"):
        canonical_json(2**53)
    chain = EventChain(tmp_path, "s", clock=FakeClock())
    with pytest.raises(ValueError, match="lone surrogates"):
        chain.append("label", {"x": "\ud800"})
    assert not chain.path_for(EPOCH_DAY).exists()


def test_event_kinds_match_the_contract() -> None:
    assert get_args(EventKind) == get_args(EventRecord.model_fields["kind"].annotation)
