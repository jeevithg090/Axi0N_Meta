"""Executes a rule's `ActionSpec`s and returns `ActionRecord`s (task P3-06, V2 Pillar 5).

`ActionExecutor` is the runtime loop's `ActionRunner`. The loop chains and publishes every
record it returns.

- **Suppressed** (`alert=False`, route `auto`): nothing runs.
- **Human queue:** only `notify` and `escalate` run, worded as "needs review", so a person decides.
  Nothing speaks, flashes or cuts power on an unconfirmed LOW/MEDIUM alert.
- **Alert:** every action runs. `interlock_cut` goes **first**, before the speaker or any
  notification, so the plug is off within milliseconds of the candidate for HIGH rules (the runtime
  calls this before verification). Power comes back only through `Interlock.resume` with a person's
  role.

A failing device never stops the other actions: its record says `ok=False`.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from rulecam.act.devices import Light, Speaker
from rulecam.act.errors import ActionError, ResumeRefusedError
from rulecam.act.escalation import EscalationManager
from rulecam.act.notify import for_event
from rulecam.bus import Bus, Topic
from rulecam.contracts import (
    ActionRecord,
    CandidateEvent,
    Clock,
    Notifier,
    Plug,
    RuleIR,
)
from rulecam.contracts.rule_ir import ActionSpec
from rulecam.evidence.chain import EventChain
from rulecam.log import get_logger
from rulecam.runtime.loop import ActionDone
from rulecam.verify.decision import Decision
from rulecam.voice_out.prerender import PrerenderedPhrase

_log = get_logger(__name__)

DEFAULT_ESCALATE_AFTER_S = 60.0
HUMAN_QUEUE_KINDS = frozenset({"notify", "escalate"})
_ORDER = {"interlock_cut": 0, "visual_signal": 1, "speak": 2, "coach_station": 2}


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

    def cut(self, device: str | None = None) -> None:
        """Cut power to one device, or to every device when `device` is `None`."""
        for name in [device] if device else list(self._plugs):
            self._plugs[name].off()

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
        by_phrase = {(p.source, p.lang): Path(p.path) for p in rendered}
        wavs = {
            (rule_id, lang): path
            for rule_id, phrase in rule_phrases.items()
            for (source, lang), path in by_phrase.items()
            if source == phrase.strip()
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
        monotonic: Callable[[], float] = time.perf_counter,
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
            monotonic: Timer for the interlock latency detail.
        """
        self._clock = clock
        self._notifier = notifier
        self._escalation = escalation
        self._interlock = interlock
        self._speaker = speaker
        self._light = light
        self._phrases = phrases
        self._monotonic = monotonic

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
        human = decision.route == "human_queue" and not decision.act_first
        specs = [s for s in rule.actions if not human or s.kind in HUMAN_QUEUE_KINDS]
        specs.sort(key=lambda s: _ORDER.get(s.kind, 3))
        with for_event(candidate.event_id):
            return [self._one(spec, candidate, rule, human=human) for spec in specs]

    def _one(
        self, spec: ActionSpec, candidate: CandidateEvent, rule: RuleIR, *, human: bool
    ) -> ActionRecord:
        try:
            detail = self._dispatch(spec, candidate, rule, human=human)
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
        self, spec: ActionSpec, candidate: CandidateEvent, rule: RuleIR, *, human: bool
    ) -> str:
        handlers: dict[str, Callable[[], str]] = {
            "interlock_cut": self._cut,
            "visual_signal": self._strobe,
            "speak": lambda: self._speak(rule.id, spec.lang),
            "coach_station": lambda: self._speak(rule.id, spec.lang) + "; station display",
            "notify": lambda: self._notify(spec, candidate, rule, human=human),
            "escalate": lambda: self._escalate(spec, candidate, rule, human=human),
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

    @staticmethod
    def _message(candidate: CandidateEvent, rule: RuleIR, *, human: bool) -> tuple[str, str]:
        title = f"{'Needs review' if human else 'Alert'}: {rule.source.text or rule.id}"
        body = (
            f"Camera {candidate.camera_id} at {candidate.ts_start:%H:%M:%S} UTC ({rule.severity})"
        )
        return title, body

    def _notify(
        self, spec: ActionSpec, candidate: CandidateEvent, rule: RuleIR, *, human: bool
    ) -> str:
        role = spec.role or "site_lead"
        title, body = self._message(candidate, rule, human=human)
        clip = candidate.strip_paths[0] if candidate.strip_paths else None
        alert_id = self._notifier.send(role, title, body, clip_path=clip)
        return f"notified {role} ({alert_id})"

    def _escalate(
        self, spec: ActionSpec, candidate: CandidateEvent, rule: RuleIR, *, human: bool
    ) -> str:
        roles = tuple(r.strip() for r in (spec.role or "site_lead").split(",") if r.strip())
        title, body = self._message(candidate, rule, human=human)
        self._escalation.start(
            candidate.event_id,
            roles,
            after_s=spec.after_s or DEFAULT_ESCALATE_AFTER_S,
            title=title,
            body=body,
            clip_path=candidate.strip_paths[0] if candidate.strip_paths else None,
        )
        return f"escalation {' → '.join(roles)}"
