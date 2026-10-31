"""Rule CI part 2 for one rule: scenes, lookalikes, injection signs and forecast (task P2-09).

`RedTeam` runs the still-image tests on the rule's real camera frame (detectability scenes, then
lookalikes, then injection signs on the positive scenes SAM confirmed) and the alert forecast,
and returns a `RedTeamReport`. Rule CI (`run_rule_ci`) attaches it to the rule's report, so it
reaches `ci.result` and the acceptance trace:

- a fooled lookalike or a silenced alert fails `ci_green` ("no violation may result"; "the
  injection sign does not silence the alert");
- the detectability pass rate and the forecast are reported, not gated (detectability is gated
  by `detect_lb>=0.8`, and the budget is the owner's call);
- a stage that could not run (no camera frame, a model client or replay-cache error) is listed
  under `skipped` with the reason; it never counts as passed.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from rulecam.contracts import Clock, Frozen, RuleIR, StructuredLLM, Verifier
from rulecam.errors import RulecamError
from rulecam.evidence.chain import EventChain
from rulecam.log import get_logger
from rulecam.planner.steps.survey import ImageSegmenter
from rulecam.rule_ci.forecast import AlertBudget, AlertForecast, forecast_alerts
from rulecam.rule_ci.injection import Decider, InjectionReport, run_injection_red_team
from rulecam.rule_ci.lookalikes import REGRESSIONS_DIR, LookalikeReport, run_lookalike_red_team
from rulecam.rule_ci.scenes import DetectabilityReport, run_detectability_scenes
from rulecam.rule_ci.stills import ImageRenderer
from rulecam.rules.backtest import BacktestReport

_log = get_logger(__name__)

FrameFor = Callable[[RuleIR], tuple[str, Path] | None]
"""The rule's camera and its latest real frame, or `None` when there is none."""
BacktestsFor = Callable[[RuleIR], Sequence[BacktestReport]]
"""Backtests of the rule over recorded tracks (`rules.backtest`)."""


class RedTeamReport(Frozen):
    """Part 2 of a rule's Rule CI report.

    Attributes:
        scenes: Detectability scenes, if they ran.
        lookalikes: The lookalike red-team, if it ran.
        injection: Injection signs, if they ran.
        forecast: The alert forecast, if it ran.
        skipped: Stages that did not run, each with why.
    """

    scenes: DetectabilityReport | None = None
    lookalikes: LookalikeReport | None = None
    injection: InjectionReport | None = None
    forecast: AlertForecast | None = None
    skipped: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        """No lookalike fooled the rule and no sign silenced its alert."""
        return not self.problems()

    def problems(self) -> list[str]:
        """Fooled lookalikes (with their fix) and silenced alerts, readable."""
        problems = []
        for finding in self.lookalikes.fooled if self.lookalikes else ():
            fix = finding.fix
            fixed_by = f"; proposed fix: {fix.kind} {fix.text!r} on {fix.concept}" if fix else ""
            problems.append(f"lookalike {finding.lookalike!r} fooled {finding.concept}{fixed_by}")
        problems += [
            f"injection sign {r.variant!r} silenced the alert"
            for r in (self.injection.silenced if self.injection else ())
        ]
        return problems

    def summary(self) -> str:
        """E.g. `18 lookalikes, fooled 1 (backpack strap) → fix proposed; injection 6/6 ...`."""
        parts = [
            part.summary()
            for part in (self.scenes, self.lookalikes, self.injection, self.forecast)
            if part is not None
        ]
        if self.skipped:
            parts.append(f"not run: {', '.join(s.split(':', 1)[0] for s in self.skipped)}")
        return "; ".join(parts)


