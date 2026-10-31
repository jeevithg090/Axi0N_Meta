"""Operator exclusion: the person riding a vehicle is its operator, not a pedestrian (WS1c).

A forklift driver sits inside the forklift's box, so `distance_m(person, forklift) < 2` measures
the driver at 0-0.8 m from their own vehicle and fires on every frame. The engine therefore
derives, per (person track, vehicle track) pair, whether the person is that vehicle's
**operator**, and ignores that vehicle for that person while they ride it. No Rule IR field is
involved: it is a derived track attribute.

**Criteria** (constants below, tuned on the synthetic Rule CI traces and the warehouse clip):

1. *On board* at a sample: `area(person box ∩ vehicle box) / area(person box)` is at least
   `MIN_CONTAINMENT` (0.5).
2. *Co-moving* at a sample: both image velocities (box centres, least-squares over a centred
   `VELOCITY_WINDOW_S` window) have cosine similarity of at least `MIN_COSINE` (0.9) and speeds
   within `MAX_SPEED_GAP` (30%) of the faster one. Image velocities are used because the rider is
   at the vehicle's depth, so both move the same number of pixels; their ground projections do
   not (the rider's feet are off the floor).
3. *Linked over a boarding*: a boarding is a run of on-board samples (gaps up to
   `MAX_BOARDING_GAP_S`, 0.5 s, bridged). Among its samples where the vehicle moves (at least
   `STILL_HEIGHTS_PER_S`, 5% of its own box height, per second), the pair co-moves on at least
   `MIN_COMOVING_FRACTION` (80%) of them, and there are at least `MIN_LINK_S` (1 s) of them.

The person is the vehicle's operator for the whole of every linked boarding. When they step off
(containment below 0.5) they are a pedestrian again. A pedestrian standing in front of a moving
forklift is on board by box overlap but does not co-move, so that boarding is never linked and
still fires; the same holds when a vehicle drives into the person who just left it. Known limits
(Rule CI covers both sides): a person walking alongside a moving vehicle *inside its box* for over
a second looks like an operator; a driver on a vehicle that does not move during the boarding is
never linked (a rule without `moving(vehicle)` fires on them), the conservative side.

**Scope.** Only pairs where one concept is person-like (`person`, `worker`, `pedestrian`, ...)
and the other vehicle-like (`forklift`, `pallet jack`, `cart`, `vehicle`, `truck`, ...), judged
on the rule's concept key and SAM prompt, in `distance_m`, buffer-zone `inside` and the near-miss
(time-to-collision) trace of `distance_m`.

**Override.** On by default. The owner turns it off per rule with engine configuration keyed by
rule id: `OperatorExclusion(disabled_rules=...)` (the runtime reads
`RULECAM_OPERATOR_EXCLUSION_OFF`, a comma-separated list of rule ids) resolves to
`evaluate(..., operator_exclusion=False)` for those rules, and `DecisionContext` records the flag
so `rederive` reproduces the decision.
"""

from __future__ import annotations

import re
from typing import Final

import numpy as np

from rulecam.contracts import Frozen, RuleIR
from rulecam.contracts.rule_ir import DistanceM, Inside
from rulecam.rules.geometry import BoolArray, FloatArray, box_centres, norm
from rulecam.rules.ir import parse_ref, walk
from rulecam.rules.timeline import SampledTrack

MIN_CONTAINMENT: Final = 0.5
MIN_COSINE: Final = 0.9
MAX_SPEED_GAP: Final = 0.3
STILL_HEIGHTS_PER_S: Final = 0.05
MIN_COMOVING_FRACTION: Final = 0.8
MIN_LINK_S: Final = 1.0
MAX_BOARDING_GAP_S: Final = 0.5
VELOCITY_WINDOW_S: Final = 1.0
_MIN_VELOCITY_SAMPLES: Final = 2

PERSON_WORDS: Final = re.compile(
    r"\b(?:persons?|people|pedestrians?|workers?|humans?|staff|employees?|visitors?|"
    r"walkers?|operatives?)\b"
)
VEHICLE_WORDS: Final = re.compile(
    r"\b(?:fork[\s_-]?lifts?|fork[\s_-]?trucks?|pallet[\s_-]?(?:jacks?|trucks?|movers?)|"
    r"reach[\s_-]?trucks?|order[\s_-]?pickers?|carts?|trolleys?|tuggers?|vehicles?|trucks?|"
    r"lift[\s_-]?trucks?|stackers?|loaders?)\b"
)


