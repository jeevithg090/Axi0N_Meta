"""Hard-negative memory: confirmed false alarms become permanent Rule CI regressions (WS1f).

Two paths confirm that an alert was false:

- an **upheld dispute** (a worker disputed it and Tier 2 agreed, P2-11);
- an **owner correction** (the owner clicked the falsely segmented object, P1-10).

Each becomes one `LookalikeItem` appended with `append_regression` (P2-09) to the rule's file in
the site's regression folder (`RULECAM_REGRESSIONS_DIR`, default `<data>/rule_ci/regressions`),
which every later lookalike red-team run of that rule tests again. Commit a site's files into
`src/rulecam/rule_ci/regressions/` to make them part of the package's own Rule CI.

An item names the rule concept that was fooled and what fooled it; its prompt asks Muse Image to
recreate the situation in the camera's frame with the real concept absent. Only fixed wording
and identifiers go into it: a worker's free text is data and never reaches an image prompt.
Recording never breaks the path that confirmed the false alarm: a failure is logged.
"""

from __future__ import annotations

from pathlib import Path

from rulecam.contracts import CandidateEvent, Dispute, RuleIR
from rulecam.log import get_logger
from rulecam.rule_ci.lookalikes import LookalikeItem, append_regression, load_regressions

_log = get_logger(__name__)

_DISPUTE_LOOKS_LIKE = {
    "lookalike_object": "an object that looks like a {prompt}",
    "glare_or_shadow": "glare or a shadow shaped like a {prompt}",
    "wrong_place_or_distance": "a {prompt} that is outside the rule's area or distance",
    "rule_was_followed": "a {prompt} doing what the rule asks",
}
_DEFAULT_LOOKS_LIKE = "something the camera mistook for a {prompt}"


def subject_concept(rule: RuleIR) -> str:
    """The concept a false alarm is most likely about: the first dynamic one, else the first."""
    dynamic = [key for key, spec in rule.concepts.items() if not spec.static]
    return (dynamic or list(rule.concepts))[0]


def concept_key(rule: RuleIR, concept: str) -> str | None:
    """The rule's concept key for `concept`, which may be a key or a SAM prompt."""
    if concept in rule.concepts:
        return concept
    return next((k for k, spec in rule.concepts.items() if spec.prompt == concept), None)


def dispute_choice(dispute: Dispute) -> str:
    """The worker's fixed reason choice (the part of `reason_text` before the free text)."""
    return dispute.reason_text.split(":", maxsplit=1)[0].strip()


class HardNegatives:
    """Appends confirmed false alarms to a rule's permanent lookalike regressions."""

    def __init__(self, directory: Path) -> None:
        """Create the recorder.

        Args:
            directory: The regression folder (one `<rule_id>.jsonl` per rule).
        """
        self._directory = directory

    @property
    def directory(self) -> Path:
        """Where the regression files are."""
        return self._directory

    def from_dispute(
        self, candidate: CandidateEvent, rule: RuleIR, dispute: Dispute
    ) -> Path | None:
        """Record an upheld dispute.

        Args:
            candidate: The disputed alert.
            rule: Its rule.
            dispute: The upheld dispute (its reason choice picks the wording).

        Returns:
            The regression file, or `None` when nothing was written.
        """
        if not rule.concepts:
            return None
        key = subject_concept(rule)
        prompt = rule.concepts[key].prompt
        choice = dispute_choice(dispute)
        looks_like = _DISPUTE_LOOKS_LIKE.get(choice, _DEFAULT_LOOKS_LIKE).format(prompt=prompt)
        item = LookalikeItem(
            concept=key,
            lookalike=f"disputed {choice or 'alert'} {candidate.event_id}",
            prompt=f"Add {looks_like} to this camera view, with no real violation of the rule.",
        )
        return self._append(rule.id, item, source="dispute", event_id=candidate.event_id)

    def from_correction(
        self, candidate: CandidateEvent, rule: RuleIR, concept: str, *, x: float, y: float
    ) -> Path | None:
        """Record an owner correction of a falsely segmented object.

        Args:
            candidate: The corrected alert.
            rule: Its rule.
            concept: The clicked track's concept (a key or a SAM prompt).
            x: Click position in image pixels.
            y: Click position in image pixels.

        Returns:
            The regression file, or `None` when nothing was written.
        """
        key = concept_key(rule, concept)
        if key is None:
            _log.warning("hard_negative_unknown_concept", rule_id=rule.id, concept=concept)
            return None
        prompt = rule.concepts[key].prompt
        item = LookalikeItem(
            concept=key,
            lookalike=f"owner-corrected false {key} {candidate.event_id}",
            prompt=(
                f"Add something the camera mistook for a {prompt} near pixel "
                f"({round(x)}, {round(y)}) of this camera view, with no real {prompt} in view."
            ),
        )
        return self._append(rule.id, item, source="correction", event_id=candidate.event_id)

    def _append(
        self, rule_id: str, item: LookalikeItem, *, source: str, event_id: str
    ) -> Path | None:
        try:
            known = {i.lookalike for i in load_regressions(rule_id, self._directory)}
            if item.lookalike in known:
                return None
            path = append_regression(rule_id, item, self._directory)
        except (OSError, ValueError) as exc:
            _log.warning(
                "hard_negative_not_recorded",
                rule_id=rule_id,
                source=source,
                event_id=event_id,
                error=type(exc).__name__,
            )
            return None
        _log.info("hard_negative_recorded", rule_id=rule_id, source=source, event_id=event_id)
        return path
