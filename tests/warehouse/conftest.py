"""Fixtures: the recorded T02 / T04 golden chains and plans (real live records, 2 Nov 2026)."""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from rulecam.contracts import EventRecord, SitePlan
from rulecam.evidence.chain import read_chain

SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios"
DAY = date(2026, 11, 2)
T02 = "worker_passes_4m"
T04 = "driver_only_4m"
T02_PLAN = "plan-10517db0b9f6"
T04_PLAN = "plan-01307279465a"
T02_EVENTS = ("ev-41e7fc0f19271cf9a782a31c", "ev-49940b44a5ce03bffe26313f")


def golden_records(name: str) -> list[EventRecord]:
    """The scenario's recorded chain."""
    return read_chain(SCENARIOS / name / "chain.jsonl")


def golden_plan(name: str) -> SitePlan:
    """The scenario's recorded plan."""
    return SitePlan.model_validate(
        json.loads((SCENARIOS / name / "plan.json").read_text(encoding="utf-8"))
    )


@pytest.fixture
def t02_records() -> list[EventRecord]:
    """T02: two near misses, both confirmed by Glimmer (`violation`), two alerts."""
    return golden_records(T02)


@pytest.fixture
def t04_records() -> list[EventRecord]:
    """T04: three candidates on the seated driver, all rejected by Glimmer."""
    return golden_records(T04)


@pytest.fixture
def chain_dir(tmp_path: Path) -> Path:
    """A site chain folder holding T02's day."""
    folder = tmp_path / "chain" / "demo"
    folder.mkdir(parents=True)
    shutil.copyfile(SCENARIOS / T02 / "chain.jsonl", folder / f"{DAY.isoformat()}.jsonl")
    return folder


@pytest.fixture
def strips_root(tmp_path: Path) -> Path:
    """T02's evidence strips under `<root>/<event_id>/`."""
    root = tmp_path / "strips"
    shutil.copytree(SCENARIOS / T02 / "strips", root)
    return root
