"""Runner E7: what does it cost and how fast is it? (task P4-07, MP §8 E7).

E7 reads the runtime's own metrics (`LoopMetrics`) rather than measuring anything itself, so the
figures are the ones the system reported while it ran.

* **SAM calls per camera-hour, gated against ungated.** The motion gate and the static cache
  exist to avoid segmenting a still scene. "Ungated" is what the same run would have cost with
  the gate off, which is every chunk processed: a counterfactual from the skip counters, not a
  second run. It is an upper bound on the saving, because a run with the gate off would have
  had different timing and some of those chunks would have been skipped for other reasons.
* **Dollars per camera-hour**, from the billed frames at hosted SAM pricing plus the GPU pods'
  hourly cost divided over the cameras they serve (`rulecam.eval.metrics.usd_per_camera_hour`).
* **Alert latency per stage**, from each stage's p50 and p95 as the runtime recorded them.

Latency only means anything if the run was fed at real time. A run played faster than real time
reports timings that no camera would produce, so `camera_hours` must come from a real-time run
and `realtime=False` marks the result simulated.
"""

from __future__ import annotations

from typing import Final

from eval.runners.common import Row, RunnerResult, not_measured, percent

from rulecam.eval.metrics import sam_usd, usd_per_camera_hour
from rulecam.runtime.loop import LoopMetrics

TITLE: Final = "What does it cost, and how fast is it?"
STAGE_ORDER: Final = ("segment", "track", "evaluate", "verify", "act")


def aggregate_e7(
    metrics: LoopMetrics,
    *,
    camera_hours: float,
    gpu_usd_per_hour: float = 0.0,
    cameras_per_gpu: float = 1.0,
    realtime: bool = True,
) -> RunnerResult:
    """Turn one run's runtime metrics into the E7 table.

    Args:
        metrics: The runtime's metrics at the end of the run.
        camera_hours: Camera-hours the run covered. Zero means the rates cannot be computed.
        gpu_usd_per_hour: Hourly price of the GPU pods, all together.
        cameras_per_gpu: How many cameras those pods serve.
        realtime: Whether the run was fed at real time. False marks the result simulated,
            because latency from a sped-up run is not latency.

    Returns:
        The result. With no camera-hours, a "not measured" result.
    """
    if camera_hours <= 0:
        return not_measured(
            "e7",
            TITLE,
            "the run covered no camera-hours, so neither a rate nor a cost can be divided out. "
            "Pass the camera-hours of a real-time run.",
        )

    gated = metrics.sam_calls
    skipped = (
        metrics.chunks_skipped_still
        + metrics.chunks_skipped_idle
        + metrics.chunks_skipped_no_rule
        + metrics.chunks_skipped_budget
    )
    ungated = gated + skipped
    usd = usd_per_camera_hour(
        metrics.billed_frames,
        camera_hours,
        gpu_usd_per_hour=gpu_usd_per_hour,
        cameras_per_gpu=cameras_per_gpu,
    )

    rows = [
        Row(
            name="SAM calls per camera-hour (gated)",
            value=f"{gated / camera_hours:.1f}",
            detail=f"{gated} calls over {camera_hours:.2f} camera-hours",
        ),
        Row(
            name="SAM calls per camera-hour (ungated)",
            value=f"{ungated / camera_hours:.1f}",
            detail=f"counterfactual: the {skipped} skipped chunks had been segmented too",
        ),
        Row(
            name="Calls the gate and cache avoided",
            value=percent(skipped, ungated),
            detail="upper bound: a gate-off run would not have skipped these for other reasons",
        ),
        Row(
            name="Dollars per camera-hour",
            value="n/a" if usd is None else f"${usd:.4f}",
            detail=(
                f"{metrics.billed_frames} billed frames (${sam_usd(metrics.billed_frames):.4f}) "
                f"plus ${gpu_usd_per_hour:.2f}/h of GPU over {cameras_per_gpu:g} cameras"
            ),
        ),
        Row(
            name="Segmentation latency p50 / p95",
            value=f"{metrics.segment_latency_p50_s:.2f} s / {metrics.segment_latency_p95_s:.2f} s",
            detail="the last 200 segmentations",
        ),
    ]
    rows.extend(_stage_rows(metrics))

    caveats: list[str] = []
    if not realtime:
        caveats.append(
            "The run was not fed at real time, so every latency here is an artefact of the "
            "pace it was played at."
        )
    if skipped and not gated:
        caveats.append(
            "Every chunk was skipped, so the gated rate is zero because nothing ran, not "
            "because the gate was efficient."
        )
    if metrics.budget_exceeded:
        caveats.append(
            f"The budget was exceeded {metrics.budget_exceeded} time(s) and "
            f"{len(metrics.budget_degraded)} camera(s) were degraded, so this run was partly "
            "cost-limited and the rates are lower than the system would otherwise have spent."
        )
    if metrics.keyframe_mode:
        caveats.append(
            "A camera went into keyframe mode during the run, so some chunks were sampled "
            "rather than fully segmented."
        )
    if metrics.chunks_failed:
        caveats.append(f"{metrics.chunks_failed} chunk(s) failed and are not in any figure.")
    caveats.append(
        "Cost is one run on one site at hosted pricing. It does not forecast a bill: call "
        "volume follows how busy the scene is."
    )

    return RunnerResult(
        id="e7",
        title=TITLE,
        rows=tuple(rows),
        caveats=tuple(caveats),
        simulated=not realtime,
        simulated_because=(
            "the run was not fed at real time, so the latency figures are not latency."
            if not realtime
            else ""
        ),
    )


def _stage_rows(metrics: LoopMetrics) -> list[Row]:
    known = [name for name in STAGE_ORDER if name in metrics.stages]
    extra = sorted(set(metrics.stages) - set(STAGE_ORDER))
    return [
        Row(
            name=f"{name} latency p50 / p95",
            value=f"{metrics.stages[name].p50_s:.2f} s / {metrics.stages[name].p95_s:.2f} s",
            detail=f"{metrics.stages[name].n} samples",
        )
        for name in (*known, *extra)
    ]
