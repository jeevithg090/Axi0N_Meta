"""Score recorded runs against the eval set's labels (`rulecam eval score`; no network).

Per run (one clip, one rule): alert episodes are matched to labelled events (temporal IoU), the
Tier 1 verdicts are checked for decisiveness and against the labels, the alert latency is taken
from event start to the verified alert, and lag, keyframe mode and SAM spend come from the
`/runtime/metrics` samples. Per rule, the counts are summed over clips. Latency and lag are only
meaningful at real-time pace, so runs played faster (`speed > 1`) are left out of those numbers.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from rulecam.contracts import Frozen
from rulecam.eval import metrics as m
from rulecam.eval.errors import EvalError
from rulecam.eval.observe import Observed, observe
from rulecam.eval.record import RunRecord
from rulecam.eval.schema import EvalSet, RuleLabels
from rulecam.eval.tracking import TrackingScores, load_mot, score_tracking

REALTIME_SPEED = 1.0
Outcome = Literal["activated", "refused", "error"]


class ScoreConfig(Frozen):
    """Matching and pricing knobs (defaults follow PLAN-9.5 §3)."""

    iou: float = Field(default=m.DEFAULT_IOU, gt=0, le=1)
    merge_gap_s: float = Field(default=m.DEFAULT_MERGE_GAP_S, ge=0)
    min_alert_s: float = Field(default=m.DEFAULT_MIN_ALERT_S, ge=0)
    gpu_usd_per_hour: float = Field(default=0.0, ge=0)
    cameras_per_gpu: float = Field(default=1.0, gt=0)
    cameras_per_gpu_source: str = ""  # where the count came from (flag, site config)


def resolve_cameras_per_gpu(
    flag: float | None, site_cameras_per_gpu: int | None, *, site_source: str = "site config"
) -> tuple[float, str]:
    """How many cameras share the GPU pod, and where that number came from.

    `--cameras-per-gpu` wins, then the site config's `cameras_per_gpu`, else 1 (no sharing:
    the camera pays the whole pod).

    Args:
        flag: `--cameras-per-gpu`, if given.
        site_cameras_per_gpu: The site config's `cameras_per_gpu`, if any.
        site_source: How to name the site config in the report.

    Returns:
        `(cameras per GPU, source)`.
    """
    if flag is not None:
        return flag, "--cameras-per-gpu"
    if site_cameras_per_gpu is not None:
        return float(site_cameras_per_gpu), site_source
    return 1.0, "default"


class TrackingRow(Frozen):
    """Tracking scores of one concept on one clip."""

    concept: str
    frames: int
    hota: float
    idf1: float
    id_switches: int
    id_switches_per_min: float | None


class RunScore(Frozen):
    """The scores of one run."""

    clip: str
    rule: str
    status: str
    expect: str
    outcome: Outcome
    refused: str | None = None
    error: str | None = None
    speed: float
    request: str
    rule_hashes: tuple[str, ...] = ()
    candidates: int = 0
    alerts: int = 0
    events: int = 0
    tp: int = 0
    fp: int = 0
    fn: int = 0
    ious: tuple[float, ...] = ()
    latencies_s: tuple[float, ...] = ()
    tier1: tuple[str | None, ...] = ()
    decisive: int = 0
    agree: int = 0
    lags_s: tuple[float, ...] = ()
    keyframe_mode: bool = False
    billed_frames: int | None = None
    camera_hours: float = 0.0
    usd_per_camera_hour: float | None = None
    tracking: tuple[TrackingRow, ...] = ()

    @property
    def scored(self) -> bool:
        """Whether this run has ground truth (labelled or negative)."""
        return self.status in {"labelled", "negative"}

    @property
    def realtime(self) -> bool:
        """Whether it was played at real-time pace."""
        return self.speed <= REALTIME_SPEED


class RuleScore(Frozen):
    """One rule summed over its clips."""

    rule: str
    clips: int
    tp: int
    fp: int
    fn: int
    precision: float | None
    recall: float | None
    false_alerts_on_negatives: int
    negative_clips: int
    latency_p95_s: float | None


class EvalReport(Frozen):
    """Everything `rulecam scorecard` needs, saved as `reports/eval/<date>.json`."""

    generated_at: datetime
    config: ScoreConfig
    runs: tuple[RunScore, ...]
    rules: tuple[RuleScore, ...]


def _episodes(
    alerts: Sequence[Observed], config: ScoreConfig
) -> list[tuple[m.Interval, datetime | None]]:
    """Merge alerting candidates into episodes; keep each episode's first alert time."""
    out: list[tuple[m.Interval, datetime | None]] = []
    for obs in sorted(alerts, key=lambda o: o.start_s):
        span = m.Interval(obs.start_s, obs.end_s)
        if out and span.start <= out[-1][0].end + config.merge_gap_s:
            last, at = out[-1]
            times = [t for t in (at, obs.alert_at) if t is not None]
            out[-1] = (m.Interval(last.start, max(last.end, span.end)), min(times, default=None))
        else:
            out.append((span, obs.alert_at))
    return [(m.widen(span, config.min_alert_s), at) for span, at in out]


def _agreement(
    observed: Sequence[Observed], events: Sequence[m.Interval], config: ScoreConfig
) -> int:
    """Count decisive Tier 1 verdicts that match the labels (violation iff it overlaps one)."""
    agree = 0
    for obs in observed:
        if obs.tier1 is None or obs.tier1 == m.UNSURE:
            continue
        span = m.widen(m.Interval(obs.start_s, obs.end_s), config.min_alert_s)
        truth = "violation" if m.overlaps_any(span, events) else "no_violation"
        agree += obs.tier1 == truth
    return agree


