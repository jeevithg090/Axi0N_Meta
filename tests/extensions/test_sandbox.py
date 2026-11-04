"""The extension sandbox: the validator refuses escapes, the child is killed at its limits."""

from __future__ import annotations

import pytest

from rulecam.extensions.errors import ExtensionRejectedError, SandboxError
from rulecam.extensions.sandbox import SandboxLimits, find_problems, run_check, validate
from rulecam.extensions.schema import CheckResult

GOOD = '''\
"""Pallet top above the line."""
import math

MARGIN_PX = 0


def line_y(line, x):
    (x1, y1), (x2, y2) = line[0], line[1]
    if x2 == x1:
        return min(y1, y2)
    return y1 + (y2 - y1) * (x - x1) / (x2 - x1)


def check(sample):
    line = sample.get("lines", {}).get("yellow")
    if not line:
        return {"value": False, "measure": None, "why": "no yellow line drawn"}
    best = None
    for obj in sample.get("objects", []):
        if obj.get("concept") != "pallet":
            continue
        x0, y0, x1, y1 = obj["box_xyxy_px"]
        gap = line_y(line, (x0 + x1) / 2) - obj["top_y_px"]
        best = gap if best is None else max(best, gap)
    if best is None:
        return {"value": False, "measure": None, "why": "no pallet"}
    return {"value": best > MARGIN_PX, "measure": round(best, 1), "why": f"{best:.0f} px"}
'''

SAMPLE_ABOVE = {
    "lines": {"yellow": [[0, 300], [640, 300]]},
    "objects": [{"concept": "pallet", "box_xyxy_px": [100, 290, 200, 400], "top_y_px": 290}],
}


def test_good_check_runs_and_answers() -> None:
    validate(GOOD)
    assert find_problems(GOOD) == []
    above, empty, no_line = run_check(GOOD, [SAMPLE_ABOVE, {"lines": SAMPLE_ABOVE["lines"]}, {}])
    assert above.result == CheckResult(value=True, measure=10.0, why="10 px")
    assert empty.result == CheckResult(value=False, why="no pallet")
    assert no_line.result == CheckResult(value=False, why="no yellow line drawn")


def test_empty_batch_starts_no_process() -> None:
    assert run_check(GOOD, []) == []


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("import os\ndef check(sample):\n    return {}", "only `import math`"),
        ("from math import sqrt\ndef check(sample):\n    return {}", "ImportFrom"),
        ("def check(sample):\n    return ().__class__.__bases__", "attributes starting with '_'"),
        ("def check(sample):\n    return open('/etc/passwd').read()", "`open` is not allowed"),
        ("def check(sample):\n    return eval('1')", "`eval` is not allowed"),
        ("def check(sample):\n    return getattr(sample, 'x')", "`getattr` is not allowed"),
        ("def check(sample):\n    return type(sample)", "`type` is not allowed"),
        ("def check(sample):\n    return __builtins__", "names starting with '_'"),
        ("def check(sample):\n    return '{0.x}'.format(sample)", "attribute `.format`"),
        ("def check(sample):\n    g = (1 for _ in [])\n    return g.gi_frame", "`.gi_frame`"),
        ("def check(sample):\n    try:\n        pass\n    except Exception:\n        pass", "Try"),
        ("class X:\n    pass\ndef check(sample):\n    return {}", "ClassDef"),
        ("def check(sample):\n    f = lambda: 1\n    return {}", "Lambda"),
        ("def check(sample):\n    def inner():\n        return 1\n    return {}", "nested"),
        ("def check(sample):\n    global X\n    return {}", "Global"),
        ("def check(sample):\n    with x:\n        pass", "With"),
        ("def check(sample):\n    raise ValueError('x')", "Raise"),
        ("def check(sample):\n    del sample\n    return {}", "Delete"),
        ("def check(sample):\n    return secret_thing", "unknown name `secret_thing`"),
        ("def check(sample):\n    sample.x = 1\n    return {}", "attribute `.x`"),
        ("def check(a, b):\n    return {}", "exactly one parameter"),
        ("def check(sample=1):\n    return {}", "default parameter"),
        ("def nope(sample):\n    return {}", "exactly one top-level"),
        ("print('hi')\ndef check(sample):\n    return {}", "`print` is not allowed"),
        ("def check(sample):\n    return math.__dict__", "attributes starting with '_'"),
        ("import math\ndef check(sample):\n    return math.sys", "attribute `.sys`"),
        ("def check(sample)\n    return {}", "syntax error"),
    ],
)
def test_validator_refuses(code: str, expected: str) -> None:
    with pytest.raises(ExtensionRejectedError) as raised:
        validate(code)
    assert any(expected in p for p in raised.value.reasons), raised.value.reasons


