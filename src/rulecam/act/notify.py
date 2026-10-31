"""LAN alerts for the phone PWA (`ui/app/alerts`): the must-tier `Notifier` (task P3-06).

`LanNotifier.send` stores the alert and publishes it on `alert.new`, which `/ws` mirrors to the
phones. Each alert carries the event it belongs to (taken from `alert_event`, a context variable
the action executor and escalation set around each send), so the *On my way*, *False alarm* and
*Escalate* buttons can act on the right chain. Alerts address roles, never people.
"""

from __future__ import annotations

import contextlib
import itertools
import threading
from collections.abc import Iterator
from contextvars import ContextVar
from datetime import datetime
from typing import Literal

from rulecam.bus import Bus, Topic
from rulecam.contracts import Clock, Frozen

AlertStatus = Literal["open", "acknowledged", "false_alarm"]

alert_event: ContextVar[str | None] = ContextVar("alert_event", default=None)


@contextlib.contextmanager
def for_event(event_id: str) -> Iterator[None]:
    """Attach `event_id` to every alert sent inside the block."""
    token = alert_event.set(event_id)
    try:
        yield
    finally:
        alert_event.reset(token)


class Alert(Frozen):
    """One alert shown in the phone app."""

    alert_id: str
    event_id: str | None
    role: str
    title: str
    body: str
    clip_path: str | None
    buttons: tuple[str, ...]
    ts: datetime
    status: AlertStatus = "open"


class LanNotifier:
    """Stores alerts in memory and pushes them over the bus (and so the WebSocket)."""

    def __init__(self, *, bus: Bus, clock: Clock) -> None:
        """Create the notifier.

        Args:
            bus: Bus whose `alert.new` topic `/ws` mirrors.
            clock: Time source for alert timestamps.
        """
        self._bus = bus
        self._clock = clock
        self._alerts: dict[str, Alert] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def send(
        self,
        role: str,
        title: str,
        body: str,
        *,
        clip_path: str | None = None,
        buttons: tuple[str, ...] = (),
    ) -> str:
        """Store and publish one alert (implements `Notifier`).

        Returns:
            The alert id.
        """
        with self._lock:
            alert = Alert(
                alert_id=f"al-{next(self._ids)}",
                event_id=alert_event.get(),
                role=role,
                title=title,
                body=body,
                clip_path=clip_path,
                buttons=buttons,
                ts=self._clock.now(),
            )
            self._alerts[alert.alert_id] = alert
        self._bus.publish(Topic.ALERT_NEW, alert)
        return alert.alert_id

    def get(self, alert_id: str) -> Alert | None:
        """Return one alert."""
        with self._lock:
            return self._alerts.get(alert_id)

    def list(self, role: str | None = None) -> list[Alert]:
        """Return alerts, newest first, optionally for one role."""
        with self._lock:
            alerts = [a for a in self._alerts.values() if role is None or a.role == role]
        return sorted(alerts, key=lambda a: a.ts, reverse=True)

    def set_status(self, event_id: str, status: AlertStatus) -> None:
        """Set the status of every alert of `event_id` (one ack closes them all)."""
        with self._lock:
            for alert_id, alert in self._alerts.items():
                if alert.event_id == event_id:
                    self._alerts[alert_id] = alert.model_copy(update={"status": status})
