"""Append-only, hash-chained event log (task P3-07a, MP §7.4).

One JSONL file per site and UTC day: `<root>/<site>/<YYYY-MM-DD>.jsonl`. Each line is one
`EventRecord`, serialised canonically:

- keys sorted, no whitespace, UTF-8 (not ASCII-escaped);
- timestamps as UTC ISO strings;
- numbers formatted exactly as JavaScript's `JSON.stringify` does, so the browser verify page
  (P3-07b) can recompute every hash with Web Crypto.

`hash = sha256(prev_hash + canonical(record without "hash"))`; the first record of a file has
`prev_hash = "0" * 64` and `seq = 1`. Verification also requires every line to be byte-identical
to the canonical form of what it parses to, so even whitespace or line-ending edits are caught.
Writers take an exclusive `flock` on a sidecar lock file, so concurrent appenders (the runtime
loop, the API, the CLI) never fork the chain.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, date
from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel, ValidationError

from rulecam.contracts import Clock, EventRecord, Frozen
from rulecam.evidence.errors import ChainError
from rulecam.log import get_logger

_log = get_logger(__name__)

GENESIS_HASH = "0" * 64
FIRST_SEQ = 1
EventKind = Literal[
    "candidate",
    "verdict",
    "action",
    "ack",
    "dispute",
    "label",
    "plan",
    "rule_change",
    "coverage",
    "advice",
    "measurement_plan",
    "sealed_cause",
    "surprise",
    "tamper_attempt",
]
EVENT_KINDS: frozenset[str] = frozenset(get_args(EventKind))
_JS_FIXED_MAX_EXP = 21
_JS_FIXED_MIN_EXP = -6


class ChainReport(Frozen):
    """The result of verifying one chain file.

    Attributes:
        ok: Whether every record is intact and linked.
        first_bad_seq: The seq of the first broken record (its position, 1-based), or `None`.
        n: How many records were read (including a broken one).
        reason: Why the first bad record failed, empty when `ok`.
    """

    ok: bool
    first_bad_seq: int | None = None
    n: int
    reason: str = ""


def js_number(value: float) -> str:
    """Format a finite float exactly like ECMAScript `Number.prototype.toString`.

    Python's `repr` already gives the shortest round-trip digits; only the layout differs
    (JavaScript writes `1e-7`, `0.00001` and `1e+21` where Python writes `1e-07`, `1e-05`,
    `1e+21`).

    Args:
        value: A finite float.

    Returns:
        The JavaScript text of the number.

    Raises:
        ValueError: For NaN or infinity, which JSON cannot carry.
    """
    if not math.isfinite(value):
        msg = "NaN and infinity are not allowed in chain records"
        raise ValueError(msg)
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    mantissa, _, exp_text = repr(abs(value)).partition("e")
    whole, _, frac = mantissa.partition(".")
    raw = whole + frac
    stripped = raw.lstrip("0")
    # value == 0.<digits> x 10**point
    point = len(whole) + int(exp_text or 0) - (len(raw) - len(stripped))
    return sign + _js_layout(stripped.rstrip("0"), point)


def _js_layout(digits: str, point: int) -> str:
    k = len(digits)
    if k <= point <= _JS_FIXED_MAX_EXP:
        return digits + "0" * (point - k)
    if 0 < point <= _JS_FIXED_MAX_EXP:
        return f"{digits[:point]}.{digits[point:]}"
    if _JS_FIXED_MIN_EXP < point <= 0:
        return "0." + "0" * -point + digits
    exponent = point - 1
    rest = f".{digits[1:]}" if k > 1 else ""
    return f"{digits[0]}{rest}e{'+' if exponent > 0 else '-'}{abs(exponent)}"


def _canonical(value: object) -> str:
    if value is None or isinstance(value, bool):
        return json.dumps(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return js_number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, Mapping):
        items = sorted((str(k), v) for k, v in value.items())
        return "{" + ",".join(f"{_canonical(k)}:{_canonical(v)}" for k, v in items) + "}"
    if isinstance(value, list | tuple):
        return "[" + ",".join(_canonical(v) for v in value) + "]"
    msg = f"cannot put {type(value).__name__} in a chain record"
    raise TypeError(msg)


def canonical_json(value: object) -> str:
    """Serialise a JSON value canonically (sorted keys, no whitespace, JavaScript numbers).

    Args:
        value: Plain JSON data (dicts, lists, strings, numbers, booleans, `None`).

    Returns:
        The canonical text, identical to what the verify page produces in the browser.

    Raises:
        TypeError: For values JSON cannot represent.
        ValueError: For NaN or infinity.
    """
    return _canonical(value)


def record_hash(prev_hash: str, body: Mapping[str, object]) -> str:
    """Return `sha256(prev_hash + canonical(body))` as hex.

    Args:
        prev_hash: The previous record's hash (or the genesis hash).
        body: The record as plain JSON, without its `hash` field.

    Returns:
        The 64-character hex digest.
    """
    material = prev_hash + canonical_json(body)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _plain(payload: Mapping[str, object] | BaseModel) -> dict[str, object]:
    if isinstance(payload, BaseModel):
        return payload.model_dump(mode="json")
    return json.loads(json.dumps(payload, default=str))  # type: ignore[no-any-return]


def _record_line(record: EventRecord) -> str:
    return canonical_json(record.model_dump(mode="json"))


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    lock_path = path.with_name(path.name + ".lock")
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _last_line(path: Path) -> str | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        position = handle.tell() - 1  # skip the final newline
        while position > 0:
            position -= 1
            handle.seek(position)
            if handle.read(1) == b"\n":
                break
        else:
            handle.seek(0)
        return handle.readline().decode("utf-8").rstrip("\n")


class EventChain:
    """Appends hash-chained records for one site."""

    def __init__(self, root: Path, site: str, *, clock: Clock) -> None:
        """Create the chain writer.

        Args:
            root: Directory holding one sub-directory per site (`data/evidence/chain`).
            site: Site identifier; becomes a directory name, so it must be a plain name.
            clock: Time source for record timestamps (UTC).

        Raises:
            ValueError: If `site` is empty or contains a path separator.
        """
        if not site or Path(site).name != site or site in {".", ".."}:
            msg = f"invalid site name {site!r}"
            raise ValueError(msg)
        self._root = root
        self._site = site
        self._clock = clock

    def path_for(self, day: date) -> Path:
        """Return the chain file of `day` (UTC)."""
        return self._root / self._site / f"{day.isoformat()}.jsonl"

    def append(self, kind: EventKind, payload: Mapping[str, object] | BaseModel) -> EventRecord:
        """Append one record to today's chain file and return it.

        Args:
            kind: The record kind (`candidate`, `verdict`, `action`, …).
            payload: A contract model or plain JSON mapping; models are dumped in JSON mode.

        Returns:
            The record as written, with its `seq`, `prev_hash` and `hash`.

        Raises:
            ValueError: If `kind` is unknown or the payload holds NaN or infinity.
            ChainError: If the existing last line cannot be read as a record.
        """
        if kind not in EVENT_KINDS:
            msg = f"unknown event kind {kind!r}"
            raise ValueError(msg)
        ts = self._clock.now().astimezone(UTC)
        path = self.path_for(ts.date())
        path.parent.mkdir(parents=True, exist_ok=True)
        with _locked(path):
            seq, prev_hash = self._next_link(path)
            body: dict[str, object] = {
                "seq": seq,
                "kind": kind,
                "ts": ts.isoformat().replace("+00:00", "Z"),
                "site": self._site,
                "payload": _plain(payload),
                "prev_hash": prev_hash,
            }
            record = EventRecord.model_validate({**body, "hash": record_hash(prev_hash, body)})
            with path.open("a", encoding="utf-8") as handle:
                handle.write(_record_line(record) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        _log.info("chain_appended", site=self._site, seq=seq, kind=kind)
        return record

    @staticmethod
    def _next_link(path: Path) -> tuple[int, str]:
        last = _last_line(path)
        if last is None:
            return FIRST_SEQ, GENESIS_HASH
        try:
            data = json.loads(last)
            return int(data["seq"]) + 1, str(data["hash"])
        except (ValueError, KeyError, TypeError) as exc:
            msg = f"cannot extend {path}: last line is not a record"
            raise ChainError(msg) from exc


def _check_line(raw: bytes, expected_seq: int, prev_hash: str) -> tuple[str | None, str]:
    """Return (problem or None, this record's hash) for one raw line."""
    try:
        text = raw.decode("utf-8")
        data = json.loads(text)
        EventRecord.model_validate(data)
    except (UnicodeDecodeError, ValueError, ValidationError):
        return "not a valid record", ""
    claimed = str(data["hash"])
    if canonical_json(data) != text:
        return "not in canonical form", claimed
    if data["seq"] != expected_seq:
        return f"seq {data['seq']} out of order", claimed
    if data["prev_hash"] != prev_hash:
        return "prev_hash does not link to the previous record", claimed
    body = {k: v for k, v in data.items() if k != "hash"}
    if record_hash(prev_hash, body) != claimed:
        return "hash does not match contents", claimed
    return None, claimed


def verify_chain(path: Path) -> ChainReport:
    """Recompute and check every link of one chain file.

    Args:
        path: A chain JSONL file.

    Returns:
        `ok=True` with the record count, or the position of the first broken record.

    Raises:
        FileNotFoundError: If `path` does not exist.
    """
    data = path.read_bytes()
    lines = data.split(b"\n")
    if lines[-1] == b"":
        lines.pop()
    prev_hash = GENESIS_HASH
    for index, raw in enumerate(lines):
        seq = FIRST_SEQ + index
        problem, prev_hash = _check_line(raw, seq, prev_hash)
        if problem is None and index == len(lines) - 1 and not data.endswith(b"\n"):
            problem = "file does not end with a newline"
        if problem is not None:
            return ChainReport(ok=False, first_bad_seq=seq, n=index + 1, reason=problem)
    return ChainReport(ok=True, n=len(lines))


def read_chain(path: Path) -> list[EventRecord]:
    """Parse every record of a chain file (without verifying it; use `verify_chain` for that).

    Args:
        path: A chain JSONL file.

    Returns:
        The records in file order.
    """
    with path.open(encoding="utf-8") as handle:
        return [EventRecord.model_validate_json(line) for line in handle if line.strip()]