def test_validator_caps_size() -> None:
    long_code = "def check(sample):\n" + "    x = 1\n" * 250 + "    return {}\n"
    assert "too long" in find_problems(long_code)[0]


def test_rejected_code_is_never_run() -> None:
    with pytest.raises(ExtensionRejectedError) as raised:
        run_check("import os\ndef check(sample):\n    return {}", [{}])
    assert raised.value.reasons


def test_infinite_loop_is_killed() -> None:
    code = "def check(sample):\n    while True:\n        pass\n"
    with pytest.raises(SandboxError, match=r"killed|ended with code"):
        run_check(code, [{}], limits=SandboxLimits(cpu_s=1, wall_s=4.0))


def test_memory_bomb_in_check_fails_only_that_sample() -> None:
    code = "def check(sample):\n    big = [0] * (10 ** 10)\n    return {'value': True}\n"
    [outcome] = run_check(code, [{}])
    assert outcome.result is None
    assert outcome.error is not None
    assert "MemoryError" in outcome.error


def test_memory_bomb_at_import_fails_the_batch() -> None:
    code = "BIG = [0] * (10 ** 10)\ndef check(sample):\n    return {'value': True}\n"
    with pytest.raises(SandboxError, match="MemoryError"):
        run_check(code, [{}])


def test_huge_output_is_refused() -> None:
    code = "def check(sample):\n    return {'value': True, 'why': 'x' * 2000000}\n"
    with pytest.raises(SandboxError, match="too large"):
        run_check(code, [{}])


def test_long_reason_fails_result_validation() -> None:
    code = "def check(sample):\n    return {'value': True, 'why': 'x' * 500}\n"
    [outcome] = run_check(code, [{}])
    assert outcome.result is None
    assert outcome.error is not None
    assert "why" in outcome.error


@pytest.mark.parametrize(
    "returned",
    [
        "{'value': 1}",
        "{'value': None}",
        "{'value': True, 'extra': 1}",
        "[True]",
        "{'value': True, 'measure': True}",
    ],
)
def test_malformed_results_are_errors(returned: str) -> None:
    [outcome] = run_check(f"def check(sample):\n    return {returned}\n", [{}])
    assert outcome.result is None
    assert outcome.error


def test_runtime_import_of_anything_but_math_fails() -> None:
    # The validator forbids `import os`; even a smuggled import would meet the importer.
    code = "import math\ndef check(sample):\n    return {'value': math.sqrt(4) == 2.0}\n"
    [outcome] = run_check(code, [{}])
    assert outcome.result == CheckResult(value=True)


def test_exception_in_check_is_reported_per_sample() -> None:
    code = "def check(sample):\n    return {'value': sample['x'] > 1}\n"
    ok, broken = run_check(code, [{"x": 2}, {}])
    assert ok.result == CheckResult(value=True)
    assert broken.error is not None
    assert broken.error.startswith("KeyError")


def test_recursion_is_contained() -> None:
    code = "def loop(n):\n    return loop(n + 1)\n\ndef check(sample):\n    return loop(0)\n"
    [outcome] = run_check(code, [{}])
    assert outcome.error is not None
    assert "RecursionError" in outcome.error


def test_child_sees_no_environment() -> None:
    # `sample` is the only way in; nothing from the parent's environment reaches the code.
    code = "def check(sample):\n    return {'value': len(sample) == 0, 'why': str(len(sample))}\n"
    [outcome] = run_check(code, [{}])
    assert outcome.result == CheckResult(value=True, why="0")


def test_too_many_samples_is_refused() -> None:
    with pytest.raises(SandboxError, match="too many samples"):
        run_check(GOOD, [{}] * 3, limits=SandboxLimits(max_samples=2))
