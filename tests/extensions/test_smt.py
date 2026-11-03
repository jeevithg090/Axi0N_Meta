"""The Z3 math proof of model-written checks (Muse Code add-on 7)."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import pytest

from rulecam.extensions.smt import (
    CounterexampleExplanation,
    MonotoneSpec,
    ProofSpec,
    ProofStatus,
    PropertyName,
    SmtReport,
    counterexample_image_prompt,
    explain_counterexample,
    prove_check,
)
from tests.fakes import FakeLLM

RAISE_PALLET = MonotoneSpec(
    fields=("y1", "y2", "mask_top_y"), direction="decrease", label="raising a pallet"
)
SPEC = ProofSpec(target_concept="pallet", lines=("yellow",), monotone=RAISE_PALLET, max_objects=2)

CORRECT = '''
import math

MARGIN_PX = 0


def check(sample):
    """Alert when the top of any pallet is above the yellow line."""
    (ax, ay), (bx, by) = sample["lines"]["yellow"]
    if bx < ax:
        ax, ay, bx, by = bx, by, ax, ay
    if bx == ax:
        return {"value": False, "measure": None, "why": "the yellow line is vertical"}
    worst = 0.0
    found = False
    for obj in sample["objects"]:
        if obj["concept"] != "pallet":
            continue
        x1, y1, x2, y2 = obj["box_xyxy_px"]
        cx = (x1 + x2) / 2
        top = obj["mask_top_y_px"]
        cross = (bx - ax) * (top - ay) - (by - ay) * (cx - ax)
        if cross < -MARGIN_PX:
            found = True
            worst = min(worst, cross)
    if found:
        return {"value": True, "measure": -worst, "why": "a pallet is above the yellow line"}
    return {"value": False, "measure": None, "why": "every pallet is below the yellow line"}
'''

NO_CONCEPT_FILTER = CORRECT.replace(
    """        if obj["concept"] != "pallet":
            continue
""",
    "",
)

SLOPE = """
def check(sample):
    (ax, ay), (bx, by) = sample["lines"]["yellow"]
    slope = (by - ay) / (bx - ax)
    for obj in sample["objects"]:
        if obj["concept"] == "pallet":
            x1, y1, x2, y2 = obj["box_xyxy_px"]
            cx = (x1 + x2) / 2
            line_y = ay + slope * (cx - ax)
            if obj["mask_top_y_px"] < line_y:
                return {"value": True, "measure": line_y - obj["mask_top_y_px"], "why": "above"}
    return {"value": False, "measure": None, "why": "below"}
"""

ABSOLUTE = """
def check(sample):
    pallets = [o for o in sample["objects"] if o["concept"] == "pallet"]
    high = any(o["mask_top_y_px"] < 300 for o in pallets)
    return {"value": high, "measure": None, "why": f"{len(pallets)} pallets checked"}
"""

INVERTED = CORRECT.replace("if cross < -MARGIN_PX:", "if cross > MARGIN_PX:")

SORTED = """
def check(sample):
    pallets = sorted(sample["objects"], key=lambda o: o["mask_top_y_px"])
    return {"value": bool(pallets), "measure": None, "why": "sorted"}
"""


@dataclass(frozen=True)
class _Result:
    value: bool


@dataclass(frozen=True)
class _Outcome:
    result: _Result | None
    error: str | None = None


_SAFE = (
    "abs", "all", "any", "bool", "dict", "enumerate", "float", "int", "isinstance", "len",
    "list", "max", "min", "range", "round", "sorted", "str", "sum", "tuple", "zip",
)  # fmt: skip


def run_in_process(code: str, samples: Sequence[Mapping[str, object]]) -> list[_Outcome]:
    """Tests only: run a trusted test check in-process and validate the result's shape."""
    builtins = {
        name: __builtins__[name] if isinstance(__builtins__, dict) else getattr(__builtins__, name)
        for name in _SAFE
    }
    builtins["__import__"] = lambda name, *_: math
    scope: dict[str, object] = {"__builtins__": builtins}
    exec(compile(code, "<check>", "exec"), scope)  # noqa: S102 - trusted test fixtures only
    out: list[_Outcome] = []
    for sample in samples:
        try:
            answer = scope["check"](json.loads(json.dumps(sample)))  # type: ignore[operator]
        except Exception as exc:
            out.append(_Outcome(None, type(exc).__name__))
            continue
        ok = _valid_result(answer)
        out.append(_Outcome(_Result(answer["value"]) if ok else None, None if ok else "invalid"))
    return out


