"""Hypothesis model for Site Advisor causal tests."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Hypothesis:
    """A catalogue cause and the metric test that decides it."""

    id: str
    statement: str
    metric: str
    threshold: float
    unit: str
    keywords: tuple[str, ...]
