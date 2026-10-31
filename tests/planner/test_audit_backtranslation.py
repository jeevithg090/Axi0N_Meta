"""Unit tests for Plan audit (Spark Open) and back-translation (Glimmer) (task P2-07)."""

from __future__ import annotations

import itertools
import json
from collections import deque
from collections.abc import Mapping
from pathlib import Path

import yaml

from rulecam.clients.open_weights.evidence_log import EvidenceLog
from rulecam.clients.open_weights.spark_open import (
    ReasoningStore,
    SparkOpenClient,
    SparkOpenConfig,
    reasoning_id,
)
from rulecam.clients.transport import ModelTransportError
from rulecam.contracts.core import PrivacyMode
from rulecam.contracts.plan import AcceptanceResult, SitePlan
from rulecam.contracts.rule_ir import RuleIR
from rulecam.planner.steps.audit import (
    AUDIT_SKIPPED_PREFIX,
    audit_plan,
    audit_plan_hook,
    is_audit_skipped,
)
from rulecam.planner.steps.back_translate import (
    DATA_NOT_INSTRUCTIONS,
    RuleBackTranslation,
    back_translate_plan,
    back_translate_plan_hook,
    back_translate_rule,
    parse_language_back_translations,
)
from rulecam.verify.tier2 import SparkOpenVerifier
from tests.fakes import FakeClock, FakeLLM

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "rules"
SAMPLE_REASONING = (
    "Auditing plan: prep table and sink are monitored by cam0, but the back-door handwash station "
    "has no camera coverage. This is a potential regulatory blindspot."
)


def _load_sample_rule(rule_id: str = "wash-before-line") -> RuleIR:
    raw = yaml.safe_load((FIXTURES_DIR / "wash-before-line.rule.yaml").read_text(encoding="utf-8"))
    raw["id"] = rule_id
    raw["source"]["text"] = "Cooks must wash hands before food prep"
    return RuleIR.model_validate(raw)


def _make_test_plan(languages: tuple[str, ...] = ("en", "es")) -> SitePlan:
    rule = _load_sample_rule("R-TEST-001")
    return SitePlan(
        plan_id="plan-audit-test-01",
        version=1,
        site_name="Test Kitchen",
        setting_inferred="commercial_kitchen",
        languages=languages,
        request=(),
        inventory=(),
        feasibility=(),
        rules=(rule,),
        privacy_mode=PrivacyMode.STANDARD,
        retention_days=30,
        protected_two_key=(),
        roles={},
        acceptance=AcceptanceResult(checks=(), rounds=0),
        audit=(),
        back_translation="",
        standards=(),
        cost_usd_per_cam_hour=None,
    )


class QueueTransport:
    """Queue transport for Spark Open testing."""

    def __init__(self, *replies: Mapping[str, object] | Exception) -> None:
        self.replies = deque(replies)
        self.requests: list[Mapping[str, object]] = []

    def __call__(self, request: Mapping[str, object]) -> Mapping[str, object]:
        self.requests.append(request)
        reply = self.replies.popleft()
        if isinstance(reply, Exception):
            raise reply
        return reply


def _spark_audit_reply(
    findings: list[str], reasoning: str = SAMPLE_REASONING
) -> Mapping[str, object]:
    content = {"findings": findings}
    message = {"role": "assistant", "content": json.dumps(content), "reasoning_content": reasoning}
    return {"status": 200, "json": {"id": "chatcmpl-audit-123", "choices": [{"message": message}]}}


def test_audit_plan_attached_findings_and_stored_reasoning(tmp_path: Path) -> None:
    """Test Spark Open audit attaches findings and references stored reasoning."""
    findings_list = [
        "The back-door sink isn't covered by any camera.",
        "Rule R-TEST-001 threshold for dwell time (20s) may be strict during peak rush.",
    ]
    transport = QueueTransport(
        _spark_audit_reply(findings_list, SAMPLE_REASONING),
        _spark_audit_reply(findings_list, SAMPLE_REASONING),
    )
    reasoning_store = ReasoningStore(tmp_path / "reasoning")
    client = SparkOpenClient(
        transport,
        clock=FakeClock(),
        reasoning=reasoning_store,
        config=SparkOpenConfig(images_supported=False),
        evidence=EvidenceLog(tmp_path / "evidence.jsonl"),
        monotonic=itertools.count(0.0, 1.0).__next__,
    )
    verifier = SparkOpenVerifier(client)

    plan = _make_test_plan()
    findings = audit_plan(plan, verifier=verifier)

    assert len(findings) == 2
    assert findings[0].by == "spark-open"
    assert findings[0].text == "The back-door sink isn't covered by any camera."
    assert findings[0].reasoning_ref == reasoning_id(SAMPLE_REASONING)

    # Verify stored reasoning text matches word-for-word
    stored_text = client.reasoning_text(findings[0].reasoning_ref)
    assert stored_text == SAMPLE_REASONING

    # Test audit_plan_hook updates plan correctly
    hook = audit_plan_hook(verifier=verifier)
    updated_plan = hook(plan)
    assert len(updated_plan.audit) == 2
    assert not is_audit_skipped(updated_plan)