def _billed(run: RunRecord) -> int | None:
    """Hosted SAM frames billed during the run, or `None` without two metrics samples."""
    if len(run.metrics) < 2:  # noqa: PLR2004 - a delta needs two samples
        return None
    return max(0, run.metrics[-1].billed_frames - run.metrics[0].billed_frames)


def _latencies(
    run: RunRecord,
    events: Sequence[m.Interval],
    episodes: Sequence[tuple[m.Interval, datetime | None]],
    matching: m.Matching,
) -> tuple[float, ...]:
    out = []
    for match in matching.matches:
        at = episodes[match.alert][1]
        start = run.timeline.to_camera(events[match.event].start)
        if at is not None and start is not None:
            out.append((at - start).total_seconds())
    return tuple(out)


def _tracking(run: RunRecord, evalset: EvalSet) -> tuple[TrackingRow, ...]:
    labels = evalset.clip(run.clip).tracks
    if labels is None or not run.predicted:
        return ()
    gt = load_mot(evalset.clip_dir(run.clip) / labels.file, labels.classes)
    scale = (1.0, 1.0)
    if labels.width and labels.height and run.chunk_width and run.chunk_height:
        scale = (labels.width / run.chunk_width, labels.height / run.chunk_height)
    scores: list[TrackingScores] = score_tracking(gt, run.predicted, fps=labels.fps, scale=scale)
    return tuple(
        TrackingRow(
            concept=s.concept,
            frames=s.frames,
            hota=s.hota,
            idf1=s.idf1,
            id_switches=s.id_switches,
            id_switches_per_min=s.id_switches_per_min,
        )
        for s in scores
    )


def score_run(
    run: RunRecord, labels: RuleLabels, evalset: EvalSet, config: ScoreConfig
) -> RunScore:
    """Score one run against its rule's labels."""
    base = {
        "clip": run.clip,
        "rule": run.rule,
        "status": labels.status,
        "expect": labels.expect,
        "speed": run.speed,
        "request": labels.request,
        "rule_hashes": run.rule_hashes,
    }
    if run.error is not None:
        return RunScore(**base, outcome="error", error=run.error)
    if run.refused is not None:
        return RunScore(**base, outcome="refused", refused=run.refused)
    observed = observe(run)
    events = [m.Interval(e.start_s, e.end_s) for e in labels.events]
    episodes = _episodes([o for o in observed if o.alert], config)
    matching = m.match_events(events, [span for span, _ in episodes], iou=config.iou)
    billed = _billed(run)
    hours = run.camera_hours
    return RunScore(
        **base,
        outcome="activated",
        candidates=len(observed),
        alerts=len(episodes),
        events=len(events),
        tp=matching.tp,
        fp=matching.fp,
        fn=matching.fn,
        ious=tuple(match.iou for match in matching.matches),
        latencies_s=_latencies(run, events, episodes, matching),
        tier1=tuple(o.tier1 for o in observed),
        decisive=m.decisiveness(o.tier1 for o in observed)[0],
        agree=_agreement(observed, events, config) if labels.status != "unlabelled" else 0,
        lags_s=tuple(s.lag_s for s in run.metrics),
        keyframe_mode=any(s.keyframe_mode for s in run.metrics),
        billed_frames=billed,
        camera_hours=hours,
        usd_per_camera_hour=None
        if billed is None
        else m.usd_per_camera_hour(
            billed,
            hours,
            gpu_usd_per_hour=config.gpu_usd_per_hour,
            cameras_per_gpu=config.cameras_per_gpu,
        ),
        tracking=_tracking(run, evalset),
    )


def summarise_rules(runs: Sequence[RunScore]) -> tuple[RuleScore, ...]:
    """Sum each rule's activated, scored runs over clips."""
    by_rule: dict[str, list[RunScore]] = defaultdict(list)
    for run in runs:
        if run.outcome == "activated" and run.scored:
            by_rule[run.rule].append(run)
    out = []
    for rule, rows in sorted(by_rule.items()):
        tp, fp, fn = (sum(getattr(r, k) for r in rows) for k in ("tp", "fp", "fn"))
        negatives = [r for r in rows if r.status == "negative"]
        latencies = [x for r in rows if r.realtime for x in r.latencies_s]
        out.append(
            RuleScore(
                rule=rule,
                clips=len(rows),
                tp=tp,
                fp=fp,
                fn=fn,
                precision=m.precision(tp, fp),
                recall=m.recall(tp, fn),
                false_alerts_on_negatives=sum(r.alerts for r in negatives),
                negative_clips=len(negatives),
                latency_p95_s=m.percentile(latencies, 95),
            )
        )
    return tuple(out)


def score_runs(
    runs: Sequence[RunRecord],
    evalset: EvalSet,
    *,
    config: ScoreConfig | None = None,
    generated_at: datetime,
) -> EvalReport:
    """Score every run and summarise per rule.

    Raises:
        EvalSetError: If a run names a clip or rule that is not in the eval set.
    """
    config = config or ScoreConfig()
    scored = tuple(
        score_run(run, evalset.clip(run.clip).rule(run.rule), evalset, config) for run in runs
    )
    return EvalReport(
        generated_at=generated_at, config=config, runs=scored, rules=summarise_rules(scored)
    )


def save_report(report: EvalReport, path: Path) -> Path:
    """Write the report as pretty JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def load_report(path: Path) -> EvalReport:
    """Read a report written by `save_report`.

    Raises:
        EvalError: If it is unreadable or malformed.
    """
    try:
        return EvalReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        msg = f"{path}: {exc}"
        raise EvalError(msg) from exc
