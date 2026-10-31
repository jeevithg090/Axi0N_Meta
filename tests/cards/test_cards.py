"""Rule cards and declined cards rendered from plan files (task P2-10)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from rulecam.cards import (
    CardError,
    load_plan_cards,
    plan_cards,
    render_card_png,
    render_declined_card_html,
    render_rule_card_html,
)
from rulecam.cards.build import REDACTED, rule_ci_numbers, spark_open_audit, worker_url
from rulecam.contracts.core import PrivacyMode
from rulecam.contracts.plan import (
    AcceptanceCheck,
    AcceptanceResult,
    AuditFinding,
    FeasibilityVerdict,
    SitePlan,
)
from rulecam.contracts.rule_ir import RuleIR
from rulecam.planner.errors import PipelineError
from rulecam.planner.guardrails import Declined, check_request
from rulecam.planner.pipeline import PlanStore
from rulecam.rule_ci import prove_plan_hook
from rulecam.rule_ci.lookalikes import LookalikeFinding, LookalikeReport
from rulecam.rule_ci.red_team import RedTeamReport

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "rules"
SNAPSHOTS = Path(__file__).resolve().parent / "snapshots"
BASE_URL = "https://rulecam.example"
HANDWASH = "hands washed for 20 seconds before the line"
SLOWEST = "who is the slowest worker, Maria or John?"
MOOD = "tell me when Carlos seems down today"
MODEL_REASON = "Carlos's mood cannot be judged from video; seems down is clinical inference."
MODEL_ALTERNATIVE = "Offer Carlos a station check-in button instead."


def _rule() -> RuleIR:
    text = (FIXTURES / "wash-before-line.rule.yaml").read_text("utf-8")
    return RuleIR.model_validate(yaml.safe_load(text))


def _lookalikes(*fooled: bool) -> RedTeamReport:
    findings = tuple(
        LookalikeFinding(
            concept="sink",
            lookalike=f"steel prep table {i}",
            image=f"lookalike_{i}.png",
            perception_fooled=f,
            tier1_fooled=False,
        )
        for i, f in enumerate(fooled)
    )
    return RedTeamReport(lookalikes=LookalikeReport(findings=findings))


def _declined(request: str) -> FeasibilityVerdict:
    decided = check_request(request)
    assert isinstance(decided, Declined)
    return decided.to_feasibility_verdict(request)


def _plan(red_team: RedTeamReport | None = None) -> SitePlan:
    """A plan as the pipeline leaves it: accepted, audited (P2-07), proved (P2-08/09)."""
    rule = _rule()
    model_decline = Declined(
        category="clinical_or_emotion", reason=MODEL_REASON, alternative=MODEL_ALTERNATIVE
    ).to_feasibility_verdict(MOOD)
    plan = SitePlan(
        plan_id="main-st-kitchen",
        version=2,
        site_name="Main St <Kitchen>",
        setting_inferred="commercial_kitchen",
        languages=("es", "en"),
        request=(),
        inventory=(),
        feasibility=(
            FeasibilityVerdict(concern=HANDWASH, verdict="observable", says="I can see the sink."),
            _declined(SLOWEST),
            model_decline,
        ),
        rules=(rule,),
        privacy_mode=PrivacyMode.ANONYMOUS,
        acceptance=AcceptanceResult(
            checks=(
                AcceptanceCheck(
                    item=HANDWASH, check="concern_covered", passed=True, detail=f"rule {rule.id!r}"
                ),
                AcceptanceCheck(item=rule.id, check="citation_text_match", passed=True),
            ),
            rounds=0,
        ),
        audit=(),
        back_translation=(
            "[es]\n- wash-before-line: Lavarse las manos 20 s <b>antes</b> de la línea.\n\n"
            "[en]\n- wash-before-line: Wash hands for 20 s before entering the line."
        ),
    )
    hook = prove_plan_hook(red_team=None if red_team is None else (lambda _rule: red_team))
    return hook(plan)


def _snapshot(name: str, html: str) -> None:
    path = SNAPSHOTS / name
    if os.environ.get("RULECAM_UPDATE_SNAPSHOTS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
    assert html == path.read_text(encoding="utf-8"), f"{name} changed; review and update it"


def test_rule_card_snapshot() -> None:
    (card,), _ = plan_cards(_plan(_lookalikes(False, False, False)), base_url=BASE_URL)
    _snapshot("rule_card.html", render_rule_card_html(card))


def test_declined_card_snapshot() -> None:
    _, declined = plan_cards(_plan())
    _snapshot("declined_card.html", render_declined_card_html(declined[0]))


def test_rule_card_fields_come_from_the_plan() -> None:
    (card,), _ = plan_cards(_plan(_lookalikes(False, False, True)), base_url=BASE_URL)

    assert card.lang == "es"  # the crew language: the plan's first
    assert card.plain_language == "Lavarse las manos 20 s <b>antes</b> de la línea."
    assert card.standards == ("Food Code 2026 §2-301.12", "Food Code 2026 §2-103.11(G)")
    assert card.citation_verified is True
    assert card.feasibility == "observable"
    assert card.calibrated_alpha == pytest.approx(0.10)
    assert card.spark_open == "no_findings"
    assert card.cameras == ("cam1",)
    assert card.worker_url == f"{BASE_URL}/worker/main-st-kitchen/cam1?lang=es"
    ci = card.rule_ci
    assert ci is not None
    assert (ci.boundary_passed, ci.boundary_total) == (13, 13)  # real Rule CI, not a default
    assert (ci.mutants_killed, ci.mutants_total) == (5, 5)
    assert ci.lookalikes is not None
    assert (ci.lookalikes.tested, ci.lookalikes.fooled) == (3, 1)
    assert ci.ci_green is False  # a fooled lookalike fails Rule CI


def test_rule_card_html_escapes_plan_text_and_shows_who_checked() -> None:
    (card,), _ = plan_cards(_plan(_lookalikes(False)), base_url=BASE_URL)
    html = render_rule_card_html(card)

    assert "<b>antes</b>" not in html
    assert "&lt;b&gt;antes&lt;/b&gt;" in html
    assert "Main St &lt;Kitchen&gt;" in html
    assert "Glimmer + Spark Open" in html
    assert "13/13" in html
    assert "0/1" in html
    assert "<svg" in html  # the QR code


def test_what_did_not_run_is_said_not_invented() -> None:
    plan = _plan().model_copy(
        update={
            "acceptance": AcceptanceResult(checks=(), rounds=0),
            "back_translation": "",
            "feasibility": (),
        }
    )
    (card,), _ = plan_cards(plan)
    html = render_rule_card_html(card)

    assert card.rule_ci is None
    assert card.feasibility is None
    assert card.plain_language is None
    assert card.spark_open == "not_run"
    assert card.worker_url is None
    assert "Rule CI has not run" in html
    assert "Feasibility: not recorded" in html
    assert "Not independently checked" in html
    assert "<svg" not in html


def test_rule_ci_without_red_team_has_no_lookalike_numbers() -> None:
    plan = _plan()
    numbers = rule_ci_numbers(plan, "wash-before-line")
    assert numbers is not None
    assert numbers.lookalikes is None
    assert numbers.ci_green is True
    assert "Lookalikes not run" in render_rule_card_html(plan_cards(plan)[0][0])


def test_spark_open_audit_states() -> None:
    plan = _plan()
    skipped = (AuditFinding(by="spark-open", text="audit_skipped: not audited, no key"),)
    found = (AuditFinding(by="spark-open", text="gloves rule missing"),)
    assert spark_open_audit(plan.model_copy(update={"audit": skipped})) == "skipped"
    assert spark_open_audit(plan.model_copy(update={"audit": found})) == "findings"
    card = plan_cards(plan.model_copy(update={"audit": skipped}))[0][0]
    assert card.audit_findings == 0
    html = render_rule_card_html(card)
    assert "Glimmer</span>" in html  # Spark Open not credited when its audit was skipped
    assert "Spark Open audit skipped" in html


def test_a_rule_glimmer_could_not_describe_is_marked() -> None:
    marker = "not back-translated (Glimmer unavailable)"
    plan = _plan().model_copy(update={"back_translation": f"[es]\n- wash-before-line: {marker}"})
    card = plan_cards(plan)[0][0]
    assert card.plain_language is None
    assert card.not_back_translated == marker
    assert not card.glimmer_checked


def test_declined_cards_never_contain_the_request_or_a_name() -> None:
    """Guardrail: the request text and the names in it never reach a declined card."""
    _, (curated, model) = plan_cards(_plan())
    for card, request in ((curated, SLOWEST), (model, MOOD)):
        html = render_declined_card_html(card)
        assert request not in html
        for name in ("Maria", "John", "Carlos", "slowest"):
            assert name not in html
    assert curated.category == "per_person_productivity"
    assert model.category is None  # the plan keeps a model decline's reason, not its category
    assert REDACTED in model.reason
    assert model.alternative == f"Offer {REDACTED} a station check-in button instead."


def test_cards_load_from_the_saved_plan(tmp_path: Path) -> None:
    store = PlanStore(tmp_path)
    plan = _plan(_lookalikes(False))
    store.save_plan(plan)
    assert load_plan_cards(store, plan.plan_id, base_url=BASE_URL) == plan_cards(
        plan, base_url=BASE_URL
    )
    with pytest.raises(CardError):
        load_plan_cards(store, "other-plan")
    with pytest.raises(PipelineError):
        load_plan_cards(store, "../etc")


def test_rendering_is_deterministic() -> None:
    plan = _plan(_lookalikes(False))
    first = [render_rule_card_html(c) for c in plan_cards(plan, base_url=BASE_URL)[0]]
    second = [render_rule_card_html(c) for c in plan_cards(plan, base_url=BASE_URL)[0]]
    assert first == second


def test_worker_url_and_skeleton_are_validated() -> None:
    with pytest.raises(CardError):
        worker_url("javascript:alert(1)", "p", "cam1", "en")
    assert worker_url(BASE_URL, "p", "cam 1/x", "es") == f"{BASE_URL}/worker/p/cam%201%2Fx?lang=es"
    card = plan_cards(_plan())[0][0]
    with pytest.raises(CardError):
        render_rule_card_html(card, skeleton_png=b"<svg onload=alert(1)>")
    html = render_rule_card_html(card, skeleton_png=b"\x89PNG\r\n\x1a\nxyz")
    assert 'src="data:image/png;base64,iVBORw0KGgp4eXo="' in html


@pytest.mark.ui
async def test_png_is_1080_by_1350(tmp_path: Path) -> None:
    pytest.importorskip("playwright")
    from PIL import Image  # noqa: PLC0415

    html = render_rule_card_html(plan_cards(_plan())[0][0])
    out = await render_card_png(html, tmp_path / "card.png")
    with Image.open(out) as image:
        assert image.size == (1080, 1350)
