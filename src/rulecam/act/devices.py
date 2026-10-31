"""Physical outputs: the Kasa smart plug (interlock), a strobing stack light, the site speaker.

`KasaPlug` keeps one connection on a private event-loop thread, so `off()` from the runtime's
worker thread is a single round trip (sub-second) instead of a fresh discovery. **There is no
automatic resume**: `on()` is only called by `Interlock.resume`, which requires a person's role.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any, Protocol

from rulecam.act.errors import DeviceError
from rulecam.contracts import Plug
from rulecam.log import get_logger

_log = get_logger(__name__)

KASA_TIMEOUT_S = 3.0


class KasaPlug:
    """`Plug` over python-kasa, talking to one device on a background event loop."""

    def __init__(self, host: str, *, timeout_s: float = KASA_TIMEOUT_S) -> None:
        """Connect lazily to the plug at `host`.

        Args:
            host: IP or hostname (`KASA_PLUG_HOST` / `KASA_LIGHT_HOST`).
            timeout_s: Per-command timeout.
        """
        self._host = host
        self._timeout_s = timeout_s
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._device: Any = None

    async def _connect(self) -> Any:
        if self._device is None:
            from kasa import Discover  # noqa: PLC0415 - optional hardware dependency

            device = await Discover.discover_single(self._host, discovery_timeout=3)
            if device is None:
                msg = f"no Kasa device at {self._host}"
                raise DeviceError(msg)
            self._device = device
        await self._device.update()
        return self._device

    async def _switch(self, on: bool) -> None:
        device = await self._connect()
        await (device.turn_on() if on else device.turn_off())

    def _run(self, coro: Coroutine[Any, Any, None]) -> None:
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            future.result(timeout=self._timeout_s)
        except (TimeoutError, OSError) as exc:
            msg = f"Kasa {self._host}: {type(exc).__name__}"
            raise DeviceError(msg) from exc

    def off(self) -> None:
        """Cut power."""
        self._run(self._switch(on=False))

    def on(self) -> None:
        """Restore power (call only through `Interlock.resume`)."""
        self._run(self._switch(on=True))


class Light(Protocol):
    """A visual signal for noisy sites and Deaf workers."""

    def strobe(self) -> None:
        """Flash the alert pattern."""
        ...


class StrobeLight:
    """A stack light or bulb on a smart plug, strobed by switching it."""

    def __init__(
        self,
        plug: Plug,
        *,
        flashes: int = 3,
        period_s: float = 0.3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Create the light.

        Args:
            plug: The plug the light hangs on.
            flashes: Number of on/off cycles; the light ends **on** (red stays visible).
            period_s: Half-period of one flash.
            sleep: Injected sleep for tests.
        """
        self._plug = plug
        self._flashes = flashes
        self._period_s = period_s
        self._sleep = sleep

    def strobe(self) -> None:
        """Flash, then stay on."""
        for _ in range(self._flashes):
            self._plug.on()
            self._sleep(self._period_s)
            self._plug.off()
            self._sleep(self._period_s)
        self._plug.on()


class Speaker(Protocol):
    """Plays a WAV on the site speaker."""

    def play(self, wav: Path) -> None:
        """Play `wav` (blocking)."""
        ...


Runner = Callable[[list[str]], int]


def _run(argv: list[str]) -> int:
    return subprocess.run(argv, check=False, capture_output=True, timeout=30).returncode  # noqa: S603 - fixed argv


class CommandSpeaker:
    """Plays WAVs with `afplay` (macOS) or `aplay` (Linux), as an argument list (never a shell)."""

    def __init__(self, player: str, *, run: Runner = _run) -> None:
        """Create the speaker.

        Args:
            player: `afplay` or `aplay`.
            run: Injected runner for tests.

        Raises:
            DeviceError: If the player is not installed.
        """
        path = shutil.which(player)
        if path is None:
            msg = f"{player} not found"
            raise DeviceError(msg)
        self._argv = [path]
        self._run = run

    def play(self, wav: Path) -> None:
        """Play one WAV file.

        Raises:
            DeviceError: If the player fails.
        """
        code = self._run([*self._argv, str(wav)])
        if code != 0:
            msg = f"speaker exited with {code}"
            raise DeviceError(msg)
