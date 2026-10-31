"""Alert rate forecast from track backtest against owner budget (task P2-09, MP §3.4).

Computes expected alerts per shift from backtesting over recorded tracks (P1-07),
comparing the forecasted rate with the owner's configured alert budget.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from rulecam.contracts.rule_ir import RuleIR
from rulecam.rules.backtest import BacktestReport

DEFAULT_SHIFT_HOURS: Final = 8.0
DEFAULT_ALERT_BUDGET_PER_SHIFT: Final = 5.0


class AlertForecastReport(BaseModel):
    """Forecast of alert frequency per shift compared with budget."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    total_window_hours: float = Field(ge=0.0)
    total_events_observed: int = Field(ge=0)
    alerts_per_hour: float = Field(ge=0.0)
    alerts_per_shift: float = Field(ge=0.0)
    alert_budget_per_shift: float = Field(ge=0.0)
    within_budget: bool
    shift_hours: float = DEFAULT_SHIFT_HOURS
    recommendation: str


def compute_alert_forecast(
    rule: RuleIR,
    backtest: BacktestReport,
    *,
    alert_budget_per_shift: float = DEFAULT_ALERT_BUDGET_PER_SHIFT,
    shift_hours: float = DEFAULT_SHIFT_HOURS,
) -> AlertForecastReport:
    """Compute forecasted alerts per shift from backtest results."""
    # Window duration in hours
    duration_s = max(1.0, (backtest.t1 - backtest.t0).total_seconds())
    window_hours = duration_s / 3600.0

    alerts_per_hour = backtest.count / window_hours if window_hours > 0 else 0.0
    alerts_per_shift = round(alerts_per_hour * shift_hours, 2)
    within_budget = alerts_per_shift <= alert_budget_per_shift

    if within_budget:
        rec = (
            f"Forecast ({alerts_per_shift:.1f}/shift) is within "
            f"owner budget ({alert_budget_per_shift:.1f}/shift)."
        )
    else:
        excess = alerts_per_shift - alert_budget_per_shift
        rec = (
            f"Forecast ({alerts_per_shift:.1f}/shift) exceeds budget by {excess:.1f}/shift. "
            "Consider tightening dwell thresholds or adjusting zone boundaries."
        )

    return AlertForecastReport(
        rule_id=rule.id,
        total_window_hours=round(window_hours, 2),
        total_events_observed=backtest.count,
        alerts_per_hour=round(alerts_per_hour, 3),
        alerts_per_shift=alerts_per_shift,
        alert_budget_per_shift=alert_budget_per_shift,
        within_budget=within_budget,
        shift_hours=shift_hours,
        recommendation=rec,
    )
