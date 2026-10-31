"""Replay layer: the model-call cache, golden runs and the pre-take preflight (task P3-02)."""

from rulecam.replay.cache import (
    CacheEntry,
    ModeChanged,
    ReplayCache,
    badge_for,
    cache_key,
    cached_call,
    canonical_json,
    configure,
    get_cache,
)
from rulecam.replay.errors import ReplayCorruptError, ReplayError, ReplayMiss, ScenarioError

__all__ = [
    "CacheEntry",
    "ModeChanged",
    "ReplayCache",
    "ReplayCorruptError",
    "ReplayError",
    "ReplayMiss",
    "ScenarioError",
    "badge_for",
    "cache_key",
    "cached_call",
    "canonical_json",
    "configure",
    "get_cache",
]