class OperatorExclusion(Frozen):
    """Engine configuration for operator exclusion, keyed by rule id.

    Attributes:
        disabled_rules: Ids of rules whose owner turned operator exclusion off.
    """

    disabled_rules: tuple[str, ...] = ()

    @classmethod
    def from_csv(cls, text: str | None) -> OperatorExclusion:
        """Parse a comma-separated list of rule ids (blank entries ignored)."""
        ids = sorted({part.strip() for part in (text or "").split(",") if part.strip()})
        return cls(disabled_rules=tuple(ids))

    def enabled_for(self, rule_id: str) -> bool:
        """Whether operator exclusion applies to the rule."""
        return rule_id not in self.disabled_rules


def _words(name: str) -> str:
    return name.lower().replace("_", " ")


def is_person_like(*names: str) -> bool:
    """Whether any of a concept's names (key, SAM prompt) denotes a person."""
    return any(PERSON_WORDS.search(_words(n)) for n in names)


def is_vehicle_like(*names: str) -> bool:
    """Whether any of a concept's names (key, SAM prompt) denotes a vehicle a person can ride."""
    return any(VEHICLE_WORDS.search(_words(n)) for n in names)


def _concept_names(rule: RuleIR, concept: str) -> tuple[str, ...]:
    spec = rule.concepts.get(concept)
    return (concept,) if spec is None else (concept, spec.prompt)


def is_operator_pair(rule: RuleIR, first: str, second: str) -> bool:
    """Whether two rule concepts are a person and a vehicle (in either order)."""
    one, two = _concept_names(rule, first), _concept_names(rule, second)
    return (is_person_like(*one) and is_vehicle_like(*two)) or (
        is_vehicle_like(*one) and is_person_like(*two)
    )


def has_operator_pair(rule: RuleIR) -> bool:
    """Whether operator exclusion can change the rule's outcome.

    True when a `distance_m` node, or an `inside` node on a buffer zone, relates a person-like
    and a vehicle-like concept.
    """
    for _, node in walk(rule.predicate):
        if isinstance(node, DistanceM):
            pair = (parse_ref(node.a)[0], parse_ref(node.b)[0])
        elif isinstance(node, Inside) and node.zone in rule.zones:
            spec = rule.zones[node.zone]
            if spec.kind != "buffer" or spec.buffer_of is None:
                continue
            pair = (parse_ref(node.object)[0], spec.buffer_of)
        else:
            continue
        if is_operator_pair(rule, *pair):
            return True
    return False


def _containment(person: FloatArray, vehicle: FloatArray) -> FloatArray:
    """Row-wise share of the person box's area that lies inside the vehicle box."""
    ix = np.clip(
        np.minimum(person[:, 2], vehicle[:, 2]) - np.maximum(person[:, 0], vehicle[:, 0]), 0, None
    )
    iy = np.clip(
        np.minimum(person[:, 3], vehicle[:, 3]) - np.maximum(person[:, 1], vehicle[:, 1]), 0, None
    )
    area = (person[:, 2] - person[:, 0]) * (person[:, 3] - person[:, 1])
    with np.errstate(divide="ignore", invalid="ignore"):
        share: FloatArray = np.where(area > 0, ix * iy / area, 0.0)
    return np.nan_to_num(share, nan=0.0)


