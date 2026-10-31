"""Sample `/runtime/metrics` on the live deployment for the scorecard's real-time rows."""

from __future__ import annotations

from rulecam.eval.client import ApiClient
from rulecam.eval.runner import Clock
from rulecam.eval.scorecard import LiveSample


def sample_live(
    api: ApiClient, *, samples: int, interval_s: float, source: str, clock: Clock | None = None
) -> LiveSample:
    """Read `/runtime/metrics` `samples` times, `interval_s` apart.

    Returns:
        Every `lag_s` reading and whether any reading was in keyframe mode.
    """
    clock = clock or Clock()
    lags, keyframe = [], False
    for i in range(samples):
        if i:
            clock.sleep(interval_s)
        raw = api.runtime_metrics()
        lag = raw.get("lag_s")
        if isinstance(lag, int | float):
            lags.append(float(lag))
        keyframe = keyframe or bool(raw.get("keyframe_mode"))
    return LiveSample(lags_s=tuple(lags), keyframe_mode=keyframe, source=source)
