"""Telling staff: an alert with the marked photo for every finding the models said to act on.

The alert goes through the site's outbound notifier (the alerts app on staff phones, plus
Telegram or WhatsApp when they are set up), addressed to a role, never a person, with the marked
frame as its picture and *On my way* / *False alarm* buttons. A shelf finding also becomes a
restock task. Every alert is written to the evidence chain (`advice` record, `retail_finding`).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Final

from rulecam.act.notify import Alert, for_event
from rulecam.contracts import Clock, Notifier
from rulecam.evidence.chain import EventChain
from rulecam.log import get_logger
from rulecam.retail.checks import new_id
from rulecam.retail.models import Finding, StaffTask
from rulecam.retail.store import TaskBoard

_log = get_logger(__name__)

BUTTONS: Final = ("On my way", "False alarm")
DEFAULT_ROLES: Final = {"queue": "tills", "hazard": "floor", "shelf": "stock"}
TITLES: Final = {
    "queue": "Checkout",
    "hazard": "Floor hazard",
    "shelf": "Restock",
}

AlertLookup = Callable[[str], Alert | None]


class StaffDesk:
    """Sends acted-on findings to staff and keeps the restock board."""

    def __init__(
        self,
        *,
        notifier: Notifier,
        alerts: AlertLookup,
        tasks: TaskBoard,
        chain: EventChain | None,
        clock: Clock,
        image_path: Callable[[str], Path | None],
    ) -> None:
        """Create the desk.

        Args:
            notifier: The site's outbound notifier (LAN alerts, Telegram, WhatsApp).
            alerts: Looks an alert up by id (its status shows whether staff answered).
            tasks: The restock board.
            chain: The evidence chain, when there is one.
            clock: Time source.
            image_path: Resolves an evidence file name to its path (the alert's picture).
        """
        self._notifier = notifier
        self._alerts = alerts
        self._tasks = tasks
        self._chain = chain
        self._clock = clock
        self._image = image_path

    def dispatch(self, finding: Finding, role: str | None = None) -> Finding:
        """Alert `role` (the kind's default role when `None`) when the finding says to act.

        Returns:
            The finding with its `alert_id` (and `task_id` for a shelf finding) filled in.
        """
        if not finding.act:
            return finding
        who = role or DEFAULT_ROLES[finding.kind]
        task = self._task(finding, who) if finding.kind in {"shelf", "hazard"} else None
        picture = self._image(finding.evidence.marked) if finding.evidence.marked else None
        title = f"{TITLES[finding.kind]}: {finding.camera_name or finding.camera_id}"
        body = finding.staff_message or finding.headline
        announce = finding.detail.get("announcement")
        if isinstance(announce, str) and announce:
            body = f"{body}\nAnnounce: {announce}"
        with for_event(finding.id, finding.watch_id):
            alert_id = self._notifier.send(
                who,
                title,
                body,
                clip_path=str(picture) if picture else None,
                buttons=BUTTONS,
            )
        if task is not None:
            self._tasks.put(task.model_copy(update={"alert_id": alert_id}))
        sent = finding.model_copy(
            update={"alert_id": alert_id, "task_id": task.id if task else None}
        )
        self._record(sent, who)
        _log.info("retail_alert", kind=finding.kind, role=who, alert_id=alert_id)
        return sent

    def answered(self, finding: Finding) -> str | None:
        """The status of the finding's alert (`open`, `acknowledged`, `false_alarm`)."""
        if finding.alert_id is None:
            return None
        alert = self._alerts(finding.alert_id)
        return alert.status if alert is not None else None

    def _task(self, finding: Finding, role: str) -> StaffTask:
        task = StaffTask(
            id=new_id("t"),
            kind="shelf" if finding.kind == "shelf" else "hazard",
            finding_id=finding.id,
            camera_id=finding.camera_id,
            where=finding.camera_name or finding.camera_id,
            items=task_items(finding),
            marked=finding.evidence.marked,
            seen_at=finding.at or finding.checked_at,
            created_at=self._clock.now(),
            role=role,
        )
        return self._tasks.put(task)

    def record_fixed(
        self, task: StaffTask, *, how: str, minutes_to_fix: float, at: datetime
    ) -> int | None:
        """Write a fixed task to the chain: what, where, seen when, fixed when and how.

        Returns:
            The chain record's seq (`None` without a chain).
        """
        if self._chain is None:
            return None
        record = self._chain.append(
            "advice",
            {
                "type": "retail_fixed",
                "task_id": task.id,
                "kind": task.kind,
                "finding_id": task.finding_id,
                "camera_id": task.camera_id,
                "items": task.items,
                "seen_at": (task.seen_at or task.created_at).isoformat(),
                "fixed_at": at.isoformat(),
                "minutes_to_fix": minutes_to_fix,
                "closed_by": how,
            },
        )
        return record.seq

    def _record(self, finding: Finding, role: str) -> None:
        if self._chain is None:
            return
        self._chain.append(
            "advice",
            {
                "type": "retail_finding",
                "finding_id": finding.id,
                "kind": finding.kind,
                "camera_id": finding.camera_id,
                "at": finding.at.isoformat() if finding.at else None,
                "watch_id": finding.watch_id,
                "headline": finding.headline,
                "staff_message": finding.staff_message,
                "role": role,
                "alert_id": finding.alert_id,
                "task_id": finding.task_id,
                "models": sorted({c.model for c in finding.calls}),
            },
        )


def task_items(finding: Finding) -> list[str]:
    """One line per gap or confirmed hazard, from the models' reading."""
    if finding.kind == "shelf":
        gaps = finding.detail.get("gaps")
        return [
            f"gap {g.get('number')}: {g.get('section', '?')} next to {g.get('neighbours', '?')} "
            f"(~{g.get('facings', '?')} facings)"
            for g in (gaps if isinstance(gaps, list) else [])
            if isinstance(g, dict)
        ]
    hazards = finding.detail.get("hazards")
    items = []
    for h in hazards if isinstance(hazards, list) else []:
        item = h.get("item") if isinstance(h, dict) else None
        if isinstance(h, dict) and h.get("confirmed") and isinstance(item, dict):
            items.append(
                f"hazard {h.get('number')}: {item.get('what', '?')} ({item.get('severity')})"
            )
    return items
