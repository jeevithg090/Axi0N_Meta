"""Escalation chains with acknowledgement timers (task P3-06, MP §5 UC3).

An escalation notifies role A, waits `after_s` for an `Ack`, then notifies role B, and so on.
An ack (or a "false alarm" tap) cancels the rest. Every step is chained and published as an
`action` record.

The manager is driven by `tick()` against the injected clock (the API calls it every second),
so a test advances a `FakeClock` and calls `tick()` to replay a 60 s timeline instantly.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from rulecam.act.notify import for_event
from rulecam.bus import Bus, Topic
from rulecam.contracts import Ack, ActionRecord, Clock, Notifier
from rulecam.evidence.chain import EventChain
from rulecam.log import get_logger
from rulecam.runtime.loop import ActionDone

_log = get_logger(__name__)

ALERT_BUTTONS = ("On my way", "False alarm", "Escalate")


@dataclass
class _Chain:
    event_id: str
    roles: tuple[str, ...]
    after_s: float
    title: str
    body: str
    clip_path: str | None
    step: int = 0
    next_at: datetime | None = None
    acked_by: str | None = None
    records: list[ActionRecord] = field(default_factory=list)


class EscalationManager:
    """Runs every open escalation chain."""

    def __init__(self, *, notifier: Notifier, clock: Clock, chain: EventChain, bus: Bus) -> None:
        """Create the manager.

        Args:
            notifier: Sends each step (LAN app, WhatsApp later).
            clock: Time source for the timers.
            chain: Evidence chain for step, ack and cancel records.
            bus: Bus for `action.done`.
        """
        self._notifier = notifier
        self._clock = clock
        self._chain = chain
        self._bus = bus
        self._open: dict[str, _Chain] = {}
        self._lock = threading.Lock()

    def start(
        self,
        event_id: str,
        roles: Sequence[str],
        *,
        after_s: float,
        title: str,
        body: str,
        clip_path: str | None = None,
    ) -> ActionRecord:
        """Notify the first role now and arm the timer for the next.

        Args:
            event_id: The alert being escalated.
            roles: Roles in escalation order, e.g. `("aide", "charge_nurse", "on_call")`.
            after_s: Seconds to wait for an ack before each next role.
            title: Notification title.
            body: Notification body (anonymous: role, rule, place, never a person).
            clip_path: Clip shown with the notification (skeleton when anonymous).

        Returns:
            The record of the first notification.

        Raises:
            ValueError: If no roles are given.
        """
        if not roles:
            msg = "an escalation needs at least one role"
            raise ValueError(msg)
        state = _Chain(event_id, tuple(roles), after_s, title, body, clip_path)
        with self._lock:
            self._open[event_id] = state
            return self._notify_next(state)

    def _notify_next(self, state: _Chain) -> ActionRecord:
        role = state.roles[state.step]
        now = self._clock.now()
        with for_event(state.event_id):
            self._notifier.send(
                role, state.title, state.body, clip_path=state.clip_path, buttons=ALERT_BUTTONS
            )
        record = ActionRecord(
            kind="notify" if state.step == 0 else "escalate",
            role=role,
            gate="auto",
            ts=now,
            ok=True,
            detail=f"step {state.step + 1}/{len(state.roles)}",
        )
        state.step += 1
        last = state.step >= len(state.roles)
        state.next_at = None if last else now + timedelta(seconds=state.after_s)
        if last:
            self._open.pop(state.event_id, None)
        self._emit(state.event_id, record)
        return record

    def _emit(self, event_id: str, record: ActionRecord) -> None:
        self._chain.append("action", {"event_id": event_id, **record.model_dump(mode="json")})
        self._bus.publish(Topic.ACTION_DONE, ActionDone(event_id=event_id, action=record))

    def tick(self) -> list[ActionRecord]:
        """Fire every timer that is due.

        Returns:
            The escalation records sent by this tick.
        """
        now = self._clock.now()
        with self._lock:
            due = [s for s in self._open.values() if s.next_at is not None and s.next_at <= now]
            return [self._notify_next(state) for state in due]

    def escalate_now(self, event_id: str) -> ActionRecord | None:
        """Skip the wait and notify the next role (the "Escalate" button)."""
        with self._lock:
            state = self._open.get(event_id)
            return self._notify_next(state) if state is not None else None

    def ack(self, event_id: str, role: str) -> Ack | None:
        """Acknowledge an alert, cancelling the rest of its chain.

        Args:
            event_id: The alert.
            role: The acknowledging role (never a name).

        Returns:
            The ack, or `None` if no chain was open for it.
        """
        with self._lock:
            state = self._open.pop(event_id, None)
        if state is None:
            return None
        ack = Ack(role=role, ts=self._clock.now())
        self._chain.append("ack", {"event_id": event_id, **ack.model_dump(mode="json")})
        _log.info("escalation_acked", event_id=event_id, role=role, step=state.step)
        return ack

    def is_open(self, event_id: str) -> bool:
        """Whether a chain is still waiting for an ack."""
        with self._lock:
            return event_id in self._open
