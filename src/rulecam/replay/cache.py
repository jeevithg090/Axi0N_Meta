"""The replay cache every model client goes through (task P3-02, task book §5.2).

A call is keyed by `sha256(provider | model | canonical_json(request))`. Binary values inside the
request (uploaded clips, images) are replaced by their SHA-256 before hashing, so keys stay small
and stable. Three modes, chosen by `RULECAM_MODE`:

- `live`: call the model, store nothing;
- `record`: call the model and store the reply;
- `replay`: serve from the cache only; a miss raises `ReplayMiss` and never touches the network.

Entries are one JSON file per key under `<replay_dir>/<provider>/`, written atomically (a
temporary file in the same directory, then `os.replace`), so concurrent writers never leave a
half-written entry. Every mode change publishes `mode.changed`, which drives the LIVE/REPLAY
badge: the UI must never switch silently.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from rulecam.bus import Bus, Topic
from rulecam.clients.transport import Transport
from rulecam.config import Settings
from rulecam.contracts import Clock, Frozen
from rulecam.log import get_logger
from rulecam.replay.errors import ReplayCorruptError, ReplayMiss

_log = get_logger(__name__)

Mode = Literal["live", "record", "replay"]
Badge = Literal["LIVE", "REPLAY"]
ModelCall = Callable[[], Mapping[str, object]]

_KEY_SEPARATOR = "|"


class ModeChanged(Frozen):
    """Payload of `mode.changed`.

    Attributes:
        mode: The new cache mode.
        badge: What the UI badge shows. `record` makes real calls, so it shows LIVE.
    """

    mode: Mode
    badge: Badge


class CacheEntry(Frozen):
    """One stored model reply.

    Attributes:
        key: The cache key (hex SHA-256).
        provider: The API provider, e.g. `meta`, `baseten`, `glimmer`.
        model: The model ID.
        recorded_at: When the reply was recorded (UTC).
        latency_ms: How long the original call took.
        response: The reply mapping, exactly as the call returned it.
    """

    key: str
    provider: str
    model: str
    recorded_at: datetime
    latency_ms: int = Field(ge=0)
    response: dict[str, object]


def badge_for(mode: Mode) -> Badge:
    """Return the UI badge for a mode (`replay` → REPLAY, otherwise LIVE)."""
    return "REPLAY" if mode == "replay" else "LIVE"


def _canonical_value(value: object) -> object:
    if isinstance(value, bytes | bytearray | memoryview):
        return {"__sha256__": hashlib.sha256(bytes(value)).hexdigest()}
    if isinstance(value, Mapping):
        return {str(k): _canonical_value(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_canonical_value(v) for v in value]
    return value


def canonical_json(value: object) -> str:
    """Serialise `value` deterministically: sorted keys, no whitespace, bytes as their SHA-256.

    Args:
        value: A JSON-like structure (mappings, sequences, scalars, bytes).

    Returns:
        The canonical JSON text; equal for structures that differ only in key order.

    Raises:
        TypeError: If `value` contains something JSON cannot represent.
    """
    return json.dumps(
        _canonical_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def cache_key(provider: str, model: str, request: Mapping[str, object]) -> str:
    """Return `sha256(provider | model | canonical_json(request))` as hex.

    Args:
        provider: The API provider.
        model: The model ID.
        request: The request mapping sent to the model.

    Returns:
        The 64-character hex key.
    """
    material = _KEY_SEPARATOR.join((provider, model, canonical_json(request)))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        Path(tmp_name).replace(path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


class ReplayCache:
    """A directory of recorded model replies, plus the current mode."""

    def __init__(
        self, directory: Path, mode: Mode, *, clock: Clock, bus: Bus | None = None
    ) -> None:
        """Create the cache.

        Args:
            directory: Root directory of entries (`RULECAM_REPLAY_DIR` or `data/replay`).
            mode: The starting mode.
            clock: Time source for `recorded_at`.
            bus: Bus for `mode.changed`; `None` for CLI tools and tests.
        """
        self._directory = directory
        self._mode: Mode = mode
        self._clock = clock
        self._bus = bus

    @classmethod
    def from_settings(
        cls, settings: Settings, *, clock: Clock, bus: Bus | None = None
    ) -> ReplayCache:
        """Build the cache from `RULECAM_MODE` and the configured replay directory.

        Args:
            settings: Application settings.
            clock: Time source.
            bus: Bus for `mode.changed`.

        Returns:
            The configured cache.
        """
        return cls(settings.replay_dir, settings.rulecam_mode, clock=clock, bus=bus)

    @property
    def mode(self) -> Mode:
        """The current mode."""
        return self._mode

    @property
    def directory(self) -> Path:
        """The root directory of entries."""
        return self._directory

    def set_mode(self, mode: Mode) -> None:
        """Switch mode and announce it on `mode.changed` (always, so the badge can never lag).

        Args:
            mode: The new mode.
        """
        previous, self._mode = self._mode, mode
        _log.info("replay_mode_changed", previous=previous, mode=mode)
        if self._bus is not None:
            self._bus.publish(Topic.MODE_CHANGED, ModeChanged(mode=mode, badge=badge_for(mode)))

    def entry_path(self, provider: str, key: str) -> Path:
        """Return where the entry for `key` lives."""
        return self._directory / provider / f"{key}.json"

    def lookup(self, provider: str, model: str, request: Mapping[str, object]) -> CacheEntry:
        """Return the stored entry for a request.

        Args:
            provider: The API provider.
            model: The model ID.
            request: The request mapping.

        Returns:
            The stored entry.

        Raises:
            ReplayMiss: If nothing was recorded for this request.
            ReplayCorruptError: If the entry exists but cannot be parsed.
        """
        key = cache_key(provider, model, request)
        path = self.entry_path(provider, key)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise ReplayMiss(provider=provider, model=model, key=key) from exc
        try:
            return CacheEntry.model_validate_json(text)
        except ValidationError as exc:
            msg = f"replay entry {path} is not a valid cache entry"
            raise ReplayCorruptError(msg) from exc

    def store(
        self,
        provider: str,
        model: str,
        request: Mapping[str, object],
        response: Mapping[str, object],
        latency_ms: int,
    ) -> CacheEntry:
        """Write one entry atomically (last writer wins; readers never see a partial file).

        Args:
            provider: The API provider.
            model: The model ID.
            request: The request mapping.
            response: The reply to store; must be JSON-serialisable.
            latency_ms: How long the call took.

        Returns:
            The stored entry.
        """
        key = cache_key(provider, model, request)
        entry = CacheEntry(
            key=key,
            provider=provider,
            model=model,
            recorded_at=self._clock.now(),
            latency_ms=latency_ms,
            response=dict(response),
        )
        _atomic_write(self.entry_path(provider, key), entry.model_dump_json(indent=2))
        return entry

    def call(
        self, provider: str, model: str, request: Mapping[str, object], call: ModelCall
    ) -> CacheEntry:
        """Serve one model call according to the mode.

        Args:
            provider: The API provider.
            model: The model ID.
            request: The request mapping (it is the cache key; it is not sent by this method).
            call: Performs the real call; never invoked in replay mode.

        Returns:
            The entry: recorded (replay), freshly stored (record) or unstored (live).

        Raises:
            ReplayMiss: In replay mode, when nothing was recorded for the request.
        """
        if self._mode == "replay":
            return self.lookup(provider, model, request)
        started = time.perf_counter()
        response = call()
        latency_ms = round((time.perf_counter() - started) * 1000)
        if self._mode == "record":
            return self.store(provider, model, request, response, latency_ms)
        return CacheEntry(
            key=cache_key(provider, model, request),
            provider=provider,
            model=model,
            recorded_at=self._clock.now(),
            latency_ms=latency_ms,
            response=dict(response),
        )

    def wrap_transport(self, provider: str, model: str, transport: Transport) -> Transport:
        """Return a `Transport` that serves `transport` through this cache.

        Args:
            provider: The API provider.
            model: The model ID used for every request through this transport.
            transport: The real transport, called only in live and record modes.

        Returns:
            A transport with the same request and reply shapes.
        """

        def cached_transport(request: Mapping[str, object]) -> Mapping[str, object]:
            return self.call(provider, model, request, lambda: transport(request)).response

        return cached_transport


_default_cache: ReplayCache | None = None


def configure(cache: ReplayCache | None) -> None:
    """Set (or with `None`, reset) the process-wide cache used by `cached_call`.

    Args:
        cache: The cache to use, normally built once at start-up with `from_settings`.
    """
    global _default_cache  # noqa: PLW0603 - one process-wide cache, set at start-up
    _default_cache = cache


def get_cache() -> ReplayCache:
    """Return the process-wide cache, building it from settings on first use."""
    if _default_cache is not None:
        return _default_cache
    from rulecam.clock import SystemClock  # noqa: PLC0415 - only needed on first use
    from rulecam.config import get_settings  # noqa: PLC0415

    cache = ReplayCache.from_settings(get_settings(), clock=SystemClock())
    configure(cache)
    return cache


def cached_call(
    provider: str, model: str, request: Mapping[str, object], call: ModelCall
) -> Mapping[str, object]:
    """Serve a model call through the process-wide replay cache (task book §5.2).

    Args:
        provider: The API provider.
        model: The model ID.
        request: The request mapping (the cache key).
        call: Performs the real call; never invoked in replay mode.

    Returns:
        The reply mapping.

    Raises:
        ReplayMiss: In replay mode, when nothing was recorded for the request.
    """
    return get_cache().call(provider, model, request, call).response
