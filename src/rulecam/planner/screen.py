"""The plan-time rule screen: Llama Guard 4 reads the request before anything is planned.

Right after the request is understood, the owner's words (and the concerns read from them, so a
spoken request is screened too) go to Llama Guard 4 (`clients.open_weights.llama_guard`). When
it flags an identity category (S7 Privacy, S10 Hate), every concern of the request is declined
with the model's reason, the survey and probes are skipped, and no rule is compiled. The screen
runs only when `LLAMA_GUARD_BASE_URL` is set; otherwise it is skipped and says so. A screen that
cannot answer (server down) is recorded as not screened and the plan goes on: the planner's own
guardrails (`planner.guardrails`) still run on every concern.

The result is saved next to the run's steps (`<plans>/<plan_id>/screen.json`) and shown on the
planning timeline as its own step.
"""

from __future__ import annotations

from typing import Protocol

from rulecam.bus import Bus
from rulecam.clients.errors import ClientConfigError, ModelClientError
from rulecam.clients.open_weights.llama_guard import GuardVerdict, LlamaGuardClient
from rulecam.config import Settings
from rulecam.contracts import Frozen
from rulecam.log import get_logger
from rulecam.planner.guardrails import Declined, GuardrailCategory
from rulecam.planner.models import UnderstandResult
from rulecam.replay.cache import ReplayCache

_log = get_logger(__name__)

PRIVACY = "S7"


class RuleScreen(Protocol):
    """Screens one rule request (`LlamaGuardClient.screen_rule_text`)."""

    def screen_rule_text(self, text: str) -> GuardVerdict:
        """Return the model's verdict on the request."""
        ...


class ScreenResult(Frozen):
    """What the screen decided for one request.

    Attributes:
        screened: Whether the model answered.
        refused: Whether it flagged an identity category: the request is declined.
        categories: The categories it flagged.
        reason: Its reason, for the owner (when refused).
        latency_ms: How long it took.
        model: The model that answered.
        note: Why it did not screen (not configured, unreachable), else empty.
    """

    screened: bool
    refused: bool = False
    categories: tuple[str, ...] = ()
    reason: str = ""
    latency_ms: int = 0
    model: str = ""
    note: str = ""

    def summary(self) -> str:
        """One line for the timeline."""
        if not self.screened:
            return f"not screened: {self.note}"
        if self.refused:
            return f"refused by {self.model} ({', '.join(self.categories)}): {self.reason}"
        return f"{self.model}: safe ({self.latency_ms / 1000:.1f} s)"


def request_text(texts: list[str], understood: UnderstandResult) -> str:
    """The words to screen: the typed text and the concerns read from the request."""
    parts = [*texts, *(concern.text for concern in understood.concerns)]
    return "\n".join(dict.fromkeys(p.strip() for p in parts if p.strip()))


def screen_request(screen: RuleScreen | None, text: str) -> ScreenResult:
    """Screen `text`, or say why it was not screened."""
    if screen is None:
        return ScreenResult(screened=False, note="LLAMA_GUARD_BASE_URL is not set")
    try:
        verdict = screen.screen_rule_text(text)
    except (ModelClientError, OSError) as exc:
        _log.warning("rule_screen_failed", error=type(exc).__name__)
        return ScreenResult(
            screened=False, note=f"Llama Guard 4 did not answer ({type(exc).__name__})"
        )
    if verdict.refused:
        _log.info("rule_screen_refused", categories=list(verdict.categories))
    return ScreenResult(
        screened=True,
        refused=verdict.refused,
        categories=verdict.categories,
        reason=verdict.reason,
        latency_ms=verdict.latency_ms,
        model=verdict.model,
    )


def screen_decline(result: ScreenResult) -> Declined:
    """The guardrail decline a refused screen gives every concern of the request."""
    category = (
        GuardrailCategory.IDENTITY
        if PRIVACY in result.categories
        else GuardrailCategory.PROTECTED_ATTRIBUTE
    )
    return Declined(
        category=category,
        reason=result.reason,
        alternative="Ask about what people do (where, when, how close), never who they are.",
    )


def rule_screen_from_settings(
    settings: Settings, *, cache: ReplayCache, bus: Bus | None = None
) -> RuleScreen | None:
    """Llama Guard 4 when `LLAMA_GUARD_BASE_URL` is set, else `None` (logged)."""
    try:
        return LlamaGuardClient.from_settings(settings, cache=cache, bus=bus)
    except ClientConfigError as exc:
        _log.info("rule_screen_skipped", reason=str(exc))
        return None
