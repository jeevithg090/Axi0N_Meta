"""One small check per construct, each with a known answer from the prover."""

from __future__ import annotations

import pytest

from rulecam.extensions.smt import ProofSpec, ProofStatus, PropertyName, prove_check
from tests.extensions.test_smt import run_in_process

SPEC = ProofSpec(target_concept="pallet", lines=("yellow",), zones={"aisle": 3}, max_objects=2)

PROVED = ProofStatus.PROVED
BROKEN = ProofStatus.COUNTEREXAMPLE
REFUSED = ProofStatus.UNSUPPORTED

TOTAL_CASES = [
    # (id, body of check(sample), expected `total` status, words expected in the detail)
    ("max-args", "return {'value': max(1, 2, 3.5) > 3 and min(4, 2) == 2}", PROVED, ""),
    (
        "max-default",
        "tops = [o['mask_top_y_px'] for o in sample['objects']]\n"
        "    return {'value': max(tops, default=-1) > 3}",
        PROVED,
        "",
    ),
    (
        "max-empty",
        "tops = [o['mask_top_y_px'] for o in sample['objects']]\n"
        "    return {'value': max(tops) > 3}",
        BROKEN,
        "ValueError",
    ),
    (
        "enumerate-start",
        "pairs = list(enumerate(sample['objects'], start=1))\n"
        "    return {'value': len(pairs) > 0 and pairs[0][0] == 1}",
        PROVED,
        "",
    ),
    (
        "str-bool-tuple",
        "return {'value': bool(tuple(sample['objects'])) or str(len(sample['lines'])) == '1'}",
        PROVED,
        "",
    ),
    (
        "attribute-on-number",
        "n = len(sample['objects'])\n    n.get\n    return {'value': True}",
        BROKEN,
        "AttributeError",
    ),
    (
        "method-on-none",
        "w = sample['frame_w']\n    return {'value': w.get('x') is None}",
        BROKEN,
        "AttributeError",
    ),
    (
        "get-default",
        "z = sample['zones'].get('dock', [])\n    return {'value': len(z) == 0}",
        PROVED,
        "",
    ),
    (
        "values-extend",
        "xs = []\n    xs.extend(sample['zones'].values())\n    return {'value': len(xs) == 1}",
        PROVED,
        "",
    ),
    (
        "zone-loop",
        "n = 0\n    for x, y in sample['zones']['aisle']:\n        n += 1 if x > y else 0\n"
        "    return {'value': n >= 2}",
        PROVED,
        "",
    ),
    (
        "sqrt-negative",
        "(ax, ay), (bx, by) = sample['lines']['yellow']\n"
        "    return {'value': math.sqrt(bx - ax) > 1}",
        BROKEN,
        "ValueError",
    ),
    (
        "power-half",
        "(ax, ay), (bx, by) = sample['lines']['yellow']\n"
        "    return {'value': ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5 > 0}",
        PROVED,
        "",
    ),
    ("float-text", "return {'value': float('1.5') + int('2') == 3.5}", PROVED, ""),
    ("bad-int-text", "return {'value': int('x') == 1}", BROKEN, "ValueError"),
    ("undefined-name", "return {'value': missing > 1}", BROKEN, "NameError"),
    ("helper-arity", "return {'value': helper(1, 2)}", BROKEN, "TypeError"),
    ("value-not-bool", "return {'value': None}", BROKEN, "InvalidResult"),
    ("extra-key", "return {'value': False, 'note': 1}", BROKEN, "InvalidResult"),
    ("not-a-dict", "return [False]", BROKEN, "InvalidResult"),
    ("falls-off", "pass", BROKEN, "InvalidResult"),
    (
        "why-too-long",
        "return {'value': False, 'why': '" + "x" * 130 + "'}",
        BROKEN,
        "InvalidResult",
    ),
    (
        "unpack-wrong",
        "a, b, c = sample['lines']['yellow']\n    return {'value': False}",
        BROKEN,
        "ValueError",
    ),
    (
        "format-none",
        "s = sample['px_per_m']\n    return {'value': False, 'why': f'{s:.1f} px'}",
        BROKEN,
        "TypeError",
    ),
    (
        "isinstance-types",
        "o = sample['objects']\n"
        "    return {'value': isinstance(o, list) and not isinstance(sample, (str, tuple))}",
        PROVED,
        "",
    ),
    (
        "chained-none",
        "w = sample['frame_w']\n    return {'value': w is not None and 0 < w < 10}",
        PROVED,
        "",
    ),
    ("none-compare", "return {'value': sample['frame_w'] < 5}", BROKEN, "TypeError"),
    (
        "dict-in",
        "return {'value': 'yellow' in sample['lines'] and 'red' not in sample['lines']}",
        PROVED,
        "",
    ),
    (
        "store-and-slice",
        "d = {'a': 1}\n    d['b'] = 2\n    xs = [1, 2, 3]\n    xs[0] = 5\n"
        "    return {'value': d['b'] + xs[0] == 7 and xs[-2:] == [2, 3]}",
        PROVED,
        "",
    ),
    ("index-out", "xs = [1]\n    xs[3] = 2\n    return {'value': True}", BROKEN, "IndexError"),
    (
        "global-read-not-local",
        "if False:\n        TABLE['b'] = 2\n    return {'value': TABLE['a'] == 1}",
        PROVED,
        "",
    ),
    (
        "iterate-none",
        "objs = sample['objects']\n    g = objs[0]['ground_xy_m'] if objs else None\n"
        "    n = 0\n    for v in g:\n        n += 1\n    return {'value': n > 0}",
        BROKEN,
        "TypeError",
    ),
    (
        "format-text-spec",
        "name = 'pallet'\n    return {'value': False, 'why': f'{name:>8}'}",
        PROVED,
        "",
    ),
    (
        "why-concept",
        "objs = sample['objects']\n"
        "    return {'value': False, 'why': objs[0]['concept'] if objs else 'none'}",
        PROVED,
        "",
    ),
    (
        "concept-len",
        "return {'value': all(len(o['concept']) > 0 for o in sample['objects'])}",
        PROVED,
        "",
    ),
]

