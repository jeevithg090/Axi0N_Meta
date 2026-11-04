"""Wakes the staff app's open event streams when something changes, from any thread."""

from __future__ import annotations

import asyncio
import threading


class Signal:
    """A broadcast "something changed" for asyncio listeners, safe to fire from worker threads.

    Each `/staff/stream` connection registers an `asyncio.Event` on its own loop; `fire` sets
    every registered event through that loop's `call_soon_threadsafe`.
    """

    def __init__(self) -> None:
        """Create a signal with no listeners."""
        self._lock = threading.Lock()
        self._listeners: list[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = []

    def listen(self) -> asyncio.Event:
        """Register a listener on the running loop (call from a coroutine)."""
        event = asyncio.Event()
        with self._lock:
            self._listeners.append((asyncio.get_running_loop(), event))
        return event

    def forget(self, event: asyncio.Event) -> None:
        """Remove a listener."""
        with self._lock:
            self._listeners = [(lp, ev) for lp, ev in self._listeners if ev is not event]

    def fire(self) -> None:
        """Wake every listener."""
        with self._lock:
            listeners = list(self._listeners)
        for loop, event in listeners:
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:  # that loop is closed: the stream is gone
                self.forget(event)