class RedTeam:
    """Runs Rule CI part 2 for a rule (a `RuleIR -> RedTeamReport` callable)."""

    def __init__(
        self,
        *,
        spark: StructuredLLM,
        renderer: ImageRenderer,
        segmenter: ImageSegmenter,
        tier1: Verifier,
        policy: Decider,
        chain: EventChain,
        clock: Clock,
        frame_for: FrameFor,
        out_dir: Path,
        regressions_dir: Path = REGRESSIONS_DIR,
        backtests_for: BacktestsFor | None = None,
        budget: AlertBudget | None = None,
    ) -> None:
        """Create the red team.

        Args:
            spark: Muse Spark (scene matrices, lookalike lists).
            renderer: Muse Image.
            segmenter: Hosted SAM 3.1 on single images.
            tier1: The Tier 1 verifier (Glimmer).
            policy: The runtime policy with its scene-text firewall.
            chain: The chain the firewall appends `tamper_attempt` records to.
            clock: The chain's clock.
            frame_for: The rule's camera and real frame.
            out_dir: Where injection composites are saved.
            regressions_dir: The permanent lookalike regressions.
            backtests_for: Backtests for the forecast; without it (or `budget`) there is none.
            budget: The owner's alert budget.
        """
        self._spark = spark
        self._renderer = renderer
        self._segmenter = segmenter
        self._tier1 = tier1
        self._policy = policy
        self._chain = chain
        self._clock = clock
        self._frame_for = frame_for
        self._out_dir = out_dir
        self._regressions_dir = regressions_dir
        self._backtests_for = backtests_for
        self._budget = budget

    def __call__(self, rule: RuleIR) -> RedTeamReport:
        """Run every stage that can run for the rule.

        Args:
            rule: The rule.

        Returns:
            The report.
        """
        skipped: list[str] = []
        forecast = self._forecast(rule, skipped)
        located = self._frame_for(rule)
        if located is None:
            skipped.append("scenes, lookalikes, injection: no camera frame for the rule")
            return RedTeamReport(forecast=forecast, skipped=tuple(skipped))
        camera_id, frame = located
        scenes = _stage("scenes", skipped, lambda: self._scenes(rule, camera_id, frame))
        lookalikes = _stage("lookalikes", skipped, lambda: self._lookalikes(rule, camera_id, frame))
        injection = None
        positives = scenes.positives() if scenes is not None else []
        if positives:
            injection = _stage(
                "injection", skipped, lambda: self._injection(rule, camera_id, positives)
            )
        else:
            skipped.append("injection: no detectable positive scene to put the signs on")
        report = RedTeamReport(
            scenes=scenes,
            lookalikes=lookalikes,
            injection=injection,
            forecast=forecast,
            skipped=tuple(skipped),
        )
        _log.info("rule_ci_red_team", rule_id=rule.id, summary=report.summary())
        return report

    def _scenes(self, rule: RuleIR, camera_id: str, frame: Path) -> DetectabilityReport:
        return run_detectability_scenes(
            rule,
            frame,
            spark=self._spark,
            renderer=self._renderer,
            segmenter=self._segmenter,
            camera_id=camera_id,
        )

    def _lookalikes(self, rule: RuleIR, camera_id: str, frame: Path) -> LookalikeReport:
        return run_lookalike_red_team(
            rule,
            frame,
            spark=self._spark,
            renderer=self._renderer,
            segmenter=self._segmenter,
            tier1=self._tier1,
            camera_id=camera_id,
            ts=self._clock.now(),
            regressions_dir=self._regressions_dir,
        )

    def _injection(self, rule: RuleIR, camera_id: str, scenes: list[Path]) -> InjectionReport:
        return run_injection_red_team(
            rule,
            scenes,
            tier1=self._tier1,
            policy=self._policy,
            chain=self._chain,
            clock=self._clock,
            camera_id=camera_id,
            out_dir=self._out_dir,
        )

    def _forecast(self, rule: RuleIR, skipped: list[str]) -> AlertForecast | None:
        if self._backtests_for is None or self._budget is None:
            return None
        backtests_for, budget = self._backtests_for, self._budget
        backtests = _stage("forecast", skipped, lambda: list(backtests_for(rule)))
        if backtests is None:
            return None
        if not backtests:
            skipped.append("forecast: no recorded tracks to backtest")
            return None
        return forecast_alerts(rule, backtests, budget)


def _stage[T](name: str, skipped: list[str], run: Callable[[], T]) -> T | None:
    """Run a stage; a model, replay or file error skips it (recorded with why, and logged)."""
    try:
        return run()
    except (RulecamError, OSError) as exc:
        _log.warning("rule_ci_stage_skipped", stage=name, error=f"{type(exc).__name__}: {exc}")
        skipped.append(f"{name}: {type(exc).__name__}: {exc}")
        return None
