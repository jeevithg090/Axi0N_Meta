"""A staff feed wired to the real notifier, escalation chains and evidence chain."""

from __future__ import annotations

from pathlib import Path

import pytest

from rulecam.act import EscalationManager, LanNotifier
from rulecam.act.notify import for_event
from rulecam.bus import Bus
from rulecam.evidence.chain import EventChain, read_chain
from rulecam.staff.chainview import ChainView
from rulecam.staff.feed import StaffFeed
from rulecam.staff.signal import Signal
from rulecam.staff.store import StaffStore
from tests.fakes import FakeClock

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"


class Rig:
    def __init__(self, tmp_path: Path, *, dry_run: bool = False) -> None:
        self.tmp = tmp_path
        self.clock = FakeClock()
        self.bus = Bus()
        self.chain = EventChain(tmp_path / "chain", "store-1", clock=self.clock)
        self.notifier = LanNotifier(bus=self.bus, clock=self.clock)
        self.escalation = EscalationManager(
            notifier=self.notifier, clock=self.clock, chain=self.chain, bus=self.bus
        )
        self.false_alarms: list[tuple[str, str | None]] = []
        self.strips = tmp_path / "strips"
        self.strips.mkdir()
        self.feed = StaffFeed(
            notifier=self.notifier,
            escalation=self.escalation,
            chain=self.chain,
            view=ChainView(tmp_path / "chain" / "store-1", today=lambda: self.clock.now().date()),
            store=StaffStore(tmp_path / "staff" / "state.jsonl"),
            clock=self.clock,
            bus=self.bus,
            signal=Signal(),
            media_roots=(self.strips, tmp_path / "staff"),
            camera_names={"cam-1": "Aisle 4"},
            dry_run=dry_run,
            on_false_alarm=lambda e, r: self.false_alarms.append((e, r)),
            timezone="UTC",
            plan_roles=lambda: {"floor_lead"},
        )

    def still(self, name: str = "1.jpg") -> Path:
        path = self.strips / name
        path.write_bytes(JPEG)
        return path

    def candidate(
        self,
        event_id: str = "ev-1",
        *,
        rule_id: str = "keep-aisle-clear",
        strips: tuple[Path, ...] = (),
        actions: list[dict[str, object]] | None = None,
        assessment: dict[str, object] | None = None,
    ) -> None:
        self.chain.append(
            "candidate",
            {
                "candidate": {
                    "event_id": event_id,
                    "rule_id": rule_id,
                    "camera_id": "cam-1",
                    "severity": "high",
                    "ts_start": self.clock.now().isoformat(),
                    "ts_end": self.clock.now().isoformat(),
                    "measurement": {"value": 0.42, "unit": "m"},
                    "strip_paths": [str(p) for p in strips],
                },
                "context": {
                    "rule": {
                        "source": {"text": "Keep aisle 4 clear of pallets."},
                        "actions": actions or [],
                    }
                },
                **({"assessment": assessment} if assessment else {}),
            },
        )

    def verdict(self, event_id: str = "ev-1", score: float = 0.91) -> None:
        self.chain.append(
            "verdict",
            {
                "event_id": event_id,
                "tier": "glimmer",
                "verdict": "violation",
                "score": score,
                "reason": "A pallet stands in the aisle.",
                "latency_ms": 1200,
                "model_version": "muse-glimmer-30b",
            },
        )

    def rule_alert(self, event_id: str = "ev-1", roles: tuple[str, ...] = ("aisle_lead",)) -> None:
        still = self.still(f"{event_id}.jpg")
        self.candidate(event_id, strips=(still,))
        self.verdict(event_id)
        self.escalation.start(
            event_id,
            roles,
            after_s=60,
            title="Alert: Keep aisle 4 clear of pallets.",
            body="Camera cam-1 (high)",
            clip_path=str(still),
            rule_id="keep-aisle-clear",
        )

    def crowd_alert(self, event_id: str = "cw-1") -> None:
        self.candidate(
            event_id,
            rule_id="crowd-watch",
            assessment={"level": "warning", "staff_actions": ["Hold entry at gate 3"]},
        )
        self.chain.append(
            "action",
            {
                "event_id": event_id,
                "kind": "speak",
                "detail": "announcement (hi): kripya",
                "ok": True,
            },
        )
        with for_event(event_id):
            self.notifier.send("steward", "Crowd WARNING: Gate 3", "Density rising", buttons=())

    def records(self) -> list:  # type: ignore[type-arg]
        return read_chain(self.chain.path_for(self.clock.now().date()))


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)
