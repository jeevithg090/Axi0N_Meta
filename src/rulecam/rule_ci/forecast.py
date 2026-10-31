"""Alert forecast per shift from backtests over recorded tracks (task P2-09, MP §3.4).

The rate comes from `rules.backtest` (P1-07), which re-runs the pure engine on stored tracks of
one camera over a window. Per camera, the rate is its candidates over its hours observed (all of
its backtests pooled); the rule's rate is the sum over its cameras. Alerts per shift are that
rate times the owner's shift length, compared with the owner's alert budget.

Counts are per rule (and camera), never per person or per track. A camera whose backtest was
`no` (the rule's concepts are not in its stored tracks) has no meaningful rate, so the forecast
is then unavailable instead of claiming zero.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Final

from pydantic import Field

from rulecam.contracts import Frozen, RuleIR
from rulecam.rules.backtest import Backtestable, BacktestReport

_SECONDS_PER_HOUR: Final = 3600.0
_WORST: Final[dict[Backtestable, int]] = {"yes": 0, "partial": 1, "no": 2}


class AlertBudget(Frozen):
    """The owner's alert budget.

    Attributes:
        alerts_per_shift: Most alerts per shift the owner will accept for a rule.
        shift_hours: Length of a shift.
    """

    alerts_per_shift: float = Field(ge=0.0)
    shift_hours: float = Field(gt=0.0, le=24.0)


class AlertForecast(Frozen):
    """Forecast alerts per shift for one rule, against the budget.

    Attributes:
        rule_id: The rule.
        cameras: Cameras backtested.
        hours: Hours of recorded tracks backtested (summed over cameras).
        candidates: Candidates the backtests produced.
        backtestable: The worst backtestability over the cameras.
        per_shift: Forecast alerts per shift; `None` when a camera was not backtestable.
        budget: The owner's budget.
    """

    rule_id: str
    cameras: tuple[str, ...]
    hours: float
    candidates: int
    backtestable: Backtestable
    per_shift: float | None
    budget: AlertBudget

    @property
    def within_budget(self) -> bool | None:
        """Whether the forecast fits the budget (`None` when there is no forecast)."""
        return None if self.per_shift is None else self.per_shift <= self.budget.alerts_per_shift

    def summary(self) -> str:
        """E.g. `forecast 3.0 alerts/shift (budget 5)`."""
        budget = f"budget {self.budget.alerts_per_shift:g}"
        if self.per_shift is None:
            return f"forecast unavailable: concepts missing from stored tracks ({budget})"
        over = "" if self.within_budget else ", over budget"
        return f"forecast {self.per_shift:.1f} alerts/shift ({budget}{over})"


def forecast_alerts(
    rule: RuleIR, backtests: Sequence[BacktestReport], budget: AlertBudget
) -> AlertForecast:
    """Forecast alerts per shift for a rule from its backtests.

    Args:
        rule: The rule.
        backtests: Backtests of this rule (one or more per camera).
        budget: The owner's budget.

    Returns:
        The forecast.

    Raises:
        ValueError: With no backtests, a backtest of another rule, or an empty window.
    """
    if not backtests:
        msg = f"no backtests to forecast {rule.id!r} from"
        raise ValueError(msg)
    others = sorted({b.rule_id for b in backtests if b.rule_id != rule.id})
    if others:
        msg = f"backtests of other rules {others} given for {rule.id!r}"
        raise ValueError(msg)
    if any(b.t1 <= b.t0 for b in backtests):
        msg = "every backtest window must have a positive duration"
        raise ValueError(msg)
    counts: dict[str, int] = defaultdict(int)
    hours: dict[str, float] = defaultdict(float)
    for report in backtests:
        counts[report.camera_id] += report.count
        hours[report.camera_id] += (report.t1 - report.t0).total_seconds() / _SECONDS_PER_HOUR
    worst = max((b.backtestable for b in backtests), key=_WORST.__getitem__)
    per_hour = sum(counts[camera] / hours[camera] for camera in counts)
    return AlertForecast(
        rule_id=rule.id,
        cameras=tuple(sorted(counts)),
        hours=sum(hours.values()),
        candidates=sum(counts.values()),
        backtestable=worst,
        per_shift=None if worst == "no" else per_hour * budget.shift_hours,
        budget=budget,
    )