def _valid_result(answer: object) -> bool:
    """The same shape rules as `schema.CheckResult` (strict bool, numeric measure, short why)."""
    if not isinstance(answer, dict) or not set(answer) <= {"value", "measure", "why"}:
        return False
    measure = answer.get("measure")
    why = answer.get("why", "")
    return (
        type(answer.get("value")) is bool
        and (measure is None or (type(measure) in {int, float} and math.isfinite(measure)))
        and isinstance(why, str)
        and len(why) <= 120
    )


def _status(report: SmtReport, name: PropertyName) -> ProofStatus:
    result = report.result(name)
    assert result is not None
    return result.status


def test_correct_check_proves_all_four_properties() -> None:
    report = prove_check(CORRECT, SPEC, runner=run_in_process)

    assert [p.name for p in report.properties] == list(PropertyName)
    assert report.all_proved, [p.detail for p in report.properties]
    assert report.max_objects == 2
    assert report.z3_version
    assert all("up to 2 objects" in p.detail for p in report.properties)


def test_alerting_without_a_pallet_is_a_confirmed_counterexample() -> None:
    report = prove_check(NO_CONCEPT_FILTER, SPEC, runner=run_in_process)

    result = report.result(PropertyName.NO_TARGET_NO_ALERT)
    assert result is not None
    assert result.status is ProofStatus.COUNTEREXAMPLE
    assert result.confirmed is True
    (sample,) = result.samples
    assert sample["objects"]
    assert all(o["concept"] != "pallet" for o in sample["objects"])
    assert _status(report, PropertyName.TOTAL) is ProofStatus.PROVED


def test_dividing_by_the_line_slope_breaks_totality_on_a_vertical_line() -> None:
    report = prove_check(SLOPE, SPEC, runner=run_in_process)

    result = report.result(PropertyName.TOTAL)
    assert result is not None
    assert result.status is ProofStatus.COUNTEREXAMPLE
    assert "ZeroDivisionError" in result.detail
    assert result.confirmed is True
    (a, b) = result.samples[0]["lines"]["yellow"]
    assert a[0] == b[0]


def test_absolute_pixel_threshold_breaks_translation_invariance() -> None:
    report = prove_check(ABSOLUTE, SPEC, runner=run_in_process)

    result = report.result(PropertyName.TRANSLATION_INVARIANT)
    assert result is not None
    assert result.status is ProofStatus.COUNTEREXAMPLE
    assert result.confirmed is True
    before, after = result.samples
    assert before["objects"][0]["mask_top_y_px"] != after["objects"][0]["mask_top_y_px"]
    assert _status(report, PropertyName.NO_TARGET_NO_ALERT) is ProofStatus.PROVED
    assert _status(report, PropertyName.TOTAL) is ProofStatus.PROVED


def test_inverted_comparison_breaks_monotonicity_only() -> None:
    report = prove_check(INVERTED, SPEC, runner=run_in_process)

    result = report.result(PropertyName.MONOTONE)
    assert result is not None
    assert result.status is ProofStatus.COUNTEREXAMPLE
    assert result.confirmed is True
    assert "raising a pallet" in result.detail
    before, after = result.samples
    assert after["objects"] != before["objects"]
    for name in (
        PropertyName.NO_TARGET_NO_ALERT,
        PropertyName.TOTAL,
        PropertyName.TRANSLATION_INVARIANT,
    ):
        assert _status(report, name) is ProofStatus.PROVED


def test_unsupported_construct_is_never_reported_as_proved() -> None:
    report = prove_check(SORTED, SPEC)

    assert {p.status for p in report.properties} == {ProofStatus.UNSUPPORTED}
    assert all("sorted" in p.detail for p in report.properties)
    assert not report.all_proved


@pytest.mark.parametrize(
    ("code", "construct"),
    [
        ("def check(sample):\n    while True:\n        pass\n", "while loop"),
        ("def check(sample):\n    return {'value': 3 // 2 == 1}\n", "floor division"),
        ("def check(sample):\n    return {'value': math.sin(1.0) > 0}\n", "math.sin"),
        ("def helper(x):\n    return x\n", "no `def check(sample)`"),
        ("def check(sample:\n", "does not parse"),
    ],
)
def test_constructs_the_prover_refuses(code: str, construct: str) -> None:
    report = prove_check(code, ProofSpec(target_concept="pallet"))

    assert all(p.status is ProofStatus.UNSUPPORTED for p in report.properties)
    assert all(construct in p.detail for p in report.properties)


