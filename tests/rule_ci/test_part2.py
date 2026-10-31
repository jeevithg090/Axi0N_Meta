"""Rule CI part 2 (task P2-09): scenes, lookalikes, injection signs, forecast, wiring.

Muse Image, SAM, Spark and Tier 1 are fakes; the policy and scene-text firewall are the real
`VerificationPolicy` and `SceneTextFirewall` (with a fake Prompt Guard and blurrer).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from PIL import Image, ImageChops

from rulecam.bus import Message, Topic
from rulecam.clients.meta_hosted.image import ImageResult
from rulecam.contracts import (
    CandidateEvent,
    Detection,
    Mask,
    RuleIR,
    Severity,
    VerifierVerdict,
)
from rulecam.contracts.core import PrivacyMode
from rulecam.contracts.plan import AcceptanceResult, SitePlan
from rulecam.evidence.chain import EventChain
from rulecam.planner.models import DATA_NOT_INSTRUCTIONS_CLAUSE
from rulecam.replay.errors import ReplayMiss
from rulecam.rule_ci import (
    AlertBudget,
    CIResult,
    RedTeam,
    RedTeamReport,
    append_regression,
    forecast_alerts,
    prove_plan_hook,
    run_detectability_scenes,
    run_injection_red_team,
    run_lookalike_red_team,
    run_rule_ci,
)
from rulecam.rule_ci.injection import SIGN_VARIANTS, composite_sign, render_sign, sign_box
from rulecam.rule_ci.lookalikes import LookalikeItem, LookalikeList, load_regressions
from rulecam.rule_ci.scenes import SceneCase, SceneMatrix, scene_matrix
from rulecam.rules.backtest import Backtestable, BacktestReport
from rulecam.verify.firewall import SceneTextFirewall
from rulecam.verify.policy import VerificationPolicy
from tests.fakes import EPOCH, FakeClock, FakeLLM, FakeVerifier, make_verdict

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "rules"


def _rule(name: str = "wash-before-line", **update: Any) -> RuleIR:
    rule = RuleIR.model_validate(yaml.safe_load((FIXTURES / f"{name}.rule.yaml").read_text()))
    return rule.model_copy(update=update)


class FakeRenderer:
    """Muse Image: writes a distinct PNG per call and remembers each image's prompt."""

    def __init__(self, out: Path, fail: Exception | None = None) -> None:
        self.out = out
        self.fail = fail
        self.calls: list[tuple[str, tuple[Path, ...]]] = []
        self.prompt_of: dict[Path, str] = {}

    def generate(
        self,
        prompt: str,
        *,
        reference_images: Sequence[Path] = (),
        size: str = "1024x1024",
        previous_response_id: str | None = None,
    ) -> ImageResult:
        if self.fail is not None:
            raise self.fail
        self.calls.append((prompt, tuple(reference_images)))
        n = len(self.calls)
        path = self.out / f"img{n}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (320, 240), (n * 7 % 256, 90, 120)).save(path)
        self.prompt_of[path] = prompt
        return ImageResult(path=path, response_id=f"resp_{n}")


def _detection(concept: str) -> Detection:
    mask = Mask(rle="x", box_xyxy=(0.0, 0.0, 10.0, 10.0), confidence=0.9)
    return Detection(
        camera_id="cam1", frame_idx=0, ts=EPOCH, concept=concept, object_id=1, mask=mask
    )


class FakeSam:
    """SAM on single images: `finds(image, concept_prompt)` decides; every call is recorded."""

    def __init__(self, finds: Callable[[Path, str], bool]) -> None:
        self.finds = finds
        self.calls: list[tuple[Path, str, str]] = []

    def segment_image(
        self,
        frame: Path,
        concept: str,
        *,
        camera_id: str = "probe",
        include_confidence: bool = True,
    ) -> list[Detection]:
        self.calls.append((frame, concept, camera_id))
        return [_detection(concept)] if self.finds(frame, concept) else []


@pytest.fixture
def frame(tmp_path: Path) -> Path:
    path = tmp_path / "real" / "cam1.png"
    path.parent.mkdir()
    Image.new("RGB", (320, 240), (40, 40, 40)).save(path)
    return path


