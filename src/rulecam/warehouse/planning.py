"""How long each planner step took, read from the plan's saved run (no extra bookkeeping).

The planner saves its input when a run starts and each step's output the moment the step ends
(`planner.pipeline.PlanStore`), so the files' modification times are the step boundaries: a
step took from the previous file's time to its own. The first step without a file is the one
running now. The rule screen (Llama Guard 4, `planner.screen`) shows as its own step after
understand, with its verdict; the survey says when it reused an earlier survey of the scene.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from rulecam.contracts import Frozen
from rulecam.planner.errors import PipelineError
from rulecam.planner.pipeline import PIPELINE_STEPS, PlanStore
from rulecam.planner.screen import ScreenResult

StepState = Literal["done", "running", "pending"]


class PlanStepTiming(Frozen):
    """One planner step's timing.

    Attributes:
        step: The step (`understand`, `screen`, `survey`, ..., `prove`).
        status: `done`, `running` (the first unfinished step) or `pending`.
        seconds: How long it took, or has been running; `None` while pending.
        detail: What it reused, when it reused something.
    """

    step: str
    status: StepState
    seconds: float | None = None
    detail: str = ""


class Planning(Frozen):
    """A plan's run, step by step.

    Attributes:
        plan_id: The plan.
        started_at: When the run started (its input was saved).
        total_s: Request to plan, once every step is done.
        steps: Every step in order.
    """

    plan_id: str
    started_at: datetime
    total_s: float | None
    steps: tuple[PlanStepTiming, ...]


def _mtime(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except OSError:
        return None


def _survey_detail(path: Path) -> str:
    try:
        reused = json.loads(path.read_text(encoding="utf-8")).get("reused") or {}
    except (OSError, ValueError):
        return ""
    return "; ".join(
        f"reused {camera}'s survey from {r.get('plan_id')} ({float(r.get('age_s', 0)):.0f} s "
        "old, same scene)"
        for camera, r in sorted(reused.items())
        if isinstance(r, dict)
    )


def _step_files(store: PlanStore, plan_id: str) -> list[tuple[str, Path]]:
    """Each step and the file it writes when it ends; the rule screen runs after understand."""
    files: list[tuple[str, Path]] = [
        (name, store.step_path(plan_id, name)) for name in PIPELINE_STEPS
    ]
    screen = store.screen_path(plan_id)
    if screen.is_file() or not files[1][1].is_file():  # a run from before the screen has none
        files.insert(1, ("screen", screen))
    return files


def _detail(name: str, path: Path) -> str:
    if name == "survey":
        return _survey_detail(path)
    if name == "screen":
        try:
            return ScreenResult.model_validate_json(path.read_text(encoding="utf-8")).summary()
        except (OSError, ValueError):
            return ""
    return ""


def planning_timeline(store: PlanStore, plan_id: str, now: datetime) -> Planning | None:
    """The plan's step timings from its saved run, or `None` when it has no saved run.

    Args:
        store: The planner's store.
        plan_id: The plan.
        now: The current time (for the running step's elapsed seconds).
    """
    try:
        started = _mtime(store.input_path(plan_id))
    except PipelineError:
        return None
    if started is None:
        return None
    previous = started
    steps: list[PlanStepTiming] = []
    running = False
    for name, path in _step_files(store, plan_id):
        ended = _mtime(path)
        if ended is not None and not running:
            detail = _detail(name, path)
            seconds = round(max(0.0, (ended - previous).total_seconds()), 1)
            steps.append(PlanStepTiming(step=name, status="done", seconds=seconds, detail=detail))
            previous = ended
        elif not running:
            running = True
            seconds = round(max(0.0, (now - previous).total_seconds()), 1)
            steps.append(PlanStepTiming(step=name, status="running", seconds=seconds))
        else:
            steps.append(PlanStepTiming(step=name, status="pending"))
    total = None if running else round((previous - started).total_seconds(), 1)
    return Planning(plan_id=plan_id, started_at=started, total_s=total, steps=tuple(steps))
