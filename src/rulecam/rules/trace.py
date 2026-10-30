"""Formatting of values written into `CandidateEvent.predicate_trace`."""

from __future__ import annotations

import math

DECIMALS = 6


def trace_value(value: float) -> float | str:
    """A JSON-safe trace value: rounded floats, "inf"/"-inf", or "unknown" for NaN."""
    if math.isnan(value):
        return "unknown"
    if math.isinf(value):
        return "inf" if value > 0 else "-inf"
    return round(value, DECIMALS)
