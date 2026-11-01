"""Operator exclusion: the person riding a vehicle is its operator, not a pedestrian (WS1c).

A forklift driver sits inside the forklift's box, so `distance_m(person, forklift) < 2` measures
the driver at 0-0.8 m from their own vehicle and fires on every frame. The engine therefore
derives, per (person track, vehicle track) pair, whether the person is that vehicle's
**operator**, and ignores that vehicle for that person while they ride it. No Rule IR field is
involved: it is a derived track attribute.

**Criteria** (constants below, tuned on the synthetic Rule CI traces and on the live
driver-only warehouse clip, `tests/fixtures/rules/live_driver_only.json`):

1. *On board* at a sample: `area(person box ∩ vehicle box) / area(person box)` is at least
   `MIN_CONTAINMENT` (0.4; the live seated driver's box spills past the close-up forklift's, at
   0.44-0.52 containment).
2. *Vehicle moving* at a sample: its image velocity (box centre, least-squares over a centred
   `VELOCITY_WINDOW_S` window) is at least `STILL_HEIGHTS_PER_S` (5%) of its box height per
   second, **or**, with a calibration, its ground speed (the `moving` predicate's estimator) is
   at least `MOVING_MPS` (0.1 m/s). The ground test is what a close-up needs: a forklift whose
   box fills the frame moves a few pixels a second (1-3% of its height) while
   `moving(forklift, 0.2)` is true, so on image speed alone no boarding was ever judged.
3. *Rigid* at a sample: the vehicle box's width and height change by at most
   `MAX_RESHAPE_HEIGHTS_PER_S` (5% of its height) per second. A box that is changing shape (the
   detector growing a close-up forklift's box by 200 px as it finds more of it, a vehicle driving
   at the camera) moves its centre without the vehicle moving, so those samples are not judged.
4. *Carried* at a sample (strict): both image velocities have cosine similarity of at least
   `MIN_COSINE` (0.9) and speeds within `MAX_SPEED_GAP` (30%) of the faster one. *Co-moving*:
   carried, or the relative image velocity `|v_person - v_vehicle|` is at most
   `MAX_RELATIVE_HEIGHTS_PER_S` (5% of the vehicle's box height per second, about 0.12 m/s for a
   2.4 m box): both displacements agree within tolerance even when both are small, as for a
   slow vehicle and its seated driver, whose directions are noise. Image velocities are used
   because the rider is at the vehicle's depth, so both move the same number of pixels; their
   ground projections do not (the rider's feet are off the floor).
5. *Linked over a boarding*: a boarding is a run of on-board samples (gaps up to
   `MAX_BOARDING_GAP_S`, 0.5 s, bridged). Its judged samples are those where the vehicle is
   moving and rigid; the pair co-moves on at least `MIN_COMOVING_FRACTION` (80%) of them, and
   there are at least `MIN_LINK_S` (1 s) of them (the minimum dwell). Samples where the vehicle is
   momentarily still are not judged, so a stop does not break the link. A boarding still running
   at the window's last sample with fewer judged samples, but at least one, is *pending*: linked
   if its judged samples so far co-move. Without that, every window that ends just after the
   tracker re-identifies the vehicle (a new vehicle track born around its seated driver) fires.
6. *Pedestrian safeguard*: a boarding is never linked (nor pending) when the person was standing
   or walking outside the box while the vehicle moved: in the `ENTRY_S` (1 s) before the
   boarding, the person was off board beside the moving vehicle on at least half of the samples
   and carried (strict test) on fewer than `MIN_COMOVING_FRACTION` of those. A slow vehicle
   that drives up to a standing person therefore stays a hazard even when their relative motion
   is under the co-moving tolerance.

7. *Warm-up* (`OPERATOR_WARMUP_S`, 1.5 s from the person track's first sample, configurable up
   to `MAX_OPERATOR_WARMUP_S`): a boarding that starts at the pair's first common sample during
   the person's warm-up (the person track is born, or the vehicle track is born around them,
   already on board, so the safeguard has nothing to look at) is *operator pending* while it
   lasts no longer than the warm-up: held (ignored) until the warm-up ends, then resolved. It is
   the vehicle's operator if it ended inside the warm-up without its judged samples refuting
   co-motion (the live keyframe tracker's first second: the seated driver's new track, then a
   re-identified forklift track, each too short for 1 s of judged samples); a pedestrian
   otherwise, and then the whole boarding counts again, from its first sample. A window that
   ends inside the warm-up holds the boarding whatever its evidence. Pedestrians seen outside
   the box first never wait. **Latency:** a pedestrian first seen inside a moving vehicle's box
   is reported at most `warmup_s` (1.5 s) later than without the hold, on HIGH (act-first) rules
   too; that is inside the 10 s alert-latency target (docs/PLAN-9.5.md section 2).

The person is the vehicle's operator for the whole of every linked boarding. When they step off
(containment below `MIN_CONTAINMENT`) they are a pedestrian again. A pedestrian standing in front
of a moving forklift does not co-move and was outside its box first, so that boarding is never
linked and still fires; the same holds when a vehicle drives into the person who just left it.
Known limits (Rule CI covers both sides): a person walking alongside a moving vehicle *inside its
box* for over a second looks like an operator, and so does a person first seen already inside
the box of a vehicle creeping slower than the co-moving tolerance; a driver on a vehicle that
does not move, or only changes shape, during the boarding is never linked (a rule without
`moving(vehicle)` fires on them), the conservative side.

**Scope.** Only pairs where one concept is person-like (`person`, `worker`, `pedestrian`, ...)
and the other vehicle-like (`forklift`, `pallet jack`, `cart`, `vehicle`, `truck`, ...), judged
on the rule's concept key and SAM prompt, in `distance_m`, buffer-zone `inside` and the near-miss
(time-to-collision) trace of `distance_m`.

**Override.** On by default. The owner turns it off per rule with engine configuration keyed by
rule id: `OperatorExclusion(disabled_rules=...)` (the runtime reads
`RULECAM_OPERATOR_EXCLUSION_OFF`, a comma-separated list of rule ids) resolves to
`evaluate(..., operator_exclusion=False)` for those rules, and `DecisionContext` records the flag
so `rederive` reproduces the decision. The warm-up is `OperatorExclusion.warmup_s`
(`RULECAM_OPERATOR_WARMUP_S`, 0 turns it off), passed as `evaluate(..., operator_warmup_s=...)`
and recorded in `DecisionContext` the same way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

import numpy as np
from pydantic import Field

from rulecam.contracts import Frozen, RuleIR
from rulecam.contracts.rule_ir import DistanceM, Inside
from rulecam.rules.geometry import (
    SPEED_MEDIAN_SAMPLES,
    BoolArray,
    FloatArray,
    box_centres,
    ground_speed,
    norm,
)
from rulecam.rules.ir import parse_ref, walk
from rulecam.rules.timeline import SampledTrack

MIN_CONTAINMENT: Final = 0.4
MIN_COSINE: Final = 0.9
MAX_SPEED_GAP: Final = 0.3
STILL_HEIGHTS_PER_S: Final = 0.05
MOVING_MPS: Final = 0.1
MAX_RELATIVE_HEIGHTS_PER_S: Final = 0.05
MAX_RESHAPE_HEIGHTS_PER_S: Final = 0.05
MIN_COMOVING_FRACTION: Final = 0.8
MIN_LINK_S: Final = 1.0
MAX_BOARDING_GAP_S: Final = 0.5
ENTRY_S: Final = 1.0
VELOCITY_WINDOW_S: Final = 1.0
OPERATOR_WARMUP_S: Final = 1.5
MAX_OPERATOR_WARMUP_S: Final = 1.5
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
        warmup_s: How long a person first seen on board is held as operator pending (rule 7).
    """

    disabled_rules: tuple[str, ...] = ()
    warmup_s: float = Field(default=OPERATOR_WARMUP_S, ge=0.0, le=MAX_OPERATOR_WARMUP_S)

    @classmethod
    def from_csv(
        cls, text: str | None, *, warmup_s: float = OPERATOR_WARMUP_S
    ) -> OperatorExclusion:
        """Parse a comma-separated list of rule ids (blank entries ignored), with a warm-up."""
        ids = sorted({part.strip() for part in (text or "").split(",") if part.strip()})
        return cls(disabled_rules=tuple(ids), warmup_s=warmup_s)

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


def image_velocity(boxes: FloatArray, hz: float, window_s: float = VELOCITY_WINDOW_S) -> FloatArray:
    """Box-centre velocity in px/s: least-squares slope over a centred window, NaN-aware.

    Args:
        boxes: Row-aligned xyxy boxes, shape (L, 4); NaN rows are absent samples.
        hz: Timeline rate.
        window_s: Length of the centred window.

    Returns:
        Velocities, shape (L, 2); NaN where fewer than two samples in the window are present.
    """
    return slopes(box_centres(boxes), hz, window_s)


def reshape_rate(boxes: FloatArray, hz: float) -> FloatArray:
    """Rate of change of box width and height in px/s, shape (L, 2), over `VELOCITY_WINDOW_S`."""
    sizes = np.stack([boxes[:, 2] - boxes[:, 0], boxes[:, 3] - boxes[:, 1]], axis=1)
    return slopes(sizes, hz, VELOCITY_WINDOW_S)


def slopes(values: FloatArray, hz: float, window_s: float) -> FloatArray:
    """Per-column least-squares slope per second over a centred window, NaN-aware.

    Args:
        values: Row-aligned series, shape (L, C); rows whose first column is NaN are absent.
        hz: Timeline rate.
        window_s: Length of the centred window.

    Returns:
        Slopes, shape (L, C); NaN where fewer than two samples in the window are present.
    """
    half = max(1, round(window_s * hz / 2))
    size, columns = values.shape
    sums = np.zeros((3, size))  # per sample: n, sum t, sum t^2
    s_x = np.zeros((size, columns))
    cross = np.zeros((size, columns))
    for offset in range(-half, half + 1):
        lo, hi = max(0, -offset), min(size, size - offset)
        if lo >= hi:
            continue
        shifted = values[lo + offset : hi + offset]
        ok = ~np.isnan(shifted[:, 0])
        t = offset / hz
        rows = slice(lo, hi)
        sums[0, rows] += ok
        sums[1, rows] += ok * t
        sums[2, rows] += ok * t * t
        s_x[rows] += np.where(ok[:, None], shifted, 0.0)
        cross[rows] += np.where(ok[:, None], shifted * t, 0.0)
    count, s_t, s_tt = sums[0], sums[1], sums[2]
    denom = count * s_tt - s_t * s_t
    good = (count >= _MIN_VELOCITY_SAMPLES) & (denom > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        slope = (count[:, None] * cross - s_t[:, None] * s_x) / denom[:, None]
    out: FloatArray = np.where(good[:, None], slope, np.nan)
    return out


def _carried(person_v: FloatArray, vehicle_v: FloatArray) -> BoolArray:
    """Strict co-motion: same direction (cosine) and similar speed."""
    p_speed, v_speed = norm(person_v), norm(vehicle_v)
    dot = person_v[:, 0] * vehicle_v[:, 0] + person_v[:, 1] * vehicle_v[:, 1]
    with np.errstate(divide="ignore", invalid="ignore"):
        cosine = dot / (p_speed * v_speed)
        gap = np.abs(p_speed - v_speed) / np.fmax(p_speed, v_speed)
    ok: BoolArray = (cosine >= MIN_COSINE) & (gap <= MAX_SPEED_GAP)
    return ok


@dataclass(frozen=True, eq=False)
class _PairKinematics:
    """Per-sample evidence for one (person, vehicle) pair over their common range."""

    both: BoolArray
    on_board: BoolArray
    vehicle_moving: BoolArray
    rigid: BoolArray
    carried: BoolArray
    co_moving: BoolArray


def _around(values: FloatArray, span: slice, pad: int) -> tuple[FloatArray, slice]:
    """`values` over `span` widened by `pad` samples each side, and `span` within that cut."""
    lo = max(0, span.start - pad)
    return values[lo : span.stop + pad], slice(span.start - lo, span.stop - lo)


def _observed(track: SampledTrack, values: FloatArray) -> FloatArray:
    """`values` with the held tail after the track's last point blanked (NaN).

    A track that ended is held for `HOLD_S` at its last position: that tail would read as a
    vehicle (or person) that stopped dead, so motion is estimated from real points only and the
    tail is never judged (a tracker re-identifying a vehicle ends its old track mid-ride).
    """
    out = values.copy()
    out[track.seen_stop - track.start :] = np.nan
    return out


def _seen(track: SampledTrack, span: slice) -> BoolArray:
    """Whether each sample of `span` (track indices) is at or before the track's last point."""
    index = np.arange(span.start, span.stop) + track.start
    seen: BoolArray = index < track.seen_stop
    return seen


def _kinematics(
    person: SampledTrack, vehicle: SampledTrack, own: slice, theirs: slice, hz: float
) -> _PairKinematics:
    both = person.present[own] & vehicle.present[theirs]
    p_box, v_box = person.box[own], vehicle.box[theirs]
    # Windowed estimates only need their neighbourhood, not the whole (maybe hours-long) track.
    pad = max(1, round(VELOCITY_WINDOW_S * hz / 2)) + SPEED_MEDIAN_SAMPLES
    p_cut, p_in = _around(_observed(person, person.box), own, pad)
    v_cut, v_in = _around(_observed(vehicle, vehicle.box), theirs, pad)
    m_cut, m_in = _around(_observed(vehicle, vehicle.foot_m), theirs, pad)
    p_vel = image_velocity(p_cut, hz)[p_in]
    v_vel = image_velocity(v_cut, hz)[v_in]
    reshape = np.abs(reshape_rate(v_cut, hz)[v_in])
    height = v_box[:, 3] - v_box[:, 1]
    ground = ground_speed(m_cut, hz)[m_in]
    carried = _carried(p_vel, v_vel)
    seen = _seen(person, own) & _seen(vehicle, theirs)
    with np.errstate(invalid="ignore"):
        vehicle_moving = (norm(v_vel) >= STILL_HEIGHTS_PER_S * height) | (ground >= MOVING_MPS)
        close = norm(p_vel - v_vel) <= MAX_RELATIVE_HEIGHTS_PER_S * height
        rigid = np.fmax(reshape[:, 0], reshape[:, 1]) <= MAX_RESHAPE_HEIGHTS_PER_S * height
    return _PairKinematics(
        both=both,
        on_board=both & (_containment(p_box, v_box) >= MIN_CONTAINMENT),
        vehicle_moving=both & seen & vehicle_moving,
        rigid=rigid,
        carried=carried,
        co_moving=carried | close,
    )


def _entered_on_foot(k: _PairKinematics, first: int, entry: int) -> bool:
    """Whether the person came into the boarding starting at `first` from outside, not carried.

    True when, in the `entry` samples before `first`, the person was off board beside the moving
    vehicle on at least half of them and carried on fewer than `MIN_COMOVING_FRACTION` of those.
    """
    before = slice(max(0, first - entry), first)
    outside = k.vehicle_moving[before] & ~k.on_board[before]
    count = int(outside.sum())
    if count < max(1, entry // 2):
        return False
    return bool((k.carried[before] & outside).sum() < MIN_COMOVING_FRACTION * count)


@dataclass(frozen=True)
class _WarmUp:
    """The person's warm-up in timeline samples: born-on-board boardings before `end` are held.

    Attributes:
        lo: The pair's first common timeline sample.
        end: One past the warm-up's last timeline sample (the person's first sample + warm-up).
        horizon: One past the window's last timeline sample.
    """

    lo: int
    end: int
    horizon: int

    def holds(self, k: _PairKinematics, first: int, last: int, agree: bool) -> bool:
        """Whether the boarding `[first, last]` (pair indices) is an operator pending or resolved.

        Only a boarding that starts at the pair's first common sample during the warm-up
        qualifies. It is held while the window ends inside the warm-up, and resolved as the
        operator when it ended inside the warm-up with agreeing (or no) judged samples.
        """
        start = self.lo + first
        if start >= self.end or first != int(np.argmax(k.both)):
            return False
        if self.horizon <= self.end:
            return True
        return self.lo + last < self.end and agree


def operator_mask(
    person: SampledTrack,
    vehicle: SampledTrack,
    hz: float,
    horizon: int,
    *,
    warmup_s: float = OPERATOR_WARMUP_S,
) -> BoolArray:
    """Samples of `person`'s range where the person is `vehicle`'s operator.

    Args:
        person: A person-like track.
        vehicle: A vehicle-like track.
        hz: Timeline rate.
        horizon: Number of timeline samples (one past the window's last sample index).
        warmup_s: The person's warm-up (rule 7); 0 turns it off.

    Returns:
        Boolean array aligned to `person`'s index range: True over every linked, pending or
        warm-up-held boarding where both tracks are present.
    """
    out = np.zeros(len(person.present), dtype=np.bool_)
    lo, hi = max(person.start, vehicle.start), min(person.stop, vehicle.stop)
    if lo >= hi:
        return out
    own = slice(lo - person.start, hi - person.start)
    k = _kinematics(person, vehicle, own, slice(lo - vehicle.start, hi - vehicle.start), hz)
    need = max(1, round(MIN_LINK_S * hz))
    entry = max(1, round(ENTRY_S * hz))
    warm = _WarmUp(lo=lo, end=person.start + round(warmup_s * hz), horizon=horizon)
    linked = np.zeros(len(k.on_board), dtype=np.bool_)
    for first, last in boardings(k.on_board, round(MAX_BOARDING_GAP_S * hz)):
        if _entered_on_foot(k, first, entry):
            continue
        run = slice(first, last + 1)
        judged = k.vehicle_moving[run] & k.rigid[run] & k.on_board[run]
        count = int(judged.sum())
        agree = bool((k.co_moving[run] & judged).sum() >= MIN_COMOVING_FRACTION * count)
        pending = 0 < count < need and lo + last == horizon - 1
        if (agree and (count >= need or pending)) or warm.holds(k, first, last, agree):
            linked[run] = True
    out[own] = linked & k.both
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
