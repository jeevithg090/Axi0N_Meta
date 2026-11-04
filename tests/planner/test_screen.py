"""The Llama Guard 4 rule screen in the planner (fakes, no network)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rulecam.clients.open_weights.llama_guard import GuardVerdict
from rulecam.clients.transport import ModelTransportError
from rulecam.config import Settings
from rulecam.planner.guardrails import GuardrailCategory
from rulecam.planner.pipeline import PlanStore
from rulecam.planner.screen import (
    ScreenResult,
    rule_screen_from_settings,
    screen_decline,
    screen_request,
)
from rulecam.replay.cache import ReplayCache
from tests.fakes import FakeClock
from tests.planner.test_pipeline import _inputs, _pipeline, _spark


class Guard:
    """Refuses (S10) or allows every request, and keeps what it was asked."""

    def __init__(self, *, refuse: bool = False, fail: bool = False) -> None:
        self.refuse = refuse
        self.fail = fail
        self.texts: list[str] = []

    def screen_rule_text(self, text: str) -> GuardVerdict:
        self.texts.append(text)
        if self.fail:
            raise ModelTransportError("down")
        if self.refuse:
            return GuardVerdict(
                safe=False,
                categories=("S10",),
                refused=True,
                reason="Llama Guard 4 flagged this request (S10 Hate): who they are.",
                latency_ms=420,
                model="llama-guard-4-12b",
            )
        return GuardVerdict(True, (), False, "", 380, "llama-guard-4-12b")


def test_a_refused_request_is_declined_without_surveying(tmp_path: Path) -> None:
    guard = Guard(refuse=True)
    pipeline = _pipeline(tmp_path, _spark(), screen=guard)
    plan = pipeline.run(_inputs(tmp_path))
    assert plan.rules == ()
    assert {f.verdict for f in plan.feasibility} == {"declined"}
    assert all("S10 Hate" in (f.reason or "") for f in plan.feasibility)
    assert plan.inventory == ()
    saved = pipeline.store.load_screen("main-st")
    assert saved is not None
    assert saved.refused
    assert saved.summary().startswith("refused by llama-guard-4-12b (S10)")
    assert guard.texts  # the request and its concerns were screened


def test_an_allowed_request_plans_as_before(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path, _spark(), screen=Guard())
    plan = pipeline.run(_inputs(tmp_path))
    assert [r.id for r in plan.rules] == ["wash-before-line"]
    saved = PlanStore(tmp_path / "plans").load_screen("main-st")
    assert saved is not None
    assert saved.summary() == "llama-guard-4-12b: safe (0.4 s)"


def test_without_a_server_or_when_it_fails_the_screen_is_skipped_and_said() -> None:
    skipped = screen_request(None, "keep people 4 m from forklifts")
    assert (skipped.screened, skipped.note) == (False, "LLAMA_GUARD_BASE_URL is not set")
    failed = screen_request(Guard(fail=True), "keep people 4 m from forklifts")
    assert failed.summary() == "not screened: Llama Guard 4 did not answer (ModelTransportError)"
    assert (
        rule_screen_from_settings(
            Settings(llama_guard_base_url=None),
            cache=ReplayCache.from_settings(Settings(rulecam_mode="replay"), clock=FakeClock()),
        )
        is None
    )


@pytest.mark.parametrize(
    ("codes", "category"),
    [(("S7",), GuardrailCategory.IDENTITY), (("S10",), GuardrailCategory.PROTECTED_ATTRIBUTE)],
)
def test_the_decline_names_the_category(codes: tuple[str, ...], category: str) -> None:
    result = ScreenResult(screened=True, refused=True, categories=codes, reason="no")
    assert screen_decline(result).category == category
