"""Scene-level predicates: counting objects and visual states."""

from __future__ import annotations

import numpy as np

from rulecam.contracts.rule_ir import Count, StateCheck
from rulecam.rules.geometry import FloatArray
from rulecam.rules.ir import parse_ref, state_concepts
from rulecam.rules.scene import Scene, ref_points, zone_contains
from rulecam.rules.signals import SCENE, Signal, make_series
from rulecam.rules.timeline import SampledTrack


def count_present(scene: Scene, tracks: tuple[SampledTrack, ...]) -> FloatArray:
    """Number of the given tracks present at each timeline sample."""
    counts = np.zeros(scene.timeline.n)
    for track in tracks:
        counts[track.start : track.stop] += track.present
    return counts


def count_in_zone(scene: Scene, ref: str, zone_name: str | None) -> FloatArray:
    """Number of `ref` objects present (and, with a zone, inside it) at each sample."""
    concept, part = parse_ref(ref)
    tracks = scene.tracks_of(concept)
    if zone_name is None:
        return count_present(scene, tracks)
    zone = scene.zone(zone_name)
    counts = np.zeros(scene.timeline.n)
    for track in tracks:
        hit = np.zeros(len(track.present), dtype=np.bool_)
        for points in ref_points(scene, track, part):
            hit |= zone_contains(scene, zone, points, track.start, track)
        counts[track.start : track.stop] += hit & track.present
    return counts


def count(node: Count, scene: Scene) -> Signal:
    """Number of objects (optionally in a zone) compared with `gt` / `lt`.

    With neither bound set, the predicate holds when at least one object is counted.
    """
    counts = count_in_zone(scene, node.object, node.zone)
    truth = counts > (node.gt if node.gt is not None else 0)
    if node.lt is not None:
        truth &= counts < node.lt
    known = np.ones(scene.timeline.n, dtype=np.bool_)
    return Signal(subject=None, unit="count", series={SCENE: make_series(0, truth, counts, known)})


def state(node: StateCheck, scene: Scene) -> Signal:
    """A visual state is the presence of its state concept: `door`/`open` → `"open door"`.

    The concept may also be declared as `"door/open"`; the state `present` means the object
    itself is visible.
    """
    by_id = {t.track_id: t for name in state_concepts(node) for t in scene.tracks_of(name)}
    counts = count_present(scene, tuple(by_id[key] for key in sorted(by_id)))
    known = np.ones(scene.timeline.n, dtype=np.bool_)
    return Signal(
        subject=None, unit="count", series={SCENE: make_series(0, counts > 0, counts, known)}
    )
