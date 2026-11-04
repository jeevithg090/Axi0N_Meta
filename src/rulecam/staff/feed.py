"""The staff feed: one role-aware list of everything frontline staff should act on.

Camera AI alerts usually land on a dashboard nobody watches; the people who can act are on the
floor with a phone. This module turns what Rulecam already produces into cards for them:

- **Rule alerts** and **human-queue reviews** from the runtime (verified by Muse Glimmer), and
  **crowd warnings** from the crowd warden (Spark assesses, Glimmer checks the frames). Both reach
  staff through the site's `LanNotifier`, so the feed reads its alerts and joins each to the
  event's chained candidate, verdicts and escalation steps (`ChainView`).
- **Dry-run alerts**: while the site's actions run in dry run nothing is sent, but the chain says
  who *would* have been told; those appear too, flagged `dry_run`, so staff can rehearse.
- **Shelf tasks**: restock tasks made from shelf gaps (Muse Spark finds them, SAM 3.1 confirms
  they are empty; `rulecam.shelf`).

Every tap (Acknowledge, On my way, Done with an optional photo, False alarm, Escalate) is appended
to the evidence chain as an `action` record with the role and, for *Done*, the photo's SHA-256.
Acknowledging cancels the escalation timer through the existing `EscalationManager`; *False
alarm* goes through `act.api.handle_button`, the same path as the alerts PWA and Telegram: an ack,
a `false_alarm` label for rule health and the sink that records a permanent Rule CI regression.

Response-time stats are per role and per shift, never per person: nothing here knows who tapped.
"""

from __future__ import annotations

import hashlib
import re
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import get_args
from zoneinfo import ZoneInfo

from rulecam.act.api import FalseAlarmSink, RespondRequest, handle_button
from rulecam.act.escalation import EscalationManager
from rulecam.act.notify import Alert, LanNotifier
from rulecam.bus import Bus, Topic
from rulecam.contracts import ActionRecord, Clock
from rulecam.errors import RulecamError
from rulecam.evidence.chain import EventChain
from rulecam.log import get_logger
from rulecam.runtime.loop import ActionDone
from rulecam.staff.chainview import ChainView, EventRecords
from rulecam.staff.models import (
    ActionResult,
    Bucket,
    ItemStatus,
    RoleInfo,
    Source,
    StaffActionKind,
    StaffActionRequest,
    StaffItem,
    StaffStats,
    Stage,
    TakenAction,
    TimelineStep,
    VerdictView,
)
from rulecam.staff.signal import Signal
from rulecam.staff.store import ShelfTask, Sighting, StaffStore

_log = get_logger(__name__)

DEFAULT_AFTER_S = 60.0  # `act.actions.DEFAULT_ESCALATE_AFTER_S`, when the rule names none
DRILL_PREFIX = "drill-"  # `act.drills.DRILL_PREFIX`
CROWD_RULE = "crowd-watch"  # the rule id the crowd warden chains its warnings under
SHELF_RULE = "shelf-gap"
STEP = re.compile(r"step (\d+)/(\d+)")
WOULD = re.compile(r"^dry-run: would (notify|escalate) (.+?)(?: \(needs review\))?$")
SPOKEN = re.compile(r"^announcement \(([A-Za-z-]{2,12})\): (.+)$", re.DOTALL)
STATUS_RANK: Mapping[ItemStatus, int] = {
    "open": 0,
    "acknowledged": 1,
    "on_my_way": 2,
    "done": 3,
    "false_alarm": 4,
}
STATUS_OF: Mapping[StaffActionKind, ItemStatus] = {
    "acknowledge": "acknowledged",
    "on_my_way": "on_my_way",
    "done": "done",
    "false_alarm": "false_alarm",
}
SHIFTS: Mapping[str, tuple[int, int]] = {"early": (6, 14), "late": (14, 22), "night": (22, 6)}
STAGE_ORDER: tuple[Stage, ...] = get_args(Stage)
TAPS = frozenset({"acknowledge", "on_my_way", "done", "false_alarm"})
ACK_STAGES = frozenset({"acknowledged", "on_my_way", "done", "false_alarm"})


