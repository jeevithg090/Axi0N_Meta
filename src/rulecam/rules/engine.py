"""The pure predicate engine: `evaluate(rule, tracks, …) -> list[CandidateEvent]` (P1-06).

`evaluate` has no I/O, no clock and no randomness. Identical inputs give byte-identical output,
and the order of the track list does not matter. Each camera that has tracks, or that a zone of
the rule is on, is evaluated separately on a common timeline.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from rulecam.contracts import Calibration, CandidateEvent, Measurement, RuleIR, Track, Zone
from rulecam.contracts.rule_ir import All, Any_, Not_, Persist, Predicate, Rate
from rulecam.contracts.rule_ir import Sequence as SequenceOp
from rulecam.rules.hashing import event_id, rule_ir_hash, track_window_hash
from rulecam.rules.ir import (
    child_path,
    children,
    referenced_concepts,
    referenced_zones,
    walk,
)
from rulecam.rules.near_miss import near_miss_trace
from rulecam.rules.operators import OPERATOR_WARMUP_S
from rulecam.rules.pose import PoseFeatures
from rulecam.rules.predicates.pose import pose as pose_state
from rulecam.rules.predicates.presence import count, state
from rulecam.rules.predicates.spatial import distance_m, inside, moving, overlap_iou, without
from rulecam.rules.scene import Scene, resolve_zone
from rulecam.rules.signals import SCENE, Series, Signal, combine, negate, scene_series
from rulecam.rules.temporal import (
    Run,
    absence,
    dwell,
    merge_runs,
    persist,
    rate,
    runs,
    sequence,
)
from rulecam.rules.timeline import Timeline, prepare_tracks, resample
from rulecam.rules.trace import DECIMALS, trace_value

EPISODE_GAP_S = 2.0
_WRAPPERS = frozenset({"all", "any", "not", "persist"})

Atom = Callable[[Any, Scene], Signal]
_ATOMS: dict[str, Atom] = {
    "distance_m": distance_m,
    "inside": inside,
    "overlap": overlap_iou,
    "without": without,
    "moving": moving,
    "count": count,
    "state": state,
    "dwell": dwell,
    "absence": absence,
    "pose": pose_state,
}


@dataclass(frozen=True)
class _Settings:
    rule: RuleIR
    rule_hash: str
    plan_version_hash: str
    end_gap: int
    cooldown: int
    pose_unavailable: bool


def evaluate(
    rule: RuleIR,
    tracks: Sequence[Track],
    *,
    calibration: Calibration | None,
    zones: Mapping[str, Zone],
    t0: datetime,
    t1: datetime,
    hz: float = 10.0,
    episode_gap_s: float = EPISODE_GAP_S,
    cooldown_s: float = 0.0,
    plan_version_hash: str = "",
    pose: PoseFeatures | None = None,
    operator_exclusion: bool = True,
    operator_warmup_s: float = OPERATOR_WARMUP_S,
) -> list[CandidateEvent]:
    """Evaluate a rule over a window of tracks and return one candidate per episode.

    Args:
        rule: The compiled rule. Its predicate being true means "violation".
        tracks: Tracks in any order; points outside `[t0, t1]` are ignored.
        calibration: Ground-plane calibration, used for tracks of its camera.
        zones: Resolved zones by rule zone name (or `zone_id`); missing names fall back to
            the rule's own polygon zone specs.
        t0: Window start (inclusive).
        t1: Window end (inclusive).
        hz: Timeline rate in samples per second.
        episode_gap_s: An episode ends after the predicate is false for this long.
        cooldown_s: Suppress a new episode of the same subject starting within this long
            after the previous reported episode ended.
        plan_version_hash: Hash of the plan version the rule belongs to, copied into events.
        pose: Pose features for `pose` predicates (P4-04). Without them pose states are
            false and candidates carry `pose_unavailable=True`.
        operator_exclusion: Ignore the vehicle a person rides as its operator in person-vehicle
            `distance_m`, buffer-zone `inside` and near-miss metrics (`rules.operators`). On by
            default; `OperatorExclusion.enabled_for(rule.id)` gives the owner's per-rule choice.
        operator_warmup_s: How long a person first seen on board a vehicle is held as its
            operator pending (`rules.operators`, rule 7); 0 turns the warm-up off.

    Returns:
        Candidates sorted by `(ts_start, camera_id, event_id)`.

    Raises:
        RuleEvaluationError: On an invalid window, an unknown zone or an incomplete predicate.
    """
    timeline = Timeline.build(t0, t1, hz)
    prepared = prepare_tracks(tracks, t0, t1, relevant_concepts(rule, zones))
    settings = _Settings(
        rule=rule,
        rule_hash=rule_ir_hash(rule),
        plan_version_hash=plan_version_hash,
        end_gap=max(1, timeline.samples_for(episode_gap_s)),
        cooldown=timeline.samples_for(cooldown_s),
        pose_unavailable=pose is None and any(n.op == "pose" for _, n in walk(rule.predicate)),
    )
    events: list[CandidateEvent] = []
    for camera_id in cameras(rule, zones, prepared):
        own = [t for t in prepared if t.camera_id == camera_id]
        cal = calibration if calibration and calibration.camera_id == camera_id else None
        scene = Scene(
            rule=rule,
            camera_id=camera_id,
            timeline=timeline,
            calibration=cal,
            zones=zones,
            tracks=tuple(resample(t, timeline, cal) for t in own),
            pose=pose,
            operator_exclusion=operator_exclusion,
            operator_warmup_s=operator_warmup_s,
        )
        window_hash = track_window_hash(camera_id, t0, t1, hz, own)
        events.extend(_evaluate_camera(settings, scene, window_hash))
    return sorted(events, key=lambda e: (e.ts_start, e.camera_id, e.event_id))


def relevant_concepts(rule: RuleIR, zones: Mapping[str, Zone]) -> frozenset[str]:
    """Track concept names (keys and SAM prompts) the rule's predicate reads."""
    concepts: set[str] = set()
    for _, node in walk(rule.predicate):
        concepts |= referenced_concepts(node)
        for name in referenced_zones(node):
            buffer_of = resolve_zone(name, zones, rule).buffer_of_concept
            if buffer_of is not None:
                concepts.add(buffer_of)
    names = set(concepts)
    for concept in concepts:
        spec = rule.concepts.get(concept)
        if spec is not None:
            names.add(spec.prompt)
    return frozenset(names)


