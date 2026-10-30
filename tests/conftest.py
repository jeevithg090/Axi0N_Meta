"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fakes import FakeClock

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES
