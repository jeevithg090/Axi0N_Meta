"""The symbolic executor agrees with Python: a differential test on random concrete samples.

For each random sample, exactly one symbolic path must match it, and that path's verdict (or
failure) must be what running the check in Python gives. This is the evidence that the encoding
behind every proof is exact.
"""

from __future__ import annotations

import random
from typing import Any

import pytest
import z3

from rulecam.extensions.smt_exec import Executor, parse_program
from rulecam.extensions.smt_sample import (
    ProofSpec,
    SymSample,
    concept_universe,
    fresh_sample,
    to_value,
    validity,
)
from tests.extensions.test_smt import run_in_process

PROGRAM = """
import math

LIMIT = 3


def centre(box):
    x1, y1, x2, y2 = box
    return (x1 + x2) / 2, (y1 + y2) / 2


def check(sample):
    objs = sample["objects"]
    first = objs[0]["mask_area_px"] / objs[0]["mask_top_y_px"] if objs else 0
    pallets = [o for o in objs if o["concept"].lower() == "pallet"]
    heights = []
    for i, o in enumerate(objs):
        if o["concept"] == "other":
            continue
        cx, cy = centre(o["box_xyxy_px"])
        if cx > 4:
            break
        heights.append(cy + i)
    scores = [o["score"] for o in objs if o.get("score") is not None]
    best = max(scores, default=0)
    points = [o["ground_xy_m"] for o in objs if o["ground_xy_m"] is not None]
    near = any(math.hypot(p[0], p[1]) <= 2 for p in points)
    width = sample["frame_w"]
    wide = width is not None and 2 < width <= 5
    (ax, ay), (bx, by) = sample["lines"]["yellow"]
    flat = ay == by
    span = abs(bx - ax) if not flat else 0
    count = len(pallets) + sum(1 for h in heights if h >= 1)
    if (n := len(objs)) > LIMIT:
        return {"value": True, "why": "crowded"}
    ratio = span / (count - 1) if count != 1 else None
    value = (near or wide) and not flat or (best >= 0.75 and isinstance(width, int))
    return {"value": value, "measure": ratio, "why": f"{n} objects, first {first}"}
"""

UNBOUND = """
def check(sample):
    for o in sample["objects"]:
        last = o["mask_top_y_px"]
    return {"value": last > 2}
"""

MINMAX = """
def check(sample):
    tops = [o["mask_top_y_px"] for o in sample["objects"]]
    low, high = min(tops), max(tops)
    total = sum(tops, 1)
    return {"value": high - low >= 2 and total > 5, "measure": round(total / 2, 1)}
"""

BUILTINS = """
import math


def check(sample):
    objs = sample["objects"]
    names = [o["concept"].upper().strip() for o in objs]
    tags = [n for n in names if n.startswith("PAL") or n.endswith("ER")]
    kinds = sum(1 for o in objs if o["concept"] in ("pallet", "box"))
    kinds += sum(1 for o in objs if "al" in o["concept"])
    sizes = [len(o["concept"]) for o in objs]
    first = objs[0] if objs else None
    box = first["box_xyxy_px"] if first is not None else [0.0, 0.0, 1.0, 1.0]
    x1, y1 = box[:2]
    w, h = box[2] - x1, box[3] - y1
    pts = list(zip(box[0::2], box[1::2]))
    d = math.dist(pts[0], pts[1])
    area = abs(w * h) + math.fabs(-w) ** 2
    grid = math.floor(x1 / 2) + math.ceil(y1 / 2) - math.trunc(-w / 3)
    info = dict(n=len(objs), d=d)
    keys = list(info.keys()) + [k for k, _ in info.items()]
    total = sum(info.values(), 0.5)
    ok = isinstance(info["n"], int) and not isinstance(w, int) and isinstance(pts[0], tuple)
    counts = [0, 0, 0]
    for i in range(len(objs)):
        counts[i] = int(objs[i]["mask_area_px"])
    counts[-1] += 1
    flag = all([d >= 0, math.isfinite(d), not math.isnan(d)]) and any([kinds > 0, grid > 1])
    score = first["score"] if first is not None else None
    level = "hi" if score is not None and score >= 0.5 else ("lo" if score is not None else "-")
    value = (ok and flag and 0 <= area < 40 and level != "lo") or (
        len(tags) + len(keys) > 5 and sizes[0] > 4
    )
    return {"value": value, "measure": total + float(counts[-1]) - grid, "why": level + "!"}
"""

FAILS = """
def check(sample):
    objs = sample["objects"]
    if len(objs) == 1 and objs[0]["mask_top_y_px"] > 3:
        return {"value": objs[1]["score"] > 0}
    if len(objs) == 2 and objs[0]["ground_xy_m"] is None:
        return {"value": objs[0]["ground_xy_m"][0] > 1}
    if objs and objs[0]["mask_area_px"] > 3:
        return {"value": sample["lines"]["red"] is None}
    if objs and objs[0]["mask_area_px"] > 1:
        return {"value": True, "measure": objs[0]["mask_area_px"] > 2}
    return {"value": -objs[0]["box_xyxy_px"][0] < 0 if objs else not objs}
"""

NONES = """
def check(sample):
    objs = sample["objects"]
    if len(objs) < 2:
        return {"value": False}
    a, b = objs[0], objs[1]
    same = a["score"] == b["score"] and a["ground_xy_m"] != b["ground_xy_m"]
    return {"value": same}
"""

SPEC = ProofSpec(target_concept="pallet", lines=("yellow",), max_paths=20_000)
VALUES = (0, 0.5, 1, 2, 3, 4, 5, 6)


def _point(rng: random.Random) -> list[float]:
    return [float(rng.choice(VALUES)), float(rng.choice(VALUES))]