def image_velocity(boxes: FloatArray, hz: float) -> FloatArray:
    """Box-centre velocity in px/s: least-squares slope over a centred window, NaN-aware.

    Args:
        boxes: Row-aligned xyxy boxes, shape (L, 4); NaN rows are absent samples.
        hz: Timeline rate.

    Returns:
        Velocities, shape (L, 2); NaN where fewer than two samples in the window are present.
    """
    centres = box_centres(boxes)
    half = max(1, round(VELOCITY_WINDOW_S * hz / 2))
    size = len(centres)
    sums = np.zeros((5, size))  # per sample: n, sum t, sum t^2, sum x, sum y
    cross = np.zeros((size, 2))
    for offset in range(-half, half + 1):
        lo, hi = max(0, -offset), min(size, size - offset)
        if lo >= hi:
            continue
        shifted = centres[lo + offset : hi + offset]
        ok = ~np.isnan(shifted[:, 0])
        t = offset / hz
        rows = slice(lo, hi)
        sums[0, rows] += ok
        sums[1, rows] += ok * t
        sums[2, rows] += ok * t * t
        sums[3, rows] += np.where(ok, shifted[:, 0], 0.0)
        sums[4, rows] += np.where(ok, shifted[:, 1], 0.0)
        cross[rows] += np.where(ok[:, None], shifted * t, 0.0)
    count, s_t, s_tt = sums[0], sums[1], sums[2]
    denom = count * s_tt - s_t * s_t
    out = np.full((size, 2), np.nan)
    good = (count >= _MIN_VELOCITY_SAMPLES) & (denom > 0)
    for axis, s_x in ((0, sums[3]), (1, sums[4])):
        with np.errstate(divide="ignore", invalid="ignore"):
            slope = (count * cross[:, axis] - s_t * s_x) / denom
        out[:, axis] = np.where(good, slope, np.nan)
    return out


def _co_moving(person_v: FloatArray, vehicle_v: FloatArray) -> BoolArray:
    p_speed, v_speed = norm(person_v), norm(vehicle_v)
    dot = person_v[:, 0] * vehicle_v[:, 0] + person_v[:, 1] * vehicle_v[:, 1]
    with np.errstate(divide="ignore", invalid="ignore"):
        cosine = dot / (p_speed * v_speed)
        gap = np.abs(p_speed - v_speed) / np.fmax(p_speed, v_speed)
    ok: BoolArray = (cosine >= MIN_COSINE) & (gap <= MAX_SPEED_GAP)
    return ok


def operator_mask(person: SampledTrack, vehicle: SampledTrack, hz: float) -> BoolArray:
    """Samples of `person`'s range where the person is `vehicle`'s operator.

    Args:
        person: A person-like track.
        vehicle: A vehicle-like track.
        hz: Timeline rate.

    Returns:
        Boolean array aligned to `person`'s index range: True over every linked boarding
        where both tracks are present.
    """
    out = np.zeros(len(person.present), dtype=np.bool_)
    lo, hi = max(person.start, vehicle.start), min(person.stop, vehicle.stop)
    if lo >= hi:
        return out
    own = slice(lo - person.start, hi - person.start)
    theirs = slice(lo - vehicle.start, hi - vehicle.start)
    both = person.present[own] & vehicle.present[theirs]
    p_box, v_box = person.box[own], vehicle.box[theirs]
    on_board = both & (_containment(p_box, v_box) >= MIN_CONTAINMENT)
    p_vel = image_velocity(person.box, hz)[own]
    v_vel = image_velocity(vehicle.box, hz)[theirs]
    height = v_box[:, 3] - v_box[:, 1]
    with np.errstate(invalid="ignore"):
        vehicle_moving = norm(v_vel) >= STILL_HEIGHTS_PER_S * height
    co_moving = _co_moving(p_vel, v_vel)
    need = max(1, round(MIN_LINK_S * hz))
    linked = np.zeros(len(on_board), dtype=np.bool_)
    for first, last in boardings(on_board, round(MAX_BOARDING_GAP_S * hz)):
        run = slice(first, last + 1)
        judged = vehicle_moving[run] & on_board[run]
        count = int(judged.sum())
        if count >= need and (co_moving[run] & judged).sum() >= MIN_COMOVING_FRACTION * count:
            linked[run] = True
    out[own] = linked & both
    return out


def boardings(on_board: BoolArray, max_gap: int) -> list[tuple[int, int]]:
    """Inclusive `(first, last)` runs of True, merged across gaps of at most `max_gap` samples."""
    idx = np.flatnonzero(on_board)
    if not len(idx):
        return []
    breaks = np.flatnonzero(np.diff(idx) > max_gap + 1)
    starts = np.concatenate([[idx[0]], idx[breaks + 1]])
    ends = np.concatenate([idx[breaks], [idx[-1]]])
    return [(int(a), int(b)) for a, b in zip(starts, ends, strict=True)]