def cameras(rule: RuleIR, zones: Mapping[str, Zone], tracks: Sequence[Track]) -> list[str]:
    """Cameras to evaluate: those with tracks plus those the predicate's zones are on."""
    found = {t.camera_id for t in tracks}
    for _, node in walk(rule.predicate):
        found |= {resolve_zone(name, zones, rule).camera_id for name in referenced_zones(node)}
    return sorted(found)


def evaluate_node(node: Predicate, scene: Scene, path: str, out: dict[str, Signal]) -> Signal:
    """Evaluate a predicate subtree, recording every node's signal under its trace path."""
    kids = [
        evaluate_node(child, scene, child_path(path, label, child), out)
        for label, child in children(node)
    ]
    signal = _apply(node, kids, scene)
    out[path] = signal
    return signal


def _apply(node: Predicate, kids: list[Signal], scene: Scene) -> Signal:
    if isinstance(node, All | Any_):
        return combine(kids, scene.timeline.n, conjunction=isinstance(node, All))
    if isinstance(node, Not_):
        return negate(kids[0])
    if isinstance(node, Persist):
        return persist(node, kids[0], scene.timeline)
    if isinstance(node, Rate):
        return rate(node, kids[0], scene.timeline)
    if isinstance(node, SequenceOp):
        return sequence(node, kids[0], kids[1], scene.timeline)
    return _ATOMS[node.op](node, scene)


def episodes(truth: Series, end_gap: int, cooldown: int) -> list[Run]:
    """Episodes of a subject: true runs merged across gaps shorter than `end_gap` samples.

    Episodes starting fewer than `cooldown` samples after the previous reported one are dropped.
    """
    kept: list[Run] = []
    for first, last in merge_runs(runs(truth.truth), end_gap - 1):
        if kept and first - kept[-1][1] - 1 < cooldown:
            continue
        kept.append((first, last))
    return kept