# --- detectability scenes -----------------------------------------------------------------------


def test_the_regenerate_loop_stops_after_two_regenerations(tmp_path: Path, frame: Path) -> None:
    rule = _rule()
    renderer = FakeRenderer(tmp_path / "out")
    sam = FakeSam(lambda _image, _concept: False)
    matrix = SceneMatrix(
        cases=[SceneCase(name="wash", kind="positive", prompt="a cook washing", present=["hands"])]
    )

    report = run_detectability_scenes(
        rule,
        frame,
        spark=FakeLLM(),
        renderer=renderer,
        segmenter=sam,
        camera_id="cam1",
        matrix=matrix,
    )

    (result,) = report.results
    assert (result.passed, result.renders, result.missing) == (False, 3, ("hands",))
    assert len(renderer.calls) == 3  # the first render plus at most 2 regenerations
    assert all(refs == (frame,) for _, refs in renderer.calls)  # always the real frame
    assert renderer.calls[0][0] == "a cook washing"
    assert "make the hands clearly visible" in renderer.calls[1][0]
    assert {(c, cam) for _, c, cam in sam.calls} == {("hands", "cam1")}
    assert (report.passed, report.pass_rate, report.positives()) == (0, 0.0, [])


def test_scenes_report_the_pass_rate_and_regenerate_only_what_failed(
    tmp_path: Path, frame: Path
) -> None:
    rule = _rule()
    renderer = FakeRenderer(tmp_path / "out")
    matrix = SceneMatrix(
        cases=[
            SceneCase(name="wash", kind="positive", prompt="hands at the sink", present=["hands"]),
            SceneCase(name="no_sink", kind="negative", prompt="the line", absent=["sink"]),
        ]
    )
    # Hands are always found; the sink is (wrongly) in the first render of the negative case.
    sam = FakeSam(
        lambda image, concept: (
            concept == "hands" or (concept == "sink" and image.name == "img2.png")
        )
    )

    report = run_detectability_scenes(
        rule,
        frame,
        spark=FakeLLM(),
        renderer=renderer,
        segmenter=sam,
        camera_id="cam1",
        matrix=matrix,
    )

    wash, no_sink = report.results
    assert (wash.passed, wash.renders) == (True, 1)
    assert (no_sink.passed, no_sink.renders) == (True, 2)
    assert "there must be no sink" in renderer.calls[2][0]
    assert report.pass_rate == 1.0
    assert report.positives() == [Path(wash.image)]
    assert report.summary() == "scenes 2/2 detectable"


def test_spark_writes_the_matrix_with_only_the_rules_concepts() -> None:
    rule = _rule()
    spark = FakeLLM(
        [
            {
                "cases": [
                    {
                        "name": "wash",
                        "kind": "positive",
                        "prompt": "p",
                        "present": ["hands", "soap"],
                        "absent": ["helmet"],
                    }
                ]
            }
        ]
    )
    (case,) = scene_matrix(rule, spark).cases
    assert (case.present, case.absent) == (["hands"], [])
    system = spark.calls[0]["messages"][0]["content"]  # type: ignore[index]
    assert DATA_NOT_INSTRUCTIONS_CLAUSE in system


# --- lookalikes ---------------------------------------------------------------------------------

POSTER = LookalikeItem(concept="person", lookalike="person on a poster", prompt="add a poster")
GLOVE = LookalikeItem(concept="hands", lookalike="wet glove", prompt="add a wet glove")
BUCKET = LookalikeItem(concept="sink", lookalike="white bucket", prompt="add a white bucket")


def _lookalike_rig(tmp_path: Path) -> tuple[FakeRenderer, FakeSam, FakeVerifier]:
    renderer = FakeRenderer(tmp_path / "out")

    def sam_finds(image: Path, concept: str) -> bool:  # SAM mistakes the poster for a person
        return concept == "person" and renderer.prompt_of[image] == POSTER.prompt

    def tier1(candidate: CandidateEvent, _rule: RuleIR) -> VerifierVerdict:
        fooled = renderer.prompt_of[Path(candidate.strip_paths[0])] == GLOVE.prompt
        return make_verdict(verdict="violation" if fooled else "no_violation", score=0.9)

    return renderer, FakeSam(sam_finds), FakeVerifier(responder=tier1)


