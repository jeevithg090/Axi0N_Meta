"""Fixtures for Voice Out tests."""

from __future__ import annotations

import pytest

from tests.voice_out.helpers import RecordingBus, StepTimer


@pytest.fixture
def bus() -> RecordingBus:
    return RecordingBus()


@pytest.fixture
def timer() -> StepTimer:
    return StepTimer()