def test_audit_plan_unavailable_flags_audit_skipped(tmp_path: Path) -> None:
    """Spark Open being unavailable gives a plan flagged audit_skipped (never a silent pass)."""
    error = ModelTransportError("Connection timed out to Baseten")
    transport = QueueTransport(error, error)
    reasoning_store = ReasoningStore(tmp_path / "reasoning")
    client = SparkOpenClient(
        transport,
        clock=FakeClock(),
        reasoning=reasoning_store,
        config=SparkOpenConfig(images_supported=False),
        evidence=EvidenceLog(tmp_path / "evidence.jsonl"),
        monotonic=itertools.count(0.0, 1.0).__next__,
    )
    verifier = SparkOpenVerifier(client)

    plan = _make_test_plan()
    findings = audit_plan(plan, verifier=verifier)

    # Never a silent pass: findings tuple must not be empty!
    assert len(findings) == 1
    assert findings[0].by == "spark-open"
    assert findings[0].text.startswith(AUDIT_SKIPPED_PREFIX)
    assert "ModelTransportError" in findings[0].text

    plan_with_skipped = plan.model_copy(update={"audit": findings})
    assert is_audit_skipped(plan_with_skipped)


def test_audit_plan_no_verifier_configured() -> None:
    """When no verifier is provided or configured, audit is explicitly flagged audit_skipped."""
    plan = _make_test_plan()
    findings = audit_plan(plan, verifier=None)
    assert len(findings) == 1
    assert findings[0].by == "spark-open"
    assert findings[0].text.startswith(AUDIT_SKIPPED_PREFIX)

    plan_skipped = plan.model_copy(update={"audit": findings})
    assert is_audit_skipped(plan_skipped)


def test_back_translation_one_per_language() -> None:
    """Test Glimmer writes a plain-language summary for each rule in each plan language."""
    fake_glimmer = FakeLLM(
        [
            RuleBackTranslation(
                rule_id="R-TEST-001",
                summary="Cooks must wash hands for 20 seconds before entering the prep table.",
            ),
            RuleBackTranslation(
                rule_id="R-TEST-001",
                summary=(
                    "Los cocineros deben lavarse las manos durante 20 segundos "
                    "antes de entrar a la mesa de preparación."
                ),
            ),
        ]
    )

    plan = _make_test_plan(languages=("en", "es"))
    back_trans_text = back_translate_plan(plan, glimmer=fake_glimmer)

    assert "[en]" in back_trans_text
    assert "[es]" in back_trans_text
    assert "Cooks must wash hands" in back_trans_text
    assert "Los cocineros deben lavarse las manos" in back_trans_text

    # Parse per language and verify exactly one back-translation per configured language
    by_lang = parse_language_back_translations(back_trans_text, plan.languages)
    assert len(by_lang) == 2
    assert "en" in by_lang
    assert "es" in by_lang
    assert "wash hands" in by_lang["en"]
    assert "lavarse las manos" in by_lang["es"]

    # Verify back_translate_plan_hook updates SitePlan
    hook = back_translate_plan_hook(glimmer=fake_glimmer)
    # Reset queue for the hook
    fake_glimmer.queue(
        RuleBackTranslation(
            rule_id="R-TEST-001",
            summary="Cooks must wash hands.",
        )
    )
    fake_glimmer.queue(
        RuleBackTranslation(
            rule_id="R-TEST-001",
            summary="Los cocineros deben lavarse las manos.",
        )
    )
    updated_plan = hook(plan)
    assert "[en]" in updated_plan.back_translation
    assert "[es]" in updated_plan.back_translation
    assert "Cooks must wash hands." in updated_plan.back_translation
    assert "Los cocineros deben lavarse las manos." in updated_plan.back_translation


def test_back_translation_prompt_hygiene() -> None:
    """Verify prompt hygiene instructions are included in the Glimmer request."""
    fake_glimmer = FakeLLM(
        [
            RuleBackTranslation(
                rule_id="R-TEST-001",
                summary="Hands must be washed.",
            )
        ]
    )
    rule = _load_sample_rule("R-TEST-001")
    back_translate_rule(rule, "en", glimmer=fake_glimmer)

    assert len(fake_glimmer.calls) == 1
    call = fake_glimmer.calls[0]
    messages = call["messages"]
    system_msg = next(m["content"] for m in messages if m["role"] == "system")
    assert DATA_NOT_INSTRUCTIONS in system_msg


def test_back_translation_fallback_without_glimmer() -> None:
    """When Glimmer is unavailable, fallback generates valid plain language translations."""
    plan = _make_test_plan(languages=("en", "es"))
    text = back_translate_plan(plan, glimmer=None)
    by_lang = parse_language_back_translations(text, plan.languages)

    assert "en" in by_lang
    assert "es" in by_lang
    assert "Rule R-TEST-001" in by_lang["en"]
    assert "Regla R-TEST-001" in by_lang["es"]