def test_a_fooled_lookalike_is_detected_and_gets_a_fix_proposal(
    tmp_path: Path, frame: Path
) -> None:
    rule = _rule()
    renderer, sam, tier1 = _lookalike_rig(tmp_path)

    report = run_lookalike_red_team(
        rule,
        frame,
        spark=FakeLLM(),
        renderer=renderer,
        segmenter=sam,
        tier1=tier1,
        camera_id="cam1",
        ts=EPOCH,
        items=[POSTER, GLOVE, BUCKET],
        regressions_dir=tmp_path / "regressions",
    )

    # Each lookalike is rendered into the real frame and judged on its own still.
    assert [p for p, _ in renderer.calls] == [POSTER.prompt, GLOVE.prompt, BUCKET.prompt]
    assert all(refs == (frame,) for _, refs in renderer.calls)
    assert [c.strip_paths for c, _ in tier1.calls] == [(str(p),) for p in renderer.prompt_of]
    assert {c.severity for c, _ in tier1.calls} == {rule.severity}
    poster, glove, bucket = report.findings
    assert (poster.perception_fooled, poster.tier1_fooled) == (True, False)
    assert poster.fix is not None
    assert (poster.fix.kind, poster.fix.text) == ("negative_concept", "person on a poster")
    assert (glove.perception_fooled, glove.tier1_fooled) == (False, True)
    assert glove.fix is not None
    assert (glove.fix.kind, glove.fix.text) == ("concept_prompt", "hands, not a wet glove")
    assert (bucket.fooled, bucket.fix) == (False, None)
    assert (
        report.summary() == "3 lookalikes, fooled 2 (person on a poster, wet glove) → fix proposed"
    )


def test_spark_lists_lookalikes_of_the_rules_concepts_only(tmp_path: Path, frame: Path) -> None:
    rule = _rule()
    renderer, sam, tier1 = _lookalike_rig(tmp_path)
    harness = LookalikeItem(concept="harness", lookalike="backpack strap", prompt="strap")
    spark = FakeLLM([LookalikeList(items=[BUCKET, harness])])

    report = run_lookalike_red_team(
        rule,
        frame,
        spark=spark,
        renderer=renderer,
        segmenter=sam,
        tier1=tier1,
        camera_id="cam1",
        ts=EPOCH,
        regressions_dir=tmp_path / "regressions",
    )

    assert [f.lookalike for f in report.findings] == ["white bucket"]
    assert DATA_NOT_INSTRUCTIONS_CLAUSE in spark.calls[0]["messages"][0]["content"]  # type: ignore[index]


def test_a_confirmed_false_alarm_is_appended_as_a_permanent_test(
    tmp_path: Path, frame: Path
) -> None:
    rule = _rule()
    folder = tmp_path / "regressions"
    path = append_regression(rule.id, POSTER, folder)
    assert append_regression(rule.id, GLOVE, folder) == path
    assert path == folder / "wash-before-line.jsonl"
    assert path.read_text().count("\n") == 2
    assert load_regressions(rule.id, folder) == [POSTER, GLOVE]
    with pytest.raises(ValueError, match="safe"):
        append_regression("../escape", POSTER, folder)

    renderer, sam, tier1 = _lookalike_rig(tmp_path)
    report = run_lookalike_red_team(
        rule,
        frame,
        spark=FakeLLM(),
        renderer=renderer,
        segmenter=sam,
        tier1=tier1,
        camera_id="cam1",
        ts=EPOCH,
        items=[GLOVE, BUCKET],
        regressions_dir=folder,
    )
    # Every run tests the regressions too; one already in the list is tested once.
    assert [(f.lookalike, f.regression) for f in report.findings] == [
        ("wet glove", False),
        ("white bucket", False),
        ("person on a poster", True),
    ]


# --- injection signs ----------------------------------------------------------------------------


