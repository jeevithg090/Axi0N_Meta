# `rulecam.stats`

Small, pure statistics shared across the planner and evaluation.

- `wilson_interval(successes, total, confidence=0.95) -> (lower, upper)`: the Wilson score
  interval for a binomial proportion. `(0.0, 1.0)` for zero trials; the lower bound is exactly 0
  with no successes and the upper bound exactly 1 when every trial succeeds. Used by the planner's
  detectability probes (P2-04) and reused by evaluation (P4-06) and rule health (P4-09).

Tests: `uv run pytest tests/stats`