def _evaluate_camera(settings: _Settings, scene: Scene, window_hash: str) -> list[CandidateEvent]:
    nodes: dict[str, Signal] = {}
    predicate = settings.rule.predicate
    root = evaluate_node(predicate, scene, predicate.op, nodes)
    sampler = _Sampler(nodes=nodes, subject=root.subject, n=scene.timeline.n)
    events = []
    for key in sorted(root.series):
        series = root.series[key]
        for first, last in episodes(series, settings.end_gap, settings.cooldown):
            fire, end = series.start + first, series.start + last
            events.append(
                _candidate(
                    settings,
                    scene,
                    window_hash=window_hash,
                    sampler=sampler,
                    key=key,
                    fire=fire,
                    end=end,
                )
            )
    return events


@dataclass
class _Sampler:
    """Reads node values for one subject at one timeline index (for traces)."""

    nodes: dict[str, Signal]
    subject: str | None
    n: int
    _scene_cache: dict[str, Series] = field(default_factory=dict)

    def at(self, path: str, key: str, index: int) -> tuple[bool, float]:
        signal = self.nodes[path]
        series = self._series(path, signal, key)
        if series is None or not series.start <= index < series.stop:
            return False, math.nan
        local = index - series.start
        return bool(series.truth[local]), float(series.value[local])

    def _series(self, path: str, signal: Signal, key: str) -> Series | None:
        if signal.subject is None:
            return signal.series[SCENE]
        if signal.subject == self.subject:
            return signal.series.get(key)
        if path not in self._scene_cache:
            self._scene_cache[path] = scene_series(signal, self.n)
        return self._scene_cache[path]


def _candidate(
    settings: _Settings,
    scene: Scene,
    *,
    window_hash: str,
    sampler: _Sampler,
    key: str,
    fire: int,
    end: int,
) -> CandidateEvent:
    timeline = scene.timeline
    ts_start = timeline.ts_at(fire)
    trace: dict[str, float | str | bool] = {
        "subject": key,
        "episode_s": round((end - fire + 1) / timeline.hz, DECIMALS),
    }
    if settings.pose_unavailable:
        trace["pose_unavailable"] = True
    trace.update(_trace(settings.rule.predicate, sampler, key, fire))
    trace.update(near_miss_trace(settings.rule.predicate, scene, key, fire))
    return CandidateEvent(
        event_id=event_id(settings.rule_hash, scene.camera_id, key, ts_start),
        rule_id=settings.rule.id,
        camera_id=scene.camera_id,
        ts_start=ts_start,
        ts_end=timeline.ts_at(end),
        severity=settings.rule.severity,
        measurement=_measurement(settings.rule.predicate, sampler, key, fire),
        predicate_trace=trace,
        strip_paths=(),
        track_window_hash=window_hash,
        plan_version_hash=settings.plan_version_hash,
        rule_ir_hash=settings.rule_hash,
    )


def _trace(
    predicate: Predicate, sampler: _Sampler, key: str, index: int
) -> dict[str, float | str | bool]:
    trace: dict[str, float | str | bool] = {}
    for path, _ in walk(predicate):
        truth, value = sampler.at(path, key, index)
        trace[path] = truth
        if sampler.nodes[path].unit is not None:
            trace[f"{path}.value"] = trace_value(value)
        elif math.isnan(value):
            trace[f"{path}.unknown"] = True
    return trace


def _measurement(
    predicate: Predicate, sampler: _Sampler, key: str, index: int
) -> Measurement | None:
    """The firing-time value of the first measuring node (wrappers are skipped)."""
    walked = list(walk(predicate))
    measuring = [p for p, n in walked if n.op not in _WRAPPERS and sampler.nodes[p].unit]
    fallback = [p for p, n in walked if n.op == "persist"]
    paths = measuring or fallback
    if not paths:
        return None
    _, value = sampler.at(paths[0], key, index)
    unit = sampler.nodes[paths[0]].unit
    if unit is None or not math.isfinite(value):
        return None
    return Measurement(value=round(value, DECIMALS), unit=unit)
