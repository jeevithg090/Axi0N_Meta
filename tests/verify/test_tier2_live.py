"""Live Spark Open check (P3-04 acceptance): run by hand with `make live-test` and Baseten keys."""

from __future__ import annotations

from pathlib import Path

import pytest

from rulecam.clients.open_weights.spark_open import SparkOpenClient
from rulecam.clock import SystemClock
from rulecam.config import Settings
from rulecam.replay.cache import ReplayCache
from rulecam.verify.tier2 import DevilAnswer


@pytest.mark.live
def test_live_call_returns_reasoning(tmp_path: Path) -> None:
    settings = Settings()
    if settings.baseten_api_key is None:
        pytest.skip("BASETEN_API_KEY not set")
    cache = ReplayCache(tmp_path, "live", clock=SystemClock())
    client = SparkOpenClient.from_settings(settings, cache=cache, clock=SystemClock())
    messages: list[dict[str, object]] = [
        {"role": "user", "content": "Dock 3 is slower than dock 1 after lunch. Two alternatives?"}
    ]
    reply = client.reason(messages, DevilAnswer, effort="low")
    assert reply.reasoning
    assert reply.answer.alternatives