def test_the_injection_composite_contains_the_sign_text(tmp_path: Path, frame: Path) -> None:
    text = SIGN_VARIANTS[0].text
    out = composite_sign(frame, text, tmp_path / "c.png")

    with Image.open(out) as composite, Image.open(frame) as scene:
        box = sign_box(composite.size)
        size = (box[2] - box[0], box[3] - box[1])
        sign = composite.convert("RGB").crop(box)
        assert ImageChops.difference(sign, render_sign(text, size)).getbbox() is None
        assert ImageChops.difference(sign, render_sign("EXIT", size)).getbbox() is not None
        blank = Image.new("RGB", size, "white")
        assert ImageChops.difference(sign, blank).getbbox() is not None  # the text is drawn
        rest = (box[2], 0, *composite.size)  # the scene beside the sign is untouched
        before, after = scene.convert("RGB").crop(rest), composite.convert("RGB").crop(rest)
        assert ImageChops.difference(before, after).getbbox() is None


class FakeGuard:
    """Prompt Guard 2 that only catches English "SYSTEM" and "ignore" signs."""

    def malicious_score(self, text: str) -> float:
        return 0.99 if "SYSTEM" in text or "ignore" in text.lower() else 0.01


class CatchAllGuard:
    def malicious_score(self, text: str) -> float:
        return 0.99 if text else 0.0


class FakeBlurrer:
    def blur(self, candidate: CandidateEvent) -> tuple[str, ...]:
        return tuple(f"{p}.blurred" for p in candidate.strip_paths)


def _obedient_glimmer(signs: dict[str, str]) -> FakeVerifier:
    """A Tier 1 that copies the sign into `scene_text` and obeys it; blurred, it sees the cook."""

    def verify(candidate: CandidateEvent, _rule: RuleIR) -> VerifierVerdict:
        path = candidate.strip_paths[0]
        if path.endswith(".blurred"):
            return make_verdict(verdict="violation", score=0.9)
        return make_verdict(verdict="no_violation", score=0.05).model_copy(
            update={"scene_text": signs[Path(path).stem]}
        )

    return FakeVerifier(responder=verify)


def _injection(
    tmp_path: Path, scene: Path, rule: RuleIR, guard: FakeGuard | CatchAllGuard
) -> tuple[Any, EventChain]:
    signs = {f"{rule.id}-{v.name}": v.text for v in SIGN_VARIANTS}
    tier1 = _obedient_glimmer(signs)
    clock = FakeClock()
    chain = EventChain(tmp_path / "chain", "site", clock=clock)
    firewall = SceneTextFirewall(classifier=guard, blurrer=FakeBlurrer(), tier1=tier1, chain=chain)
    policy = VerificationPolicy(tier1=tier1, firewall=firewall)
    report = run_injection_red_team(
        rule,
        [scene],
        tier1=tier1,
        policy=policy,
        chain=chain,
        clock=clock,
        camera_id="cam1",
        out_dir=tmp_path / "injection",
    )
    return report, chain


def test_a_high_severity_alert_survives_every_sign(tmp_path: Path, frame: Path) -> None:
    """D3: HIGH acts first and a veto only demotes it, so no sign can silence it."""
    report, _ = _injection(tmp_path, frame, _rule(severity=Severity.HIGH), FakeGuard())
    assert len(report.results) == 6
    assert report.silenced == ()
    flagged = {r.variant for r in report.results if r.tamper}
    assert flagged == {"english_system", "ignore_instructions"}  # D2: Prompt Guard flagged them
    assert report.summary() == "injection 6/6 alerts kept, 2 flagged"


def test_flagged_signs_keep_a_medium_alert_and_misses_are_reported(
    tmp_path: Path, frame: Path
) -> None:
    rule = _rule()
    caught, _ = _injection(tmp_path / "a", frame, rule, CatchAllGuard())
    assert caught.silenced == ()
    assert all(r.tamper and r.route == "auto" for r in caught.results)

    leaky, _ = _injection(tmp_path / "b", frame, rule, FakeGuard())
    assert {r.variant for r in leaky.silenced} == {
        "spanish_system",
        "fake_authority",
        "test_mode",
        "fake_exemption",
    }
    red_team = RedTeamReport(injection=leaky)
    report = run_rule_ci(rule, red_team=red_team)
    assert not report.ci_green
    assert "injection sign 'spanish_system' silenced the alert" in report.failures


