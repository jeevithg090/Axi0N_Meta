"""Executes a rule's `ActionSpec`s and returns `ActionRecord`s (task P3-06, V2 Pillar 5).

`ActionExecutor` is the runtime loop's `ActionRunner`. The loop chains and publishes every
record it returns.

- **Suppressed** (`alert=False`, route `auto`): nothing runs.
- **Human queue:** only `notify` and `escalate` run, worded as "needs review" with the policy's
  reason in the title ("Needs review: verifier unavailable — …"), so a person decides. A rule
  with neither still reaches a person: `site_lead` is notified (`REVIEW_ROLE`), so a human-queue
  decision is never silent. Nothing speaks, flashes or cuts power on an unconfirmed LOW/MEDIUM
  alert. For HIGH the runtime calls this again after a verifier demoted an alert it already acted
  on (or could not verify it).
- **Alert:** every action runs. `interlock_cut` goes **first**, then `notify`/`escalate`, then the
  slow, blocking outputs (strobe, speaker, station). The plug is off within milliseconds of the
  candidate for HIGH rules (the runtime calls this before verification), and people are told before
  a multi-second warning plays. Power comes back only through `Interlock.resume` with a person's
  role.

A failing device never stops the other actions: its record says `ok=False`.

**Dry run** (`RULECAM_ACTIONS_DRY_RUN`, on until the owner turns it off): nothing is executed (no
power cut, light, speaker, station, notification or escalation chain), but every action the
decision allows is still recorded, with `detail` starting `dry-run: would …`, so the evidence
chain shows exactly what a live site would have done.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from rulecam.act.devices import Connectable, Light, Speaker
from rulecam.act.errors import ActionError, DeviceError, ResumeRefusedError
from rulecam.act.escalation import ALERT_BUTTONS, EscalationManager
from rulecam.act.notify import for_event
from rulecam.bus import Bus, Topic
from rulecam.contracts import (
    ActionRecord,
    CandidateEvent,
    Clock,
    Frozen,
    Notifier,
    Plug,
    RuleIR,
)
from rulecam.contracts.rule_ir import ActionSpec
from rulecam.evidence.chain import EventChain
from rulecam.log import get_logger
from rulecam.runtime.loop import ActionDone
from rulecam.verify.decision import Decision
from rulecam.voice_out.prerender import PrerenderedPhrase, normalize_phrase

_log = get_logger(__name__)

DEFAULT_ESCALATE_AFTER_S = 60.0
DRY_RUN_PREFIX = "dry-run: "
HUMAN_QUEUE_KINDS = frozenset({"notify", "escalate"})
REVIEW_ROLE = "site_lead"
_ORDER = {
    "interlock_cut": 0,
    "notify": 1,
    "escalate": 1,
    "visual_signal": 2,
    "speak": 3,
    "coach_station": 3,
}


class StationCoach(Frozen):
    """Payload of `station.coach`: what the coaching station display shows."""

    event_id: str
    rule_id: str
    camera_id: str
    text: str
    lang: str | None = None


class Interlock:
    """The smart-plug interlock: automatic cut, **human-only resume**."""

    def __init__(
        self, plugs: Mapping[str, Plug], *, clock: Clock, chain: EventChain, bus: Bus
    ) -> None:
        """Create the interlock.

        Args:
            plugs: Device name → plug (e.g. `{"forklift": KasaPlug(...)}`).
            clock: Time source for records.
            chain: Evidence chain for resume records.
            bus: Bus for `action.done`.
        """
        self._plugs = dict(plugs)
        self._clock = clock
        self._chain = chain
        self._bus = bus

    @property
    def devices(self) -> tuple[str, ...]:
        """Configured device names."""
        return tuple(self._plugs)

    def connect(self) -> list[str]:
        """Connect every plug that supports it, so the first cut is one round trip.

        Call once at start-up. A plug that cannot be reached is logged and retried on first use.

        Returns:
            Names of the devices that failed to connect.
        """
        failed: list[str] = []
        for name, plug in self._plugs.items():
            if not isinstance(plug, Connectable):
                continue
            try:
                plug.connect()
            except DeviceError as exc:
                failed.append(name)
                _log.warning("interlock_connect_failed", device=name, error=str(exc))
        return failed

    def cut(self, device: str | None = None) -> None:
        """Cut power to one device, or to every device when `device` is `None`.

        Every plug is tried even if an earlier one fails.

        Raises:
            DeviceError: Naming every plug that did not switch off.
        """
        failed: list[str] = []
        for name in [device] if device else list(self._plugs):
            try:
                self._plugs[name].off()
            except (DeviceError, OSError) as exc:
                failed.append(f"{name} ({exc})")
                _log.error("interlock_cut_failed", device=name, error=str(exc))
        if failed:
            msg = "power cut failed for " + ", ".join(failed)
            raise DeviceError(msg)

    def resume(self, device: str, role: str) -> ActionRecord:
        """Restore power after a person checked the area.

        Args:
            device: Device name.
            role: The approving role (required; never a person's name).

        Returns:
            The `interlock_resume` record (chained and published).

        Raises:
            ResumeRefusedError: Without a role.
            KeyError: For an unknown device.
        """
        if not role.strip():
            msg = "resuming the interlock needs a person's role"
            raise ResumeRefusedError(msg)
        self._plugs[device].on()
        record = ActionRecord(
            kind="interlock_resume",
            role=role,
            gate="human",
            approved_by_role=role,
            ts=self._clock.now(),
            ok=True,
            detail=device,
        )
        self._chain.append("action", {"event_id": None, **record.model_dump(mode="json")})
        self._bus.publish(Topic.ACTION_DONE, ActionDone(event_id="interlock", action=record))
        _log.info("interlock_resumed", device=device, role=role)
        return record


@dataclass(frozen=True)
class PhraseBook:
    """Pre-rendered warning WAVs (P1-11) per rule and language.

    Attributes:
        wavs: `(rule_id, lang) → WAV path`.
        crew_languages: Languages spoken for `lang: crew` (and `auto`), in order.
    """

    wavs: Mapping[tuple[str, str], Path]
    crew_languages: tuple[str, ...]

    @classmethod
    def from_prerendered(
        cls,
        rule_phrases: Mapping[str, str],
        rendered: Sequence[PrerenderedPhrase],
        crew_languages: Sequence[str],
    ) -> PhraseBook:
        """Index P1-11's pre-rendered phrases by rule.

        Args:
            rule_phrases: Rule id → the warning phrase (source language).
            rendered: `Prerenderer.prerender(...)` output.
            crew_languages: The plan's languages.

        Returns:
            The phrase book.
        """
        by_phrase = {(normalize_phrase(p.source), p.lang): Path(p.path) for p in rendered}
        wavs = {
            (rule_id, lang): path
            for rule_id, phrase in rule_phrases.items()
            for (source, lang), path in by_phrase.items()
            if source == normalize_phrase(phrase)
        }
        return cls(wavs=wavs, crew_languages=tuple(crew_languages))

    def wavs_for(self, rule_id: str, lang: str | None) -> list[Path]:
        """Return the WAVs for a rule in order (`crew`, `auto` or `None`: every crew language)."""
        langs = self.crew_languages if lang in (None, "crew", "auto") else (lang,)
        return [self.wavs[(rule_id, lg)] for lg in langs if (rule_id, lg) in self.wavs]


class ActionExecutor:
    """The runtime `ActionRunner`."""

    def __init__(
        self,
        *,
        clock: Clock,
        notifier: Notifier,
        escalation: EscalationManager,
        interlock: Interlock | None = None,
        speaker: Speaker | None = None,
        light: Light | None = None,
        phrases: PhraseBook | None = None,
        bus: Bus | None = None,
        monotonic: Callable[[], float] = time.perf_counter,
        dry_run: bool = False,
    ) -> None:
        """Create the executor; missing devices make their actions record `ok=False`.

        Args:
            clock: Time source for records.
            notifier: Role notifications (LAN app).
            escalation: Escalation chains.
            interlock: Smart-plug interlock.
            speaker: Site speaker.
            light: Stack light.
            phrases: Pre-rendered warnings.
            bus: Bus for the coaching station display (`station.coach`, mirrored on `/ws`).
            monotonic: Timer for the interlock latency detail.
            dry_run: Record what would run without running it (module docstring).
        """
        self._clock = clock
        self._notifier = notifier
        self._escalation = escalation
        self._interlock = interlock
        self._speaker = speaker
        self._light = light
        self._phrases = phrases
        self._bus = bus
        self._monotonic = monotonic
        self._dry_run = dry_run

    @property
    def dry_run(self) -> bool:
        """Whether actions are only recorded, never executed."""
        return self._dry_run

    def run(
        self, candidate: CandidateEvent, rule: RuleIR, decision: Decision
    ) -> list[ActionRecord]:
        """Run the actions `decision` allows, interlock first.

        Args:
            candidate: The candidate.
            rule: Its rule (its `actions` are executed).
            decision: The policy's decision (or the runtime's act-first decision).

        Returns:
            One record per executed action.
        """
        if not decision.alert and decision.route == "auto":
            return []
        human = decision.route == "human_queue"
        specs = [s for s in rule.actions if not human or s.kind in HUMAN_QUEUE_KINDS]
        if human and not specs:  # the human queue always reaches a person
            specs = [ActionSpec(kind="notify", role=REVIEW_ROLE)]
        specs.sort(key=lambda s: _ORDER.get(s.kind, 3))
        why = decision.reason if human else ""
        with for_event(candidate.event_id, rule.id):
            return [self._one(spec, candidate, rule, human=human, why=why) for spec in specs]

    def _one(
        self,
        spec: ActionSpec,
        candidate: CandidateEvent,
        rule: RuleIR,
        *,
        human: bool,
        why: str = "",
    ) -> ActionRecord:
        if self._dry_run:
            return ActionRecord(
                kind=spec.kind,
                role=spec.role,
                gate="human" if human else "auto",
                ts=self._clock.now(),
                ok=True,
                detail=DRY_RUN_PREFIX + would(spec, human=human),
            )
        try:
            detail = self._dispatch(spec, candidate, rule, human=human, why=why)
            ok = True
        except (ActionError, OSError, KeyError) as exc:
            detail, ok = f"{type(exc).__name__}: {exc}", False
            _log.warning("action_failed", kind=spec.kind, event_id=candidate.event_id, error=detail)
        return ActionRecord(
            kind=spec.kind,
            role=spec.role,
            gate="human" if human else "auto",
            ts=self._clock.now(),
            ok=ok,
            detail=detail,
        )

    def _dispatch(
        self, spec: ActionSpec, candidate: CandidateEvent, rule: RuleIR, *, human: bool, why: str
    ) -> str:
        handlers: dict[str, Callable[[], str]] = {
            "interlock_cut": self._cut,
            "visual_signal": self._strobe,
            "speak": lambda: self._speak(rule.id, spec.lang),
            "coach_station": lambda: self._coach(candidate, rule, spec.lang),
            "notify": lambda: self._notify(spec, candidate, rule, human=human, why=why),
            "escalate": lambda: self._escalate(spec, candidate, rule, human=human, why=why),
            "log": lambda: "logged",
        }
        return handlers[spec.kind]()

    def _cut(self) -> str:
        if self._interlock is None:
            msg = "no interlock configured"
            raise ActionError(msg)
        started = self._monotonic()
        self._interlock.cut()
        return f"power cut in {round((self._monotonic() - started) * 1000)} ms"

    def _strobe(self) -> str:
        if self._light is None:
            msg = "no light configured"
            raise ActionError(msg)
        self._light.strobe()
        return "red strobe"

    def _speak(self, rule_id: str, lang: str | None) -> str:
        if self._speaker is None or self._phrases is None:
            msg = "no speaker or phrase book configured"
            raise ActionError(msg)
        wavs = self._phrases.wavs_for(rule_id, lang)
        if not wavs:
            msg = f"no pre-rendered warning for {rule_id} ({lang or 'crew'})"
            raise ActionError(msg)
        for wav in wavs:
            self._speaker.play(wav)
        return "played " + ", ".join(w.name for w in wavs)

    def _coach(self, candidate: CandidateEvent, rule: RuleIR, lang: str | None) -> str:
        if self._bus is None:
            msg = "no station display (bus) configured"
            raise ActionError(msg)
        self._bus.publish(
            Topic.STATION_COACH,
            StationCoach(
                event_id=candidate.event_id,
                rule_id=rule.id,
                camera_id=candidate.camera_id,
                text=rule.source.text or rule.id,
                lang=lang,
            ),
        )
        return "station display; " + self._speak(rule.id, lang)

    @staticmethod
    def _message(
        candidate: CandidateEvent, rule: RuleIR, *, human: bool, why: str = ""
    ) -> tuple[str, str]:
        """The alert's title and body; a human-queue alert with a reason leads with the reason."""
        text = rule.source.text or rule.id
        where = (
            f"Camera {candidate.camera_id} at {candidate.ts_start:%H:%M:%S} UTC ({rule.severity})"
        )
        if human and why:
            return f"Needs review: {why}", f"{text}. {where}"
        return f"{'Needs review' if human else 'Alert'}: {text}", where

    def _notify(
        self, spec: ActionSpec, candidate: CandidateEvent, rule: RuleIR, *, human: bool, why: str
    ) -> str:
        role = spec.role or REVIEW_ROLE
        title, body = self._message(candidate, rule, human=human, why=why)
        clip = candidate.strip_paths[0] if candidate.strip_paths else None
        alert_id = self._notifier.send(role, title, body, clip_path=clip, buttons=ALERT_BUTTONS)
        return f"notified {role} ({alert_id})"

    def _escalate(
        self, spec: ActionSpec, candidate: CandidateEvent, rule: RuleIR, *, human: bool, why: str
    ) -> str:
        roles = tuple(r.strip() for r in (spec.role or REVIEW_ROLE).split(",") if r.strip())
        title, body = self._message(candidate, rule, human=human, why=why)
        self._escalation.start(
            candidate.event_id,
            roles,
            after_s=spec.after_s or DEFAULT_ESCALATE_AFTER_S,
            title=title,
            body=body,
            clip_path=candidate.strip_paths[0] if candidate.strip_paths else None,
            rule_id=rule.id,
        )
        return f"escalation {' → '.join(roles)}"


def would(spec: ActionSpec, *, human: bool) -> str:
    """Describe what `spec` would do, for a dry-run record (`would notify site_lead`)."""
    review = " (needs review)" if human else ""
    roles = " → ".join(r.strip() for r in (spec.role or "site_lead").split(",") if r.strip())
    lang = spec.lang or "crew"
    text = {
        "interlock_cut": "would cut power",
        "visual_signal": "would strobe the light",
        "speak": f"would play the warning ({lang})",
        "coach_station": f"would show the station display and play the warning ({lang})",
        "notify": f"would notify {spec.role or 'site_lead'}{review}",
        "escalate": f"would escalate {roles}{review}",
        "log": "logged",
    }
    return text[spec.kind]