class StaffError(RulecamError):
    """A tap that cannot be applied (unknown item, nobody to escalate to)."""


class UnknownItemError(StaffError):
    """No item with that id."""


@dataclass
class _Card:
    """An item with what the feed needs to act on it and to time responses."""

    item: StaffItem
    reached: dict[str, datetime] = field(default_factory=dict)
    media: list[Path] = field(default_factory=list)
    rule_id: str | None = None
    entry: EventRecords = field(default_factory=EventRecords)


def _dict(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _ts(raw: object) -> datetime | None:
    if isinstance(raw, datetime):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _sent_order(alert: Alert) -> int:
    """The notifier's running number of an alert (`al-12` → 12), for alerts sent at one time."""
    tail = alert.alert_id.rsplit("-", 1)[-1]
    return int(tail) if tail.isdigit() else 0


def _roles(text: str) -> tuple[str, ...]:
    """Roles from a rule's role spec (`a, b`) or a dry-run line (`a → b`)."""
    return tuple(r.strip() for r in re.split(r",|→", text) if r.strip())


def measurement_text(candidate: Mapping[str, object]) -> str | None:
    """A candidate's measurement as `0.42 m`, when it has one."""
    measured = _dict(candidate.get("measurement"))
    value = measured.get("value")
    if not isinstance(value, int | float):
        return None
    return f"{value:.2f} {measured.get('unit', '')}".strip()


def verdict_view(entry: EventRecords) -> VerdictView | None:
    """The newest verifier verdict of an event (decisions without a verdict are skipped)."""
    for payload in reversed(entry.verdicts):
        verdict = payload.get("verdict")
        if not isinstance(verdict, str):
            continue
        score = payload.get("score")
        return VerdictView(
            model=str(payload.get("model_version") or payload.get("tier") or "verifier"),
            verdict=verdict,
            confidence=float(score) if isinstance(score, int | float) else None,
            reason=str(payload.get("reason", ""))[:400],
            late=payload.get("late") is True,
        )
    return None


def escalation_timer(
    escalation: EscalationManager, event_id: str
) -> tuple[str | None, datetime | None]:
    """The next role of an event's open escalation chain and when it is told.

    Reads the manager's own state (its roles, step and `next_at`), so the phone's countdown is
    the real timer whoever started the chain (a rule, the crowd warden, a drill, a tap here).

    Returns:
        `(None, None)` when no chain is waiting.
    """
    with escalation._lock:
        state = escalation._open.get(event_id)
        if state is None or state.next_at is None or state.step >= len(state.roles):
            return None, None
        return state.roles[state.step], state.next_at


TAP_STAGE: Mapping[StaffActionKind, Stage] = {
    "acknowledge": "acknowledged",
    "on_my_way": "on_my_way",
    "done": "done",
    "false_alarm": "false_alarm",
    "escalate": "escalated",
}
TAP_WORDS: Mapping[StaffActionKind, str] = {
    "acknowledge": "acknowledged",
    "on_my_way": "on the way",
    "done": "done",
    "false_alarm": "marked false alarm",
    "escalate": "escalated",
}


def _raised(item: StaffItem, entry: EventRecords) -> tuple[datetime, str]:
    """When the item was raised, and how: a camera event, a crowd reading, a task, an alert."""
    candidate = entry.candidate or {}
    seen = _ts(candidate.get("ts_start"))
    where = item.camera_name or item.camera_id
    if entry.candidate_ts is not None:
        on = f" on {where}" if where else ""
        at = f" ({seen:%H:%M:%S} UTC on camera)" if seen is not None else ""
        return entry.candidate_ts, f"Raised{on}{at}"
    if item.source == "shelf":
        return item.ts, f"Gap found on {where or 'a shelf photo'}"
    return item.ts, "Raised"


Step = tuple[Stage, datetime, str, str | None]


def _verdict_steps(entry: EventRecords) -> list[Step]:
    steps: list[Step] = []
    for verdict in entry.verdicts:
        word, ts = verdict.get("verdict"), _ts(verdict.get("_ts"))
        if isinstance(word, str) and ts is not None:
            score = verdict.get("score")
            scored = f" ({score:.2f})" if isinstance(score, int | float) else ""
            model = verdict.get("model_version") or verdict.get("tier") or "verifier"
            steps.append(("verified", ts, f"{model}: {word}{scored}", None))
    return steps


def _action_steps(entry: EventRecords) -> list[Step]:
    """Chained actions other than sends (shown as `assigned`) and staff-app taps."""
    steps: list[Step] = []
    for action in entry.actions:
        ts, detail = _ts(action.get("_ts")), str(action.get("detail", ""))
        role, kind = action.get("role"), action.get("kind")
        skip = action.get("source") == "staff_app" or kind == "notify"
        if ts is None or skip or detail.startswith("dry-run: would"):
            continue
        stage: Stage = "escalated" if kind == "escalate" else "record"
        steps.append((stage, ts, detail or str(kind), role if isinstance(role, str) else None))
    return steps


def _people_steps(entry: EventRecords, taps: Sequence[TakenAction]) -> list[Step]:
    """Acks and labels from other channels (Telegram, the alerts page, the crowd page)."""
    tapped = {(t.role, t.action) for t in taps}
    steps: list[Step] = []
    for ack in entry.acks:
        role, ts = str(ack.get("role", "")), _ts(ack.get("_ts"))
        if ts is not None and not any(r == role for r, _ in tapped):
            steps.append(("acknowledged", ts, f"{role} acknowledged", role))
    for label in entry.labels:
        role, ts = str(label.get("role") or label.get("source") or ""), _ts(label.get("_ts"))
        if ts is None or (role, "false_alarm") in tapped:
            continue
        word = str(label.get("label", ""))
        stage: Stage = "false_alarm" if word == "false_alarm" else "record"
        steps.append((stage, ts, f"Labelled {word.replace('_', ' ')} by {role or '?'}", role))
    return steps


def _record_steps(entry: EventRecords, taps: Sequence[TakenAction]) -> list[Step]:
    """Every chained step of an event that is not a staff-app tap or a send already shown."""
    others: list[Step] = [
        ("record", ts, record_words(other), None)
        for other in entry.others
        if (ts := _ts(other.get("_ts"))) is not None
    ]
    return [*_verdict_steps(entry), *_action_steps(entry), *_people_steps(entry, taps), *others]


WORDS_FROM: tuple[str, ...] = ("detail", "headline", "outcome", "closed_by", "status")


def record_words(payload: Mapping[str, object]) -> str:
    """Plain words for a chained record the feed has no special view of (data, never code)."""
    kind = str(payload.get("type") or payload.get("_kind") or "record").replace("_", " ")
    words = [str(payload[k]) for k in WORDS_FROM if isinstance(payload.get(k), str)]
    minutes = payload.get("minutes_to_fix")
    if isinstance(minutes, int | float):
        words.append(f"fixed in {minutes:.1f} min")
    return f"{kind}: {'; '.join(words)}"[:300] if words else kind


def loop_of(
    item: StaffItem, entry: EventRecords, reached: Mapping[str, datetime]
) -> dict[str, object]:
    """The item's loop from camera to action, with time to acknowledge and time to fix.

    Returns:
        `timeline`, `ack_s` and `fix_s` for `StaffItem.model_copy`.
    """
    raised, how = _raised(item, entry)
    steps: list[Step] = [("raised", raised, how, None)]
    steps += [("assigned", ts, f"Sent to {role}", role) for role, ts in reached.items()]
    steps += _record_steps(entry, item.actions)
    for tap in item.actions:
        note = f": {tap.note}" if tap.note else ""
        steps.append(
            (TAP_STAGE[tap.action], tap.ts, f"{tap.role} {TAP_WORDS[tap.action]}{note}", tap.role)
        )
    steps.sort(key=lambda s: (s[1], STAGE_ORDER.index(s[0])))
    timeline = tuple(
        TimelineStep(
            stage=stage,
            ts=ts,
            label=label,
            role=role,
            after_s=round((ts - raised).total_seconds(), 1),
        )
        for stage, ts, label, role in steps
    )
    told = min(reached.values(), default=None)
    acked = min((s.ts for s in timeline if s.stage in ACK_STAGES), default=None)
    done = min((s.ts for s in timeline if s.stage == "done"), default=None)
    return {
        "timeline": timeline,
        "ack_s": round((acked - told).total_seconds(), 1) if acked and told else None,
        "fix_s": round((done - raised).total_seconds(), 1) if done else None,
    }


def percentile(values: Sequence[float], q: float) -> float | None:
    """The `q`-th percentile (0-100) by linear interpolation; `None` for no values."""
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q / 100.0
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (pos - low), 3)