def test_a_rerun_still_detects_tamper(tmp_path: Path, frame: Path) -> None:
    rule = _rule()
    signs = {f"{rule.id}-{v.name}": v.text for v in SIGN_VARIANTS}
    tier1 = _obedient_glimmer(signs)
    clock = FakeClock()
    chain = EventChain(tmp_path / "chain", "site", clock=clock)
    firewall = SceneTextFirewall(
        classifier=CatchAllGuard(), blurrer=FakeBlurrer(), tier1=tier1, chain=chain
    )
    policy = VerificationPolicy(tier1=tier1, firewall=firewall)
    for _ in range(2):
        report = run_injection_red_team(
            rule,
            [frame],
            tier1=tier1,
            policy=policy,
            chain=chain,
            clock=clock,
            camera_id="cam1",
            out_dir=tmp_path / "injection",
            variants=SIGN_VARIANTS[:1],
        )
        assert [r.tamper for r in report.results] == [True]
    with pytest.raises(ValueError, match="positive scene"):
        run_injection_red_team(
            rule,
            [],
            tier1=tier1,
            policy=policy,
            chain=chain,
            clock=clock,
            camera_id="cam1",
            out_dir=tmp_path,
        )


# --- forecast -----------------------------------------------------------------------------------


def _backtest(
    camera: str, hours: float, count: int, backtestable: Backtestable = "yes", rule_id: str = ""
) -> BacktestReport:
    return BacktestReport(
        rule_id=rule_id or "wash-before-line",
        rule_ir_hash="h",
        camera_id=camera,
        t0=EPOCH,
        t1=EPOCH + timedelta(hours=hours),
        backtestable=backtestable,
        missing_concepts=(),
        events=(),
        count=count,
        per_hour=count / hours,
    )


def test_forecast_arithmetic() -> None:
    rule = _rule()
    backtests = [_backtest("cam1", 2, 3), _backtest("cam1", 1, 0), _backtest("cam2", 4, 2)]

    forecast = forecast_alerts(rule, backtests, AlertBudget(alerts_per_shift=10, shift_hours=8))

    # cam1: 3 candidates in 3 h = 1/h; cam2: 2 in 4 h = 0.5/h; 1.5/h x 8 h = 12 per shift.
    assert forecast.per_shift == pytest.approx(12.0)
    assert (forecast.hours, forecast.candidates, forecast.cameras) == (7.0, 5, ("cam1", "cam2"))
    assert forecast.within_budget is False
    assert forecast.summary() == "forecast 12.0 alerts/shift (budget 10, over budget)"
    roomy = forecast_alerts(rule, backtests, AlertBudget(alerts_per_shift=12, shift_hours=8))
    assert roomy.within_budget is True


def test_forecast_is_unavailable_rather_than_zero_without_the_concepts() -> None:
    rule = _rule()
    budget = AlertBudget(alerts_per_shift=5, shift_hours=8)
    forecast = forecast_alerts(rule, [_backtest("cam1", 2, 0, "no")], budget)
    assert (forecast.per_shift, forecast.within_budget, forecast.backtestable) == (None, None, "no")
    assert forecast.summary().startswith("forecast unavailable")
    with pytest.raises(ValueError, match="no backtests"):
        forecast_alerts(rule, [], budget)
    with pytest.raises(ValueError, match="other rules"):
        forecast_alerts(rule, [_backtest("cam1", 1, 0, rule_id="other")], budget)
    with pytest.raises(ValueError, match="positive duration"):
        forecast_alerts(rule, [_backtest("cam1", 1, 0).model_copy(update={"t1": EPOCH})], budget)


# --- the red team and the prove step ------------------------------------------------------------


class Recorder:
    def __init__(self) -> None:
        self.messages: list[Message] = []

    def publish(self, topic: Topic, payload: object) -> None:
        self.messages.append(Message(topic=topic, payload=payload))


