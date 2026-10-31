"""Guardrails and safety checks for user rule requests (MP §3.6, SA §5, task book §4.2).

Deterministic pattern checks run first (English and Spanish) and cannot be overridden by
any model. An optional second-pass StructuredLLM classifier categorizes non-pattern requests
into the same closed set of banned categories.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from rulecam.contracts.core import Frozen
from rulecam.contracts.plan import FeasibilityVerdict
from rulecam.contracts.protocols import StructuredLLM
from rulecam.log import get_logger
from rulecam.planner.models import DATA_NOT_INSTRUCTIONS_CLAUSE

logger = get_logger(__name__)


class GuardrailCategory(StrEnum):
    """Banned request categories under Rulecam policy (MP §3.6, SA §5)."""

    IDENTITY = "identity"
    PER_PERSON_PRODUCTIVITY = "per_person_productivity"
    CLINICAL_OR_EMOTION = "clinical_or_emotion"
    PRIVATE_SPACE = "private_space"
    COVERT = "covert"
    PROTECTED_ATTRIBUTE = "protected_attribute"
    SPEECH_AUDIO = "speech_audio"


class Allowed(Frozen):
    """The request passes all guardrail checks."""

    verdict: Literal["allowed"] = "allowed"


class Declined(Frozen):
    """The request violates a guardrail policy and is declined with an alternative."""

    verdict: Literal["declined"] = "declined"
    category: GuardrailCategory
    reason: str
    alternative: str | None = None

    def to_feasibility_verdict(self, concern: str = "") -> FeasibilityVerdict:
        """Convert this decline into a FeasibilityVerdict contract."""
        says = f"I cannot accept this request: {self.reason}"
        return FeasibilityVerdict(
            concern=concern or self.reason,
            verdict="declined",
            says=says,
            reason=self.reason,
            alternative=self.alternative,
        )


GuardrailVerdict = Allowed | Declined


# Curated deterministic pattern rules: (category, pattern, reason, alternative)
_PATTERNS: tuple[tuple[GuardrailCategory, re.Pattern[str], str, str], ...] = (
    # 1. Per-person productivity / ranking / worker speed (checked before general identity)
    (
        GuardrailCategory.PER_PERSON_PRODUCTIVITY,
        re.compile(
            r"\b("
            r"who\s+is\s+(?:the\s+)?(?:slowest|fastest|laziest|most\s+productive|least\s+productive)|"
            r"which\s+(?:worker|cashier|employee|person)\s+is\s+(?:the\s+)?(?:slowest|fastest)|"
            r"which\s+(?:worker|cashier|employee)\s+has\s+the\s+lowest\s+(?:scan\s+rate|throughput)|"
            r"track\s+(?:per-person|individual|worker|employee)\s+productivity|"
            r"rank\s+(?:workers?|employees?|staff)|"
            r"ranking\s+de\s+trabajadores|"
            r"ranking\s+of\s+(?:workers?|employees?)|"
            r"who\s+(?:worked|did)\s+(?:the\s+)?(?:least|most)|"
            r"worker\s+efficiency\s+per\s+person|"
            r"idle\s+time\s+per\s+(?:worker|employee|person)|"
            r"who\s+took\s+the\s+longest\s+break|"
            r"qui[eé]n\s+es\s+el\s+m[aá]s\s+(?:lento|r[aá]pido|flojo|productivo)|"
            r"qu[eé]\s+empleado\s+tarda\s+m[aá]s|"
            r"productividad\s+(?:por\s+persona|individual|de\s+cada\s+empleado)|"
            r"qui[eé]n\s+trabaja\s+menos|"
            r"qui[eé]n\s+toma\s+pausas\s+m[aá]s\s+largas|"
            r"identify\s+workers\s+who\s+take\s+too\s+long"
            r")\b",
            re.IGNORECASE,
        ),
        "Per-person productivity tracking, rankings, and individual idle tracking are prohibited.",
        (
            "We can measure station throughput, queue wait times, or process cycle times at "
            "equipment/stations without attributing metrics to individuals."
        ),
    ),
    # 2. Identity / face recognition / biometrics
    (
        GuardrailCategory.IDENTITY,
        re.compile(
            r"\b("
            r"recogni[sz]e\s+faces?|"
            r"face\s+recogni[sz]|"
            r"facial\s+recogni[sz]|"
            r"reconocimiento\s+facial|"
            r"reconoc(?:er|imiento)\s+de\s+rostros?|"
            r"reconnaissance\s+faciale|"
            r"identify\s+(?:the\s+)?(?:workers?|persons?|people|individuals?|employees?|staff|who)|"
            r"identificar\s+(?:a\s+los\s+|al\s+)?(?:trabajador(?:es)?|emplead(?:os?|as?)|personal)|"
            r"match\s+(?:employee\s+)?faces?|"
            r"match\s+.*against\s+photos?|"
            r"biometrics?|biometr[ií]a|"
            r"scan\s+faces?|"
            r"who\s+is\s+(?:this|that|in\s+the|the\s+person)|"
            r"qui[eé]n\s+es\s+(?:esta\s+persona|este|ese|aquel)"
            r")\b",
            re.IGNORECASE,
        ),
        "Identity, facial recognition, and biometric individual identification are prohibited.",
        (
            "We can detect presence, equipment usage, or safety PPE compliance across anonymous "
            "people without identifying anyone."
        ),
    ),
    # 3. Clinical, emotional, or illness inference
    (
        GuardrailCategory.CLINICAL_OR_EMOTION,
        re.compile(
            r"\b("
            r"looks?\s+confused|"
            r"tell\s+me\s+if\s+.*looks?\s+confused|"
            r"is\s+(?:a\s+)?(?:cook|worker|employee|person|someone)\s+sick|"
            r"detect\s+(?:illness|sickness|disease|fever|fatigue|emotions?|distress|sadness)|"
            r"emotional\s+distress|"
            r"detect\s+worker\s+fatigue|"
            r"is\s+worker\s+tired|"
            r"fever\s+detection|"
            r"est[aá]\s+enfermo|"
            r"cocinero\s+est[aá]\s+enfermo|"
            r"parece\s+confundid[oa]|"
            r"detectar\s+(?:enfermedad|fiebre|fatiga|tristeza|cansancio|depresi[oó]n|emociones?)|"
            r"estornudos?\s+de\s+cocinero|"
            r"diagnos(?:e|is)|clinical\s+inference"
            r")\b",
            re.IGNORECASE,
        ),
        "Clinical, medical, illness, or emotional state inferences are strictly prohibited.",
        (
            "We can enforce standard shift-start policy checklists, ergonomic posture alerts, "
            "or scheduled cleaning cadences."
        ),
    ),
    # 4. Private spaces (bathroom, bedroom, break room)
    (
        GuardrailCategory.PRIVATE_SPACE,
        re.compile(
            r"\b("
            r"bathrooms?|restrooms?|toilets?|water\s*closets?|w\.?c\.?|"
            r"bedrooms?|private\s+quarters?|"
            r"break\s*rooms?|staff\s+bedroom|"
            r"changing\s*rooms?|locker\s*rooms?|showers?|"
            r"ba[nñ]os?|servicios?|sanitarios?|dormitorios?|rec[aá]maras?|cuartos?\s+de\s+ba[nñ]o|"
            r"salas?\s+de\s+descanso|vestuarios?|vestidores?"
            r")\b",
            re.IGNORECASE,
        ),
        "Cameras and visual monitoring inside private spaces are strictly prohibited.",
        (
            "We can monitor public entry corridors, exterior access gates, or station perimeters "
            "outside private areas."
        ),
    ),
    # 5. Covert / secret monitoring
    (
        GuardrailCategory.COVERT,
        re.compile(
            r"\b("
            r"hidden\s+camera|"
            r"spy\s+(?:camera|on)|"
            r"secret\s+(?:camera|monitoring|surveillance)|"
            r"covert\s+(?:camera|monitoring)|"
            r"without\s+(?:them|workers?|employees?)\s+knowing|"
            r"unannounced\s+camera|"
            r"undercover\s+camera|"
            r"c[aá]mara\s+oculta|"
            r"c[aá]mara\s+esp[ií]a|"
            r"espiar\s+(?:a\s+los\s+)?trabajadores|"
            r"monitoreo\s+secreto|"
            r"sin\s+que\s+(?:se\s+den\s+cuenta|lo\s+sepan)"
            r")\b",
            re.IGNORECASE,
        ),
        "Covert or unannounced worker surveillance is prohibited. Rules must be transparent.",
        "All active rules are published openly on the worker rules portal with dispute mechanisms.",
    ),
    # 6. Protected attributes (gender, race, religion, pregnancy, disability)
    (
        GuardrailCategory.PROTECTED_ATTRIBUTE,
        re.compile(
            r"\b("
            r"pregnant|pregnancy|"
            r"gender|sex\b|race\b|ethnicity|religion|"
            r"religious\s+attire|hijab|turban|yarmulke|skullcap|"
            r"skin\s+colou?r|sexual\s+orientation|disabilit(?:y|ies)|"
            r"embarazad[ao]|embarazo|g[eé]nero|raza|etnia|religi[oó]n|"
            r"vestimenta\s+religiosa|hiyab|turbante|color\s+de\s+piel|"
            r"orientaci[oó]n\s+sexual|discapacidad"
            r")\b",
            re.IGNORECASE,
        ),
        "Rules targeting protected personal attributes are prohibited.",
        (
            "We can monitor universal safety standards or required equipment handling applied "
            "uniformly across all roles."
        ),
    ),
    # 7. Ambient speech audio transcription
    (
        GuardrailCategory.SPEECH_AUDIO,
        re.compile(
            r"\b("
            r"transcribe\s+(?:what\s+workers\s+say|speech|conversations?|audio|talk|crew\s+gossip)|"
            r"record\s+audio\s+conversations?|"
            r"listen\s+to\s+(?:what\s+they\s+say|talk|conversations?)|"
            r"eavesdrop(?:ping)?|"
            r"speech\s+(?:surveillance|monitoring)|"
            r"what\s+workers\s+are\s+talking\s+about|"
            r"transcribir\s+(?:lo\s+que\s+dicen|las\s+conversaciones|el\s+audio|la\s+charla)|"
            r"escuchar\s+(?:conversaciones|a\s+los\s+empleados|lo\s+que\s+hablan)|"
            r"grabar\s+conversaciones|"
            r"qu[eé]\s+est[aá]n\s+hablando"
            r")\b",
            re.IGNORECASE,
        ),
        "Ambient speech transcription and eavesdropping on worker conversations are prohibited.",
        (
            "We can monitor non-speech acoustic safety signals such as glass breakage, equipment "
            "alarms, or machinery squeals."
        ),
    ),
)


class _ModelClassification(Frozen):
    """Internal model output for guardrail categorization."""

    verdict: Literal["allowed", "declined"]
    category: GuardrailCategory | None = None
    reason: str | None = None
    alternative: str | None = None


_GUARDRAIL_SYSTEM_PROMPT = (
    "You are Rulecam's Safety and Policy Guardrail Classifier.\n"
    f"{DATA_NOT_INSTRUCTIONS_CLAUSE}\n\n"
    "Rulecam turns cameras into safety and flow sentinels, but strictly refuses:\n"
    "1. identity: facial recognition, matching faces, identifying named persons.\n"
    "2. per_person_productivity: tracking worker speed, employee rankings, idle time.\n"
    "3. clinical_or_emotion: diagnosing illness, fever, fatigue, confusion, emotional distress.\n"
    "4. private_space: cameras in bathrooms, bedrooms, break rooms, or locker rooms.\n"
    "5. covert: hidden cameras, spy surveillance, monitoring without worker knowledge.\n"
    "6. protected_attribute: rules targeting gender, race, pregnancy, religion, disability.\n"
    "7. speech_audio: transcribing ambient conversation, eavesdropping on worker talk.\n\n"
    "Allowed: safety rules (handwashing, safety zones, PPE, emergency exits, fall detection, "
    "vehicle distances) and station-level flow metrics (station cycle time, queue length).\n\n"
    "If the request violates any banned category, return verdict='declined', the matching "
    "category, a polite reason, and a constructive privacy-preserving alternative. "
    "If it is safe, return verdict='allowed'."
)


def check_request(
    text: str,
    *,
    spark: StructuredLLM | None = None,
) -> GuardrailVerdict:
    """Evaluate a user rule request against safety guardrails.

    Runs deterministic regex pattern checks first in English and Spanish; deterministic declines
    cannot be overridden. If deterministic checks pass and a Spark StructuredLLM client is provided,
    calls Spark structured output to classify edge cases into the closed set of banned categories.

    Args:
        text: The user's rule request or concern text.
        spark: Optional StructuredLLM instance for second-pass model classification.

    Returns:
        Allowed if compliant, or Declined(category, reason, alternative).
    """
    cleaned = text.strip()
    if not cleaned:
        return Allowed()

    # Pass 1: Deterministic pattern matching (cannot be overridden)
    for category, pattern, reason, alternative in _PATTERNS:
        if pattern.search(cleaned):
            logger.info("guardrail_deterministic_decline", category=category, request=cleaned)
            return Declined(category=category, reason=reason, alternative=alternative)

    # Pass 2: Model classification (if client provided)
    if spark is not None:
        try:
            classification = spark.structured(
                model="muse-spark-1.3",
                messages=[
                    {"role": "system", "content": _GUARDRAIL_SYSTEM_PROMPT},
                    {"role": "user", "content": f"User request:\n{cleaned}"},
                ],
                schema=_ModelClassification,
            )
            if classification.verdict == "declined" and classification.category is not None:
                default_reason = f"Request violates policy on {classification.category}."
                reason = classification.reason or default_reason
                logger.info(
                    "guardrail_model_decline",
                    category=classification.category,
                    request=cleaned,
                )
                return Declined(
                    category=classification.category,
                    reason=reason,
                    alternative=classification.alternative,
                )
        except Exception as exc:
            logger.warning("guardrail_model_check_failed", error=str(exc))

    return Allowed()