def _random_sample(rng: random.Random, n: int, concepts: tuple[str, ...]) -> dict[str, Any]:
    objects = []
    for _ in range(n):
        x1, x2 = sorted(rng.choice(VALUES) for _ in range(2))
        y1, y2 = sorted(rng.choice(VALUES) for _ in range(2))
        top = rng.choice([v for v in VALUES if y1 <= v <= y2] or [y1])
        objects.append(
            {
                "concept": rng.choice(concepts),
                "box_xyxy_px": [float(x1), float(y1), float(x2), float(y2)],
                "mask_top_y_px": float(top),
                "mask_area_px": float(rng.choice(VALUES)),
                "ground_xy_m": _point(rng) if rng.random() < 0.6 else None,
                "score": rng.choice([0.0, 0.25, 0.5, 0.75, 1.0]) if rng.random() < 0.7 else None,
            }
        )
    a = _point(rng)
    b = _point(rng)
    while b == a:
        b = _point(rng)
    return {
        "timestamp": "2026-11-03T12:00:00Z",
        "frame_w": rng.randint(1, 7) if rng.random() < 0.7 else None,
        "frame_h": None,
        "objects": objects,
        "zones": {},
        "lines": {"yellow": [a, b]},
        "px_per_m": None,
    }


def _bindings(sym: SymSample, sample: dict[str, Any]) -> list[tuple[Any, Any]]:
    pairs: list[tuple[Any, Any]] = []
    for obj, raw in zip(sym.objects, sample["objects"], strict=True):
        pairs.append((obj.concept, z3.IntVal(sym.concepts.index(raw["concept"]))))
        pairs.extend(
            (term, z3.RealVal(v)) for term, v in zip(obj.box, raw["box_xyxy_px"], strict=True)
        )
        pairs.extend(
            [
                (obj.mask_top, z3.RealVal(raw["mask_top_y_px"])),
                (obj.mask_area, z3.RealVal(raw["mask_area_px"])),
            ]
        )
        ground = raw["ground_xy_m"] or [0.0, 0.0]
        pairs.extend(
            [
                (obj.has_ground, z3.BoolVal(raw["ground_xy_m"] is not None)),
                (obj.ground[0], z3.RealVal(ground[0])),
                (obj.ground[1], z3.RealVal(ground[1])),
            ]
        )
        score = raw["score"] if raw["score"] is not None else 0.0
        pairs.extend(
            [(obj.has_score, z3.BoolVal(raw["score"] is not None)), (obj.score, z3.RealVal(score))]
        )
    width = sample["frame_w"]
    pairs.extend(
        [(sym.has_frame_w, z3.BoolVal(width is not None)), (sym.frame_w, z3.RealVal(width or 1))]
    )
    pairs.extend([(sym.has_frame_h, z3.BoolVal(False)), (sym.frame_h, z3.RealVal(1))])
    pairs.extend([(sym.has_px_per_m, z3.BoolVal(False)), (sym.px_per_m, z3.RealVal(1))])
    for (_, (p, q)), raw_line in zip(sym.lines, sample["lines"].values(), strict=True):
        for term, value in zip((*p, *q), (*raw_line[0], *raw_line[1]), strict=True):
            pairs.append((term, z3.RealVal(value)))
    return pairs


def _holds(term: Any, pairs: list[tuple[Any, Any]]) -> bool:
    """Whether `term` can hold under the concrete bindings (fresh sqrt/round variables free)."""
    concrete = z3.simplify(z3.substitute(term, *pairs))
    if z3.is_true(concrete) or z3.is_false(concrete):
        return bool(z3.is_true(concrete))
    solver = z3.Solver()
    solver.add(concrete)
    return bool(solver.check() == z3.sat)


# The rich program forks into ~13k paths with two objects (~8 min here), so CI keeps it at 0-1.
CASES = [
    *[pytest.param(PROGRAM, n, id=f"rich-{n}") for n in (0, 1)],
    *[pytest.param(UNBOUND, n, id=f"unbound-{n}") for n in (0, 1, 2)],
    *[pytest.param(MINMAX, n, id=f"minmax-{n}") for n in (0, 1, 2)],
    pytest.param(NONES, 2, id="nones-2"),
    *[pytest.param(BUILTINS, n, id=f"builtins-{n}") for n in (0, 1, 2)],
    *[pytest.param(FAILS, n, id=f"fails-{n}") for n in (0, 1, 2)],
]


@pytest.mark.parametrize(("code", "n"), CASES)
def test_symbolic_paths_match_python_on_random_samples(code: str, n: int) -> None:
    program = parse_program(code)
    concepts = concept_universe(SPEC, program.literals)
    sym = fresh_sample(n, SPEC, concepts, "d_")
    outcomes = Executor(program, validity(sym), max_paths=SPEC.max_paths).run(to_value(sym))
    rng = random.Random(f"{code[:40]}-{n}")  # noqa: S311 - reproducible test data
    samples = [_random_sample(rng, n, concepts) for _ in range(40)]
    concrete = run_in_process(code, samples)
    approximated = "round(" in code

    for sample, real in zip(samples, concrete, strict=True):
        pairs = _bindings(sym, sample)
        matching = [o for o in outcomes if _holds(z3.And(True, *o.pc), pairs)]
        if approximated:
            assert matching, sample
            continue
        assert len(matching) == 1, (sample, len(matching))
        (path,) = matching
        if real.result is None:
            assert path.failure is not None, (sample, real.error)
            continue
        assert path.failure is None, (sample, path.failure)
        assert _holds(z3.And(*path.pc, path.value), pairs) == real.result.value, sample