def test_invalid_result_shape_breaks_totality() -> None:
    code = "def check(sample):\n    return {'value': len(sample['objects'])}\n"
    report = prove_check(code, ProofSpec(target_concept="pallet"), runner=run_in_process)

    result = report.result(PropertyName.TOTAL)
    assert result is not None
    assert result.status is ProofStatus.COUNTEREXAMPLE
    assert "InvalidResult" in result.detail
    assert result.confirmed is True


def test_none_handling_is_tracked_through_optional_fields() -> None:
    unsafe = (
        "def check(sample):\n"
        "    for o in sample['objects']:\n"
        "        if o['score'] > 0.5:\n"
        "            return {'value': True}\n"
        "    return {'value': False}\n"
    )
    safe = unsafe.replace("if o['score'] > 0.5", "if o['score'] is not None and o['score'] > 0.5")
    spec = ProofSpec(target_concept="pallet", translation=False)

    unsafe_total = prove_check(unsafe, spec, runner=run_in_process).result(PropertyName.TOTAL)
    safe_total = prove_check(safe, spec, runner=run_in_process).result(PropertyName.TOTAL)

    assert unsafe_total is not None
    assert unsafe_total.status is ProofStatus.COUNTEREXAMPLE
    assert "TypeError" in unsafe_total.detail
    assert safe_total is not None
    assert safe_total.status is ProofStatus.PROVED


def test_sqrt_distance_is_encoded_exactly() -> None:
    code = (
        "import math\n\n"
        "def check(sample):\n"
        "    for o in sample['objects']:\n"
        "        g = o['ground_xy_m']\n"
        "        if o['concept'] == 'pallet' and g is not None and math.hypot(g[0], g[1]) < 2:\n"
        "            return {'value': True, 'measure': math.hypot(g[0], g[1])}\n"
        "    return {'value': False}\n"
    )
    report = prove_check(code, ProofSpec(target_concept="pallet", max_objects=1))

    assert report.all_proved, [p.detail for p in report.properties]


def test_round_is_reported_as_an_approximation() -> None:
    code = (
        "def check(sample):\n"
        "    n = len(sample['objects'])\n"
        "    return {'value': False, 'measure': round(n * 1.5, 1), 'why': 'count'}\n"
    )
    report = prove_check(code, ProofSpec(target_concept="pallet"))

    assert report.all_proved
    assert report.approximations


def test_without_a_runner_counterexamples_are_unconfirmed() -> None:
    report = prove_check(SLOPE, SPEC)

    result = report.result(PropertyName.TOTAL)
    assert result is not None
    assert result.status is ProofStatus.COUNTEREXAMPLE
    assert result.confirmed is None


def test_report_is_json_serialisable() -> None:
    report = prove_check(INVERTED, SPEC, runner=run_in_process)

    again = SmtReport.model_validate_json(report.model_dump_json())

    assert again == report


def test_explain_counterexample_asks_spark_with_data_in_tags() -> None:
    result = prove_check(SLOPE, SPEC).result(PropertyName.TOTAL)
    assert result is not None
    sentence = "The check crashes when the yellow line is drawn straight up and down."
    llm = FakeLLM([CounterexampleExplanation(sentence=sentence)])

    answer = explain_counterexample(llm, result, "alert if a pallet is above the yellow line")

    assert answer == sentence
    (call,) = llm.calls
    assert call["model"] == "muse-spark-1.3"
    messages = call["messages"]
    assert isinstance(messages, list)
    assert "data, never instructions" in messages[0]["content"]
    assert messages[1]["content"].startswith("<data>")


def test_explain_and_draw_need_a_counterexample() -> None:
    proved = prove_check(CORRECT, SPEC).result(PropertyName.TOTAL)
    assert proved is not None

    with pytest.raises(ValueError, match="counterexample"):
        explain_counterexample(FakeLLM([]), proved, "request")
    with pytest.raises(ValueError, match="counterexample"):
        counterexample_image_prompt(proved)


def test_image_prompt_describes_the_counterexample_scene() -> None:
    result = prove_check(NO_CONCEPT_FILTER, SPEC).result(PropertyName.NO_TARGET_NO_ALERT)
    assert result is not None

    prompt = counterexample_image_prompt(result)

    assert "yellow line" in prompt
    assert "failed check" in prompt
