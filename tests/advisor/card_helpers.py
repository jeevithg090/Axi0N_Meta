"""Builders for Site Advisor card tests (P3-10): a dock-3 card, its numbers and a process-log DB."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypeVar

import duckdb
from pydantic import BaseModel

from rulecam.advisor.recompute import NumberToCheck
from rulecam.contracts import (
    Gain,
    MeasurementPlan,
    Provenance,
    Recommendation,
    SafetyCheck,
    SlotClaim,
)

M = TypeVar("M", bound=BaseModel)
T0 = datetime(2026, 10, 31, 13, 0, tzinfo=UTC)
T1 = datetime(2026, 10, 31, 17, 0, tzinfo=UTC)
WAIT_SQL = "SELECT avg(wait_s) / 60 FROM cases WHERE station = 'dock_3'"
TURN_SQL = "SELECT avg(turnaround_s) / 60 FROM cases WHERE station = 'dock_1'"


def make_db(path: Path) -> Path:
    """A tiny process log: dock_3 waits 12 and 16 min, dock_1 turns around in 31 min."""
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE cases(case_id VARCHAR, station VARCHAR, truck_object_id VARCHAR, "
        "arrived_ts TIMESTAMP, departed_ts TIMESTAMP, wait_s DOUBLE, handling_s DOUBLE, "
        "post_dwell_s DOUBLE, turnaround_s DOUBLE, complete BOOLEAN)"
    )
    rows = [
        ("c1", "dock_3", "t1", 720.0, 1800.0, 600.0, 3120.0),
        ("c2", "dock_3", "t2", 960.0, 1500.0, 540.0, 3000.0),
        ("c3", "dock_1", "t3", 120.0, 1500.0, 240.0, 1860.0),
    ]
    for case_id, station, truck, wait, handling, dwell, turn in rows:
        con.execute(
            "INSERT INTO cases VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, true)",
            [case_id, station, truck, T0, T1, wait, handling, dwell, turn],
        )
    con.execute("CREATE TABLE tracks(track_id VARCHAR)")  # never an allowed table
    con.close()
    return path


def numbers() -> list[NumberToCheck]:
    return [
        NumberToCheck(
            result_id="mr_wait",
            metric="dock_wait_for_forklift_min",
            definition="Mean minutes a truck waits at the dock before a forklift arrives.",
            station="dock_3",
            t0=T0,
            t1=T1,
            value=14.0,
            unit="min",
        ),
        NumberToCheck(
            result_id="mr_turn",
            metric="dock_turnaround_min",
            definition="Mean minutes from truck arrival to departure.",
            station="dock_1",
            t0=T0,
            t1=T1,
            value=31.0,
            unit="min",
        ),
    ]


def make_card(status: Literal["proposed", "accepted", "measuring"] = "proposed") -> Recommendation:
    return Recommendation(
        id="rec-0007",
        status=status,
        scope_kind="station",
        scope_ids=("dock_3",),
        klass="test",
        provenance=Provenance.RECORDED,
        saw=(
            SlotClaim(
                template="Trucks wait {m1} for a forklift at dock 3 vs {m2} turnaround at dock 1",
                slots={"m1": "mr_wait", "m2": "mr_turn"},
                grade="B",
            ),
        ),
        why=(),
        do=(),
        who_role="dock_lead",
        revert="move replenishment back to 13:00",
        expect=Gain(
            metric="dock3_wait_for_forklift_min",
            interval=(-14.0, -4.0),
            level=0.8,
            unit="min/truck",
            model_rung="erlang_c",
            assumptions={"poisson_arrivals": "pass"},
        ),
        safety=SafetyCheck(
            rules_checked=("ped-forklift-2m",),
            verdict="pass",
            considered_not_recommended=(("add a second forklift", "raises ped-forklift-2m"),),
        ),
        rank_score=0.8,
        numbers_total=0,
        numbers_reproduced=0,
    )


def make_plan(recommendation_id: str = "rec-0007") -> MeasurementPlan:
    return MeasurementPlan(
        id="mp-0007",
        recommendation_id=recommendation_id,
        primary_metric="dock3_wait_for_forklift_min",
        guard_metrics=("near_misses_per_forklift_hour",),
        design="switchback",
        block_minutes=20,
        n_pairs=6,
        min_detectable_effect=4.0,
        smallest_possible_p=0.03125,
        decision_rule="worked if the 80% CI excludes 0 and the effect is negative",
        preregistration_hash="",
    )


class FakeSqlWriter:
    """Returns queued SQL per metric (Glimmer's independent query)."""

    def __init__(self, sql_by_metric: Mapping[str, str]) -> None:
        self.sql_by_metric = dict(sql_by_metric)
        self.prompts: list[str] = []

    def structured(
        self,
        *,
        model: str,
        messages: list[dict[str, object]],
        schema: type[M],
        reasoning_effort: str = "low",
        background: bool = False,
    ) -> M:
        text = str(messages[-1]["content"])
        self.prompts.append(text)
        metric = text.split("\n", 1)[0].removeprefix("Metric: ")
        if metric not in self.sql_by_metric:
            msg = f"no SQL queued for {metric}"
            raise ValueError(msg)
        return schema.model_validate({"sql": self.sql_by_metric[metric]})