REFUSED_CASES = [
    ("power-symbolic", "x = sample['px_per_m'] or 1\n    return {'value': 2 ** x > 1}", "exponent"),
    ("list-sort", "xs = [1]\n    xs.sort()\n    return {'value': True}", "list.sort"),
    ("power-big", "return {'value': 2 ** 9 > 1}", "exponent"),
    ("long-range", "return {'value': len(range(1000)) > 0}", "range longer"),
    ("split", "return {'value': len('a b'.split()) == 2}", "str.split"),
    ("max-key", "return {'value': max([1], key=abs) == 1}", "max"),
    ("isinstance-non-type", "return {'value': isinstance(1, len)}", "isinstance"),
    ("computed-truth", "s = f'{len(sample)}'\n    return {'value': bool(s)}", "computed string"),
    ("dict-compare", "return {'value': sample['zones'] == {}}", "comparing dicts"),
    ("recursion", "return {'value': loop(1)}", "too deep"),
    ("global-append", "ITEMS.append(1)\n    return {'value': True}", "local list"),
    ("math-inf", "return {'value': 1 < math.inf}", "math.inf"),
    ("global-store", "TABLE['b'] = 2\n    return {'value': True}", "module-level"),
]

HELPERS = """
import math

ITEMS = []
TABLE = {"a": 1}


def helper(x):
    return {"value": x > 0}


def loop(n):
    return loop(n + 1)


def check(sample):
    {body}
"""


def _code(body: str) -> str:
    return HELPERS.replace("{body}", body)


@pytest.mark.parametrize(
    ("body", "status", "words"),
    [pytest.param(body, status, words, id=name) for name, body, status, words in TOTAL_CASES],
)
def test_totality_of_each_construct(body: str, status: ProofStatus, words: str) -> None:
    report = prove_check(_code(body), SPEC, runner=run_in_process)

    total = report.result(PropertyName.TOTAL)
    assert total is not None
    assert total.status is status, total.detail
    assert words in total.detail
    if status is BROKEN:
        assert total.confirmed is True


@pytest.mark.parametrize(
    ("body", "words"),
    [pytest.param(body, words, id=name) for name, body, words in REFUSED_CASES],
)
def test_constructs_outside_the_encoding_are_refused(body: str, words: str) -> None:
    report = prove_check(_code(body), SPEC)

    total = report.result(PropertyName.TOTAL)
    assert total is not None
    assert total.status is REFUSED
    assert words in total.detail


def test_top_level_code_that_fails_at_import_is_refused() -> None:
    code = (
        "LIMIT = max(1, 2) if 1 > 0 else len([])\n"
        "X = LIMIT.get\n\n"
        "def check(sample):\n"
        "    return {'value': True}\n"
    )

    report = prove_check(code, SPEC)

    assert all(p.status is REFUSED for p in report.properties)


def test_a_constant_cannot_call_a_function_defined_below_it() -> None:
    code = (
        "X = later(1)\n\n"
        "def later(x):\n"
        "    return x\n\n"
        "def check(sample):\n"
        "    return {'value': X == 1}\n"
    )

    report = prove_check(code, SPEC)

    assert all(p.status is REFUSED for p in report.properties)


def test_path_budget_ends_in_timeout_not_proof() -> None:
    body = "\n    ".join(
        f"x{i} = 1 if sample['objects'] and sample['objects'][0]['mask_top_y_px'] > {i} else 0"
        for i in range(14)
    )
    code = _code(body + "\n    return {'value': False}")
    report = prove_check(code, SPEC.model_copy(update={"max_paths": 10}))

    total = report.result(PropertyName.TOTAL)
    assert total is not None
    assert total.status is ProofStatus.TIMEOUT
    assert "paths" in total.detail
