"""Content hashes that make every candidate re-derivable (P1-06 step 7, MP §7.4)."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from datetime import datetime

import numpy as np

from rulecam.contracts import RuleIR, Track, TrackPoint


def canonical_json(data: object) -> str:
    """JSON with sorted keys and no whitespace."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def rule_ir_hash(rule: RuleIR) -> str:
    """sha256 of the rule's canonical JSON (sorted keys)."""
    payload = canonical_json(rule.model_dump(mode="json"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def track_window_hash(
    camera_id: str, t0: datetime, t1: datetime, hz: float, tracks: Sequence[Track]
) -> str:
    """sha256 over a canonical binary serialisation of the points an evaluation used.

    Args:
        camera_id: The evaluated camera.
        t0: Window start.
        t1: Window end.
        hz: Timeline rate.
        tracks: Prepared tracks (clipped, merged and canonically ordered).

    Returns:
        Hex digest.
    """
    digest = hashlib.sha256()
    header = {"camera_id": camera_id, "t0": t0.isoformat(), "t1": t1.isoformat(), "hz": hz}
    digest.update(canonical_json(header).encode("utf-8"))
    for track in tracks:
        digest.update(canonical_json([track.track_id, track.concept, len(track.points)]).encode())
        digest.update(_points_bytes(track.points, t0))
    return digest.hexdigest()


def _points_bytes(points: tuple[TrackPoint, ...], t0: datetime) -> bytes:
    nan2 = (math.nan, math.nan)
    rows = np.array(
        [
            (
                (p.ts - t0).total_seconds(),
                p.frame_idx,
                *p.box_xyxy,
                *p.foot_px,
                *(p.foot_m if p.foot_m is not None else nan2),
                p.confidence if p.confidence is not None else math.nan,
            )
            for p in points
        ],
        dtype="<f8",
    )
    keypoints = [
        [index, [[k.x, k.y, k.score] for k in p.keypoints]]
        for index, p in enumerate(points)
        if p.keypoints is not None
    ]
    return rows.tobytes() + canonical_json(keypoints).encode("utf-8")


def event_id(rule_hash: str, camera_id: str, subject: str, ts_start: datetime) -> str:
    """Deterministic event id: the same episode found in overlapping windows gets the same id."""
    raw = f"{rule_hash}|{camera_id}|{subject}|{ts_start.isoformat()}"
    return "ev-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