def shift_of(moment: datetime, tz: ZoneInfo) -> str:
    """The shift (`early`, `late`, `night`) a moment falls in, in the site's time zone."""
    hour = moment.astimezone(tz).hour
    for name, (start, end) in SHIFTS.items():
        if (start <= hour < end) if start < end else (hour >= start or hour < end):
            return name
    return "night"


class StaffFeed:
    """Builds the feed, applies taps and computes response-time stats."""

    def __init__(
        self,
        *,
        notifier: LanNotifier,
        escalation: EscalationManager,
        chain: EventChain,
        view: ChainView,
        store: StaffStore,
        clock: Clock,
        bus: Bus,
        signal: Signal,
        media_roots: Sequence[Path],
        camera_names: Mapping[str, str] | None = None,
        dry_run: bool = False,
        on_false_alarm: FalseAlarmSink | None = None,
        timezone: str = "UTC",
        plan_roles: Callable[[], set[str]] | None = None,
    ) -> None:
        """Create the feed.

        Args:
            notifier: The site's LAN alert store (rule alerts and crowd warnings land here).
            escalation: Escalation chains (acks cancel them; *Escalate* advances them).
            chain: The evidence chain every tap is appended to.
            view: Read-only index of the chain by event.
            store: The staff app's state (taps, sightings, shelf tasks).
            clock: Time source.
            bus: Bus for `action.done` (the dashboards see staff taps live).
            signal: Wakes open `/staff/stream` connections.
            media_roots: The only folders evidence stills and photos are served from.
            camera_names: Camera id → friendly name.
            dry_run: The site's actions run in dry run.
            on_false_alarm: Records a *False alarm* as a Rule CI regression.
            timezone: The site's IANA time zone (shifts).
            plan_roles: Roles the site's plans name.
        """
        self._notifier = notifier
        self._escalation = escalation
        self._chain = chain
        self._view = view
        self._store = store
        self._clock = clock
        self._bus = bus
        self._signal = signal
        self._roots = [root.resolve() for root in media_roots]
        self._names = dict(camera_names or {})
        self._dry_run = dry_run
        self._on_false_alarm = on_false_alarm
        self._tz = ZoneInfo(timezone)
        self._timezone = timezone
        self._plan_roles = plan_roles

    @property
    def signal(self) -> Signal:
        """Fired whenever a tap or a task changes the feed."""
        return self._signal

    @property
    def store(self) -> StaffStore:
        """The staff app's state."""
        return self._store

    # Reading -------------------------------------------------------------------------------

    def items(self, role: str | None = None) -> list[StaffItem]:
        """The feed for `role` (everything when `None`), newest first."""
        cards = self._cards()
        return [c.item for c in cards if role is None or role in c.item.roles]

    def item(self, item_id: str) -> StaffItem | None:
        """One item, or `None`."""
        card = self._card(item_id)
        return card.item if card is not None else None

    def media(self, item_id: str, index: int) -> Path | None:
        """The `index`-th evidence still or photo of an item, only from the allowed folders."""
        card = self._card(item_id)
        if card is None or not 0 <= index < len(card.media):
            return None
        return card.media[index]

    def roles(self) -> list[RoleInfo]:
        """Every role the site uses: plans, alerts, the chain's notify steps and shelf tasks."""
        found: dict[str, set[str]] = {}
        for role in self._plan_roles() if self._plan_roles is not None else set():
            found.setdefault(role, set()).add("plan")
        for card in self._cards():
            for role in card.item.roles:
                found.setdefault(role, set()).add(card.item.source)
        return [RoleInfo(role=r, sources=tuple(sorted(s))) for r, s in sorted(found.items())]

    def _cards(self) -> list[_Card]:
        self._view.refresh()
        groups: dict[str, list[Alert]] = {}
        for alert in sorted(self._notifier.list(), key=lambda a: (a.ts, _sent_order(a))):
            groups.setdefault(alert.event_id or alert.alert_id, []).append(alert)
        tasks = {task.task_id: task for task in self._store.tasks()}
        cards = [self._task_card(t, groups.get(t.task_id, [])) for t in tasks.values()]
        cards += [self._alert_card(k, a) for k, a in groups.items() if k not in tasks]
        cards += self._dry_cards(skip=set(groups) | set(tasks))
        for card in cards:
            card.item = card.item.model_copy(update=loop_of(card.item, card.entry, card.reached))
        return sorted(cards, key=lambda c: c.item.ts, reverse=True)

    def _card(self, item_id: str) -> _Card | None:
        return next((c for c in self._cards() if c.item.item_id == item_id), None)

    def _alert_card(self, key: str, alerts: Sequence[Alert]) -> _Card:
        entry = self._view.get(key) or EventRecords()
        candidate = entry.candidate or {}
        first, last = alerts[0], alerts[-1]
        reached: dict[str, datetime] = {}
        for alert in alerts:
            reached.setdefault(alert.role, alert.ts)
        rule_id = str(candidate.get("rule_id") or first.rule_id or "") or None
        clips = [a.clip_path for a in alerts if a.clip_path]
        media = self._media(candidate, clips, key)
        step, next_role, at = self._escalation_state(key, entry)
        item = StaffItem(
            item_id=key,
            source=self._source(key, rule_id, first.title),
            title=last.title,
            body=last.body,
            roles=tuple(reached),
            ts=first.ts,
            status=self._status(key, alerts, entry),
            escalation_step=step,
            next_role=next_role,
            escalates_at=at,
            thumbnails=len(media),
        ).model_copy(update=self._context(key, entry))
        return _Card(item=item, reached=reached, media=media, rule_id=rule_id, entry=entry)

    def _context(self, key: str, entry: EventRecords) -> dict[str, object]:
        """The fields an event's chained records give a card."""
        candidate = entry.candidate or {}
        rule = _dict(_dict(entry.context).get("rule"))
        camera = candidate.get("camera_id")
        camera_id = camera if isinstance(camera, str) else None
        assessment = entry.assessment or {}
        do_now = assessment.get("staff_actions")
        return {
            "severity": str(candidate.get("severity") or ""),
            "camera_id": camera_id,
            "camera_name": self._names.get(camera_id) if camera_id else None,
            "rule_text": str(_dict(rule.get("source")).get("text") or "") or None,
            "measurement": measurement_text(candidate),
            "verdict": verdict_view(entry),
            "do_now": tuple(str(x) for x in do_now) if isinstance(do_now, list) else (),
            "announcements": tuple(self._announcements(entry)),
            "actions": tuple(self._store.actions(key)),
            "dry_run": self._dry_run,
        }

    @staticmethod
    def _announcements(entry: EventRecords) -> list[dict[str, str]]:
        found = []
        for action in entry.actions:
            if action.get("kind") != "speak":
                continue
            match = SPOKEN.match(str(action.get("detail", "")))
            if match:
                found.append({"lang": match.group(1), "text": match.group(2)})
        return found

    def _dry_cards(self, skip: set[str]) -> list[_Card]:
        """Chained alerts that dry run kept from being sent: who would have been told."""
        cards = []
        for event_id in self._view.event_ids():
            entry = self._view.get(event_id)
            if event_id in skip or entry is None:
                continue
            would = [
                (m, _ts(a.get("ts")) or _ts(a.get("_ts")))
                for a in entry.actions
                if (m := WOULD.match(str(a.get("detail", ""))))
            ]
            if not would:
                continue
            roles = tuple(dict.fromkeys(r for m, _ in would for r in _roles(m.group(2))))
            ts = would[0][1] or entry.candidate_ts or self._clock.now()
            candidate = entry.candidate or {}
            rule_id = str(candidate.get("rule_id") or "") or None
            media = self._media(candidate, [], event_id)
            context = self._context(event_id, entry)
            text = context["rule_text"] or rule_id or "rule"
            human = any("(needs review)" in str(a.get("detail", "")) for a in entry.actions)
            item = StaffItem(
                item_id=event_id,
                source="review" if human else self._source(event_id, rule_id, ""),
                title=f"{'Needs review' if human else 'Alert'}: {text}",
                body=f"Camera {candidate.get('camera_id', '?')} at {ts:%H:%M:%S} UTC",
                roles=roles,
                ts=ts,
                status=self._status(event_id, [], entry),
                thumbnails=len(media),
            ).model_copy(update={**context, "dry_run": True})
            reached = dict.fromkeys(roles, ts)
            cards.append(
                _Card(item=item, reached=reached, media=media, rule_id=rule_id, entry=entry)
            )
        return cards

    def _task_card(self, task: ShelfTask, alerts: Sequence[Alert]) -> _Card:
        reached = {task.role: task.ts}
        for alert in alerts:
            reached.setdefault(alert.role, alert.ts)
        media = self._allowed([task.image] if task.image else [])
        media += self._allowed(self._store.photos(task.task_id))
        step, next_role, at = self._escalation_state(task.task_id, EventRecords())
        empty = round(1.0 - task.product_cover, 2)
        item = StaffItem(
            item_id=task.task_id,
            source="shelf",
            title=f"Restock: {task.section}",
            body=f"Gap next to {task.neighbours}. About {task.facings} facings missing.",
            roles=tuple(reached),
            severity="task",
            camera_id=task.camera_id,
            camera_name=self._names.get(task.camera_id),
            ts=task.ts,
            status=self._status(task.task_id, alerts, EventRecords()),
            verdict=VerdictView(
                model=f"{task.model or 'muse-spark-1.3'} + sam-3.1",
                verdict="empty",
                confidence=empty,
                reason=f"Muse Spark marked the gap; SAM 3.1 found product in "
                f"{task.product_cover:.0%} of it.",
            ),
            thumbnails=len(media),
            escalation_step=step,
            next_role=next_role,
            escalates_at=at,
            actions=tuple(self._store.actions(task.task_id)),
            sample=task.sample,
        )
        return _Card(item=item, reached=reached, media=media, rule_id=SHELF_RULE)

    @staticmethod
    def _source(key: str, rule_id: str | None, title: str) -> Source:
        if key.startswith(DRILL_PREFIX):
            return "drill"
        if rule_id == CROWD_RULE:
            return "crowd"
        if title.startswith("Needs review"):
            return "review"
        return "rule"

    def _status(self, key: str, alerts: Sequence[Alert], entry: EventRecords) -> ItemStatus:
        found: list[ItemStatus] = ["open"]
        found += [STATUS_OF[a.action] for a in self._store.actions(key) if a.action in STATUS_OF]
        found += [a.status for a in alerts]
        if any(label.get("label") == "false_alarm" for label in entry.labels):
            found.append("false_alarm")
        if entry.acks:
            found.append("acknowledged")
        return max(found, key=lambda s: STATUS_RANK[s])

    def _escalation_state(
        self, key: str, entry: EventRecords
    ) -> tuple[str | None, str | None, datetime | None]:
        """`k/n`, the next role and when it is told, from the chained escalation steps."""
        steps = [
            (action, match)
            for action in entry.actions
            if action.get("kind") in {"notify", "escalate"}
            and (match := STEP.search(str(action.get("detail", ""))))
        ]
        if not steps:
            return None, None, None
        _, match = steps[-1]
        done, total = int(match.group(1)), int(match.group(2))
        step = f"{done}/{total}"
        if done >= total:
            return step, None, None
        next_role, at = escalation_timer(self._escalation, key)
        return step, next_role, at

    def _media(self, candidate: Mapping[str, object], clips: Sequence[str], key: str) -> list[Path]:
        strips = candidate.get("strip_paths")
        raw = [str(p) for p in strips] if isinstance(strips, list) else []
        raw += [c for c in clips if c not in raw]
        return self._allowed(raw) + self._allowed(self._store.photos(key))

    def _allowed(self, raw: Sequence[str]) -> list[Path]:
        kept = []
        for text in raw:
            path = Path(text).resolve()
            if any(path.is_relative_to(root) for root in self._roots) and path.is_file():
                kept.append(path)
        return kept

    # Acting --------------------------------------------------------------------------------

    def act(
        self, item_id: str, request: StaffActionRequest, *, photo: Path | None = None
    ) -> ActionResult:
        """Apply one tap, chain it and wake the open streams.

        Args:
            item_id: The item.
            request: The tap, the tapping role and an optional note.
            photo: A photo taken with *Done* (already saved under a media root).

        Returns:
            The item after the tap and what the tap did.

        Raises:
            UnknownItemError: For an unknown item.
            StaffError: When *Escalate* has no open chain and names no role.
        """
        card = self._card(item_id)
        if card is None:
            msg = f"no item {item_id}"
            raise UnknownItemError(msg)
        if request.action == "escalate":
            did = self._escalate(card, request)
        elif request.action == "false_alarm":
            handle_button(
                RespondRequest(button="False alarm", role=request.role),
                item_id,
                notifier=self._notifier,
                escalation=self._escalation,
                chain=self._chain,
                rule_id=card.rule_id,
                on_false_alarm=self._on_false_alarm,
            )
            did = "false alarm: labelled for rule health and sent to Rule CI as a regression"
        else:
            if self._escalation.ack(item_id, request.role) is None and card.item.status == "open":
                self._escalation.record_ack(item_id, request.role)
            self._notifier.set_status(item_id, "acknowledged")
            did = {"acknowledge": "acknowledged", "on_my_way": "on the way", "done": "done"}[
                request.action
            ]
        taken = self._chain_tap(item_id, request, photo)
        self._store.record_action(item_id, taken, str(photo) if photo is not None else None)
        self._signal.fire()
        after = self.item(item_id)
        return ActionResult(item=after or card.item, did=did)

    def _escalate(self, card: _Card, request: StaffActionRequest) -> str:
        """Send it to the role the person picked, else move the open chain on now."""
        if request.to_role is None:
            record = self._escalation.escalate_now(card.item.item_id)
            if record is not None:
                return f"escalated to {record.role}"
            msg = "nothing left to escalate to: choose who to send it to"
            raise StaffError(msg)
        self._escalation.start(
            card.item.item_id,
            (request.to_role,),
            after_s=DEFAULT_AFTER_S,
            title=card.item.title,
            body=card.item.body,
            clip_path=str(card.media[0]) if card.media else None,
            rule_id=card.rule_id,
        )
        return f"sent to {request.to_role}"

    def _chain_tap(
        self, item_id: str, request: StaffActionRequest, photo: Path | None
    ) -> TakenAction:
        now = self._clock.now()
        detail = f"staff app: {request.action.replace('_', ' ')}"
        extra: dict[str, object] = {"source": "staff_app", "staff_action": request.action}
        if request.note:
            detail += f"; note: {request.note}"
        if request.to_role:
            extra["to_role"] = request.to_role
        if photo is not None:
            digest = hashlib.sha256(photo.read_bytes()).hexdigest()
            extra["photo_sha256"] = digest
            detail += f"; photo sha256 {digest[:16]}"
        record = ActionRecord(
            kind="log",
            role=request.role,
            gate="human",
            approved_by_role=request.role,
            ts=now,
            ok=True,
            detail=detail,
        )
        chained = self._chain.append(
            "action", {"event_id": item_id, **record.model_dump(mode="json"), **extra}
        )
        self._bus.publish(Topic.ACTION_DONE, ActionDone(event_id=item_id, action=record))
        return TakenAction(
            action=request.action,
            role=request.role,
            ts=now,
            note=request.note,
            chain_hash=chained.hash,
        )

    def add_task(self, task: ShelfTask) -> StaffItem:
        """Chain a new shelf task, remember it and wake the streams."""
        self._chain.append(
            "action",
            {
                "event_id": task.task_id,
                "kind": "log",
                "role": task.role,
                "gate": "auto",
                "ts": task.ts.isoformat(),
                "ok": True,
                "detail": f"staff app: shelf task for {task.role}: restock {task.section} "
                f"(product cover {task.product_cover:.0%})",
                "source": "staff_app",
                "model": task.model,
            },
        )
        self._store.add_task(task)
        self._signal.fire()
        made = self.item(task.task_id)
        if made is None:  # pragma: no cover - the task was just stored
            msg = f"task {task.task_id} vanished"
            raise StaffError(msg)
        return made

    def seen(self, item_id: str, role: str, client_ms: float | None) -> float | None:
        """Record the first time a role's phone showed an item.

        Returns:
            Milliseconds from the alert reaching that role to now, on the first sighting.
        """
        card = self._card(item_id)
        if card is None:
            msg = f"no item {item_id}"
            raise UnknownItemError(msg)
        now = self._clock.now()
        first = self._store.mark_seen(
            Sighting(item_id=item_id, role=role, at=now, client_ms=client_ms)
        )
        sent = card.reached.get(role, card.item.ts)
        return round((now - sent).total_seconds() * 1000, 1) if first else None

    # Stats ---------------------------------------------------------------------------------

    def stats(self, hours: float = 24.0) -> StaffStats:
        """Response times per role and per shift over the last `hours` (live items only)."""
        since = self._clock.now() - timedelta(hours=hours)
        cards = [c for c in self._cards() if c.item.ts >= since and not c.item.sample]
        seen: dict[tuple[str, str], Sighting] = {
            (s.item_id, s.role): s for s in self._store.sightings()
        }
        by_role: dict[str, list[_Timing]] = {}
        by_shift: dict[str, list[_Timing]] = {name: [] for name in SHIFTS}
        for card in cards:
            for role, sent in card.reached.items():
                by_role.setdefault(role, []).append(_timing(card, sent, seen, role))
            by_shift[shift_of(card.item.ts, self._tz)].append(
                _timing(card, card.item.ts, seen, None)
            )
        return StaffStats(
            since=since,
            timezone=self._timezone,
            shifts={k: f"{a:02d}:00-{b:02d}:00" for k, (a, b) in SHIFTS.items()},
            by_role=[_bucket(k, v) for k, v in sorted(by_role.items())],
            by_shift=[_bucket(k, v) for k, v in by_shift.items()],
        )