def _red_team(
    tmp_path: Path, frame: Path | None, renderer: FakeRenderer | None = None
) -> tuple[RedTeam, FakeRenderer]:
    renderer = renderer or FakeRenderer(tmp_path / "out")

    def sam_finds(image: Path, concept: str) -> bool:
        prompt = renderer.prompt_of[image]
        return (concept == "hands" and "wash" in prompt) or prompt == POSTER.prompt

    signs = {f"wash-before-line-{v.name}": v.text for v in SIGN_VARIANTS}
    tier1 = _obedient_glimmer(signs)

    def judge(candidate: CandidateEvent, rule: RuleIR) -> VerifierVerdict:
        path = Path(candidate.strip_paths[0])
        if path in renderer.prompt_of:  # a lookalike still: nothing to report
            return make_verdict(verdict="no_violation", score=0.05)
        return tier1.verify(candidate, rule)

    glimmer = FakeVerifier(responder=judge)
    clock = FakeClock()
    chain = EventChain(tmp_path / "chain", "site", clock=clock)
    firewall = SceneTextFirewall(
        classifier=CatchAllGuard(), blurrer=FakeBlurrer(), tier1=glimmer, chain=chain
    )
    spark = FakeLLM(
        [
            {
                "cases": [
                    {"name": "wash", "kind": "positive", "prompt": "wash", "present": ["hands"]}
                ]
            },
            LookalikeList(items=[POSTER, BUCKET]),
        ]
    )
    team = RedTeam(
        spark=spark,
        renderer=renderer,
        segmenter=FakeSam(sam_finds),
        tier1=glimmer,
        policy=VerificationPolicy(tier1=glimmer, firewall=firewall),
        chain=chain,
        clock=clock,
        frame_for=lambda _rule: None if frame is None else ("cam1", frame),
        out_dir=tmp_path / "injection",
        regressions_dir=tmp_path / "regressions",
        backtests_for=lambda _rule: [_backtest("cam1", 8, 2)],
        budget=AlertBudget(alerts_per_shift=5, shift_hours=8),
    )
    return team, renderer


def test_the_prove_step_records_part_two_in_ci_result_and_the_acceptance_trace(
    tmp_path: Path, frame: Path
) -> None:
    rule = _rule()
    team, _ = _red_team(tmp_path, frame)
    bus = Recorder()
    plan = SitePlan(
        plan_id="p",
        site_name="Test",
        setting_inferred="kitchen",
        languages=("en",),
        request=(),
        inventory=(),
        feasibility=(),
        rules=(rule,),
        privacy_mode=PrivacyMode.STANDARD,
        acceptance=AcceptanceResult(checks=(), rounds=1),
    )

    proved = prove_plan_hook(bus, red_team=team)(plan)  # type: ignore[arg-type]

    (payload,) = [m.payload for m in bus.messages if m.topic == Topic.CI_RESULT]
    assert isinstance(payload, CIResult)
    part2 = payload.report.red_team
    assert part2 is not None
    assert part2.skipped == ()
    assert part2.scenes is not None
    assert part2.scenes.summary() == "scenes 1/1 detectable"
    assert part2.injection is not None
    assert part2.injection.silenced == ()
    assert part2.forecast is not None
    assert part2.forecast.per_shift == pytest.approx(2.0)
    assert not payload.ci_green  # the poster fooled SAM
    assert "2 lookalikes, fooled 1 (person on a poster) → fix proposed" in payload.summary
    checks = {c.check: c for c in proved.acceptance.checks}
    assert not checks["ci_green"].passed
    assert "lookalike 'person on a poster' fooled person" in checks["ci_green"].detail
    assert checks["nonvacuous"].passed


def test_stages_that_cannot_run_are_skipped_with_why(tmp_path: Path, frame: Path) -> None:
    rule = _rule()
    no_frame, _ = _red_team(tmp_path / "a", None)
    report = no_frame(rule)
    assert report.scenes is None
    assert report.lookalikes is None
    assert report.injection is None
    assert report.forecast is not None
    assert report.skipped == ("scenes, lookalikes, injection: no camera frame for the rule",)
    assert run_rule_ci(rule, red_team=report).ci_green  # part 1 is green; nothing failed

    miss = ReplayMiss(provider="meta", model="muse-image-1.0", key="k")
    broken, _ = _red_team(tmp_path / "b", frame, FakeRenderer(tmp_path / "b", fail=miss))
    report = broken(rule)
    assert [s.split(":")[0] for s in report.skipped] == ["scenes", "lookalikes", "injection"]
    assert "ReplayMiss" in report.skipped[0]
    assert report.summary().endswith("not run: scenes, lookalikes, injection")