@dataclass(frozen=True)
class _Timing:
    ack_s: float | None
    done_s: float | None
    false_alarm: bool
    escalated: bool
    phone_ms: float | None


def _timing(
    card: _Card, sent: datetime, seen: Mapping[tuple[str, str], Sighting], role: str | None
) -> _Timing:
    """How one role (or, with `None`, anyone) responded to one item."""
    taps = [a for a in card.item.actions if role is None or a.role == role]
    first = min((a.ts for a in taps if a.action in TAPS), default=None)
    done = min((a.ts for a in taps if a.action == "done"), default=None)
    roles = card.item.roles
    passed_on = role is not None and role in roles and roles.index(role) < len(roles) - 1
    phone = min(
        (
            s.at
            for (item_id, seen_role), s in seen.items()
            if item_id == card.item.item_id and (role is None or seen_role == role)
        ),
        default=None,
    )
    return _Timing(
        ack_s=(first - sent).total_seconds() if first is not None else None,
        done_s=(done - sent).total_seconds() if done is not None else None,
        false_alarm=any(a.action == "false_alarm" for a in taps),
        escalated=passed_on or any(a.action == "escalate" for a in taps),
        phone_ms=(phone - sent).total_seconds() * 1000 if phone is not None else None,
    )


def _bucket(key: str, timings: Sequence[_Timing]) -> Bucket:
    acks = [t.ack_s for t in timings if t.ack_s is not None]
    dones = [t.done_s for t in timings if t.done_s is not None]
    phones = [t.phone_ms for t in timings if t.phone_ms is not None]
    return Bucket(
        key=key,
        items=len(timings),
        acknowledged=len(acks),
        done=len(dones),
        false_alarms=sum(t.false_alarm for t in timings),
        escalated=sum(t.escalated for t in timings),
        ack_p50_s=percentile(acks, 50),
        ack_p90_s=percentile(acks, 90),
        done_p50_s=round(statistics.median(dones), 3) if dones else None,
        phone_p50_ms=percentile(phones, 50),
        phone_p95_ms=percentile(phones, 95),
    )
