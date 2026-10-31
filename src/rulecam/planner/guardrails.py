"""Planner guardrails: decline banned requests before anything is compiled (MP §3.6, SA §5).

`check_request(text)` returns `Allowed` or `Declined(category, reason, alternative)`.

1. A deterministic check runs first and cannot be overridden by any model. The request is
   accent-folded and lower-cased, then matched against curated English and Spanish patterns
   (plus a few French ones). The patterns name what is banned (a person-ranking superlative, a
   face-recognition verb, a private room), so ordinary safety rules that merely contain a risky
   word ("safety shower", "disability ramp", "race through the aisle", "área de servicio",
   "who is in the exclusion zone") stay allowed.
2. When a `StructuredLLM` is given, Spark then classifies the request into the same closed set of
   categories. A model failure is not swallowed: the check fails closed by raising.

A decline becomes a `FeasibilityVerdict(verdict="declined", reason=…, alternative=…)`.
"""

from __future__ import annotations

import re
import unicodedata
from enum import StrEnum
from typing import Final, Literal

from pydantic import model_validator

from rulecam.contracts.core import Frozen
from rulecam.contracts.plan import FeasibilityVerdict
from rulecam.contracts.protocols import StructuredLLM
from rulecam.log import get_logger
from rulecam.planner.models import DATA_NOT_INSTRUCTIONS_CLAUSE

logger = get_logger(__name__)

GUARDRAIL_MODEL: Final = "muse-spark-1.3"


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
        """Convert this decline into a `FeasibilityVerdict`.

        Args:
            concern: The declined request text; defaults to the reason.

        Returns:
            A `declined` verdict carrying the reason and the alternative.
        """
        return FeasibilityVerdict(
            concern=concern or self.reason,
            verdict="declined",
            says=f"I cannot accept this request: {self.reason}",
            reason=self.reason,
            alternative=self.alternative,
        )


GuardrailVerdict = Allowed | Declined


def normalize(text: str) -> str:
    """Accent-fold, lower-case and collapse punctuation and whitespace to single spaces.

    Args:
        text: Any request text.

    Returns:
        The normalised text, e.g. "¿Quién es el MÁS lento?" -> "quien es el mas lento".
    """
    folded = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in folded if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^\w\s-]", " ", stripped).split())


def _rx(*alternatives: str) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(alternatives) + r")\b")


# People nouns, singular or plural (normalised text, so no accents).
_PEOPLE = (
    r"(?:workers?|employees?|cooks?|chefs?|cashiers?|packers?|pickers?|drivers?|operators?|"
    r"servers?|waiters?|waitress(?:es)?|staff(?:\s+members?)?|persons?|people|individuals?|"
    r"crew\s+members?|residents?|customers?|nurses?|guards?|team\s+members?)"
)
_PERSONAS = (
    r"(?:trabajador(?:es|as?)?|emplead[oa]s?|cociner[oa]s?|cajer[oa]s?|personal|personas?|"
    r"operari[oa]s?|meser[oa]s?|conductor(?:es)?)"
)
_SUPERLATIVE = (
    r"(?:slowest|fastest|laziest|quickest|worst|best|least\s+productive|most\s+productive|"
    r"least\s+efficient|most\s+efficient|least\s+active|most\s+idle)"
)
_PERSON_METRIC = (
    r"(?:productiv\w*|efficien\w*|idle\w*|idling|scan\s+rate|throughput|output|performance|"
    r"how\s+long|breaks?|lunch)"
)
_MOOD = (
    r"(?:sick|ill|unwell|drunk|intoxicated|stoned|stressed|depressed|anxious|confused|tired|"
    r"fatigued|exhausted|sad|upset|angry|nervous|in\s+pain)"
)
_ESTADO = r"(?:enferm\w*|borrach\w*|cansad\w*|estresad\w*|confundid\w*|trist\w*|deprimid\w*)"

_PATTERNS: Final[tuple[tuple[GuardrailCategory, re.Pattern[str], str, str], ...]] = (
    (
        GuardrailCategory.PER_PERSON_PRODUCTIVITY,
        _rx(
            rf"{_SUPERLATIVE}\s+(?:\w+\s+)?{_PEOPLE}",
            rf"who\s+(?:is|was|s)\s+(?:the\s+)?{_SUPERLATIVE}",
            r"who\s+(?:is|was)\s+(?:the\s+)?(?:slowest|fastest|laziest)",
            rf"which\s+{_PEOPLE}\s+(?:is|was)\s+(?:the\s+)?{_SUPERLATIVE}",
            rf"which\s+{_PEOPLE}\s+(?:has|had)\s+the\s+(?:lowest|highest|most|least|worst|best)",
            rf"which\s+{_PEOPLE}\s+takes?\s+(?:the\s+)?(?:longest|most|least)",
            rf"(?:rank|ranks|ranking|rate|rating|score|scoring|leaderboard\s+of|compare)\s+"
            rf"(?:the\s+|our\s+|all\s+)?{_PEOPLE}",
            rf"{_PEOPLE}\s+(?:by|on)\s+(?:speed|productivity|efficiency|output|performance)",
            rf"(?:per|each|every|individual)\s+{_PEOPLE}\W*(?:\w+\W+){{0,4}}?{_PERSON_METRIC}",
            rf"{_PERSON_METRIC}\s+(?:\w+\s+){{0,3}}?(?:per|each|of\s+each|for\s+each|by)\s+{_PEOPLE}",
            rf"(?:per-person|per\s+person)\s+{_PERSON_METRIC}",
            r"who\s+(?:worked|works|did|does)\s+(?:the\s+)?(?:least|most|less|more)",
            r"who\s+took\s+the\s+longest",
            rf"{_PEOPLE}\s+who\s+take\s+too\s+long",
            rf"quien\s+es\s+(?:el|la)\s+(?:{_PERSONAS}\s+)?mas\s+"
            r"(?:lent|rapid|floj|productiv|vag)\w*",
            rf"{_PERSONAS}\s+mas\s+(?:lent|rapid|floj|productiv|vag)\w*",
            rf"(?:ranking|clasificacion)\s+de\s+{_PERSONAS}",
            rf"que\s+{_PERSONAS}\s+tarda\s+mas",
            rf"cuanto\s+tarda\s+cada\s+{_PERSONAS}",
            r"productividad\s+(?:por\s+persona|individual|de\s+cada)",
            r"quien\s+trabaja\s+(?:menos|mas)",
            r"quien\s+toma\s+(?:pausas|descansos)",
        ),
        "Per-person productivity tracking, rankings, and individual idle tracking are prohibited.",
        (
            "We can measure station throughput, queue wait times, or process cycle times at "
            "equipment/stations without attributing metrics to individuals."
        ),
    ),
    (
        GuardrailCategory.IDENTITY,
        _rx(
            r"fac(?:e|es|ial)\s+(?:id|ids|recogni\w*|match\w*|scan\w*|identif\w*)",
            r"(?:recogni\w*|match\w*|scan\w*|identif\w*|track\w*)\s+(?:\w+\s+){0,2}?faces?",
            r"facial\s+\w+",
            r"biometric\w*|biometria",
            r"re-?identif\w*",
            rf"identif(?:y|ies|ying)\s+(?:the\s+|which\s+|each\s+|every\s+)?(?:{_PEOPLE}|who)",
            rf"track\w*\s+(?:individual|each|every|specific|named)\s+{_PEOPLE}",
            rf"follow\s+(?:each|every|individual|specific)\s+{_PEOPLE}",
            r"who\s+is\s+(?:this|that)\s+(?:person|man|woman|guy|girl|worker)",
            r"who\s+is\s+the\s+person\s+(?:in|at|on|near)",
            r"reconocimiento\s+facial|reconnaissance\s+faciale",
            r"reconoc\w*\s+(?:de\s+)?(?:las\s+|los\s+)?(?:caras?|rostros?)",
            rf"identificar\s+(?:a\s+)?(?:los\s+|las\s+|al\s+|el\s+|la\s+)?{_PERSONAS}",
            r"quien\s+es\s+(?:esta|esa|este|ese|aquel)\s+(?:persona|hombre|mujer|senor\w*|chic[oa])",
        ),
        "Identity, facial recognition, and biometric individual identification are prohibited.",
        (
            "We can detect presence, equipment usage, or safety PPE compliance across anonymous "
            "people without identifying anyone."
        ),
    ),
    (
        GuardrailCategory.CLINICAL_OR_EMOTION,
        _rx(
            rf"(?:looks?|looking|seems?|appears?)\s+(?:to\s+be\s+|very\s+|a\s+bit\s+)?{_MOOD}",
            rf"(?:is|are|was|were|gets?)\s+(?:\w+\s+){{0,2}}?{_MOOD}",
            r"detect\w*\s+(?:\w+\s+){0,2}?(?:illness|sickness|disease|fever|fatigue|emotions?|"
            r"distress|sadness|stress|intoxication|drunkenness|mood|pain|seizures?|symptoms?)",
            r"fever|emotional\s+\w+|emotion\s+(?:detection|recognition)|mental\s+health|"
            r"clinical\s+inference|dementia|alzheimer\w*|intoxicat\w*|drunkenness",
            rf"diagnos\w*\s+(?:the\s+)?(?:illness|disease|condition|{_PEOPLE})",
            rf"esta\s+(?:\w+\s+)?{_ESTADO}",
            rf"(?:parece|se\s+ve)\s+(?:\w+\s+)?{_ESTADO}",
            r"detectar\s+(?:\w+\s+){0,2}?(?:enfermedad|fiebre|fatiga|tristeza|cansancio|"
            r"depresion|emociones?|estado\s+de\s+animo|estres)",
            r"estornudos?\s+de\s+(?:los\s+)?cociner[oa]s?",
        ),
        "Clinical, medical, illness, or emotional state inferences are strictly prohibited.",
        (
            "We can enforce standard shift-start policy checklists, ergonomic posture alerts, "
            "or scheduled cleaning cadences."
        ),
    ),
    (
        GuardrailCategory.PRIVATE_SPACE,
        _rx(
            r"bathrooms?|restrooms?|toilets?|washrooms?|lavator(?:y|ies)|wc",
            r"bedrooms?|(?:private|sleeping|staff)\s+quarters",
            r"break\s*rooms?|breakrooms?|staff\s+lounge",
            r"(?:changing|locker|dressing|nursing|lactation|prayer)\s*rooms?",
            r"(?<!safety\s)(?<!emergency\s)(?<!eyewash\s)showers?(?!\s+station)",
            r"banos?(?!\s+maria)|aseos?|servicios\s+higienicos|cuartos?\s+de\s+bano",
            r"dormitorios?|recamaras?",
            r"(?:salas?|cuartos?)\s+de\s+descanso",
            r"vestuarios?|vestidores?|regaderas?|duchas?",
        ),
        "Cameras and visual monitoring inside private spaces are strictly prohibited.",
        (
            "We can monitor public entry corridors, exterior access gates, or station perimeters "
            "outside private areas."
        ),
    ),
    (
        GuardrailCategory.COVERT,
        _rx(
            r"(?:hidden|spy|covert|secret|undercover|concealed|disguised|pinhole)\s+"
            r"(?:cam\w*|monitor\w*|surveil\w*|record\w*)",
            r"spy\s+on|spying\s+on|covertly",
            r"secretly\s+(?:watch|monitor|record|film|track|observe)\w*",
            rf"without\s+(?:them|their|anyone|the\s+)?\s*(?:{_PEOPLE}\s+)?"
            r"(?:knowing|noticing|knowledge|being\s+told|consent)",
            rf"without\s+(?:telling|informing|notifying)\s+(?:them|anyone|the\s+|our\s+)?\s*"
            rf"(?:{_PEOPLE})?",
            r"hide\s+(?:a|the)\s+(?:\w+\s+){0,2}?cam\w*",
            r"unannounced\s+cam\w*",
            r"camara\s+(?:oculta|espia|escondida|secreta)",
            r"espiar|monitoreo\s+secreto|vigilancia\s+secreta|en\s+secreto",
            r"sin\s+que\s+(?:se\s+den\s+cuenta|lo\s+sepan|sepan)",
            r"sin\s+(?:avisar|decirles|informar)",
        ),
        "Covert or unannounced worker surveillance is prohibited. Rules must be transparent.",
        "All active rules are published openly on the worker rules portal with dispute mechanisms.",
    ),
    (
        GuardrailCategory.PROTECTED_ATTRIBUTE,
        _rx(
            r"pregnan\w*|gender|sex|sexual\s+orientation|racial|ethnic\w*|religio\w*",
            r"(?:by|their|of)\s+race|race\s+of",
            r"hijab|headscarf|turbans?|yarmulke|kippah|skullcap",
            r"skin\s+colou?r|nationality|immigra\w*",
            rf"disabled\s+{_PEOPLE}|{_PEOPLE}\s+with\s+disabilit\w*",
            rf"age\s+of\s+(?:the\s+)?{_PEOPLE}|how\s+old\s+(?:is|are)|"
            rf"(?:older|elderly|younger)\s+{_PEOPLE}",
            r"women|woman|females?|males?|men(?!\s+at\s+work)",
            r"embarazad\w*|embarazo|raza|etnia|etnic\w*|religion|religios\w*",
            r"hiyab|velo\s+islamico|turbante|color\s+de\s+piel|orientacion\s+sexual",
            r"nacionalidad|inmigrant\w*|mujeres|hombres",
        ),
        "Rules targeting protected personal attributes are prohibited.",
        (
            "We can monitor universal safety standards or required equipment handling applied "
            "uniformly across all roles."
        ),
    ),
    (
        GuardrailCategory.SPEECH_AUDIO,
        _rx(
            r"transcrib\w*\s+(?:\w+\s+){0,3}?(?:say|says|said|saying|speech|conversations?|audio|"
            r"talk\w*|chatter|gossip|calls?|discussions?|what)",
            r"(?:record|recording|capture|log|store)\w*\s+(?:\w+\s+){0,3}?(?:conversations?|speech|"
            r"chatter|gossip|voices)",
            r"(?:record|recording|capture|log)\w*\s+what\s+(?:\w+\s+){0,2}?"
            r"(?:say|says|saying|said|talk\w*)",
            r"listen\w*\s+(?:in\s+)?(?:to\s+|on\s+)?(?:\w+\s+){0,2}?(?:conversations?|talk\w*|"
            r"chatter|gossip|what\s+(?:they|\w+)\s+say)",
            r"eavesdrop\w*|wiretap\w*",
            r"(?:speech|voice|audio)\s+(?:surveillance|monitoring|recording|capture)",
            rf"what\s+(?:the\s+)?(?:{_PEOPLE}|they)\s+(?:are\s+)?(?:saying|talking\s+about|say)",
            r"microphones?\s+to\s+(?:listen|record|capture)",
            r"transcribir\w*",
            r"grabar\s+(?:\w+\s+){0,3}?(?:conversacion\w*|lo\s+que\s+(?:dicen|hablan)|voces)",
            r"escuchar\s+(?:\w+\s+){0,2}?(?:conversacion\w*|lo\s+que\s+(?:dicen|hablan)|"
            rf"a\s+los\s+{_PERSONAS})",
            rf"lo\s+que\s+(?:dicen|hablan)\s+los\s+{_PERSONAS}",
            r"(?:de\s+)?que\s+estan\s+hablando",
        ),
        "Ambient speech transcription and eavesdropping on worker conversations are prohibited.",
        (
            "We can monitor non-speech acoustic safety signals such as glass breakage, equipment "
            "alarms, or machinery squeals."
        ),
    ),
)


class _ModelClassification(Frozen):
    """The classifier's answer; a decline must name its category."""

    verdict: Literal["allowed", "declined"]
    category: GuardrailCategory | None = None
    reason: str | None = None
    alternative: str | None = None

    @model_validator(mode="after")
    def _decline_has_category(self) -> _ModelClassification:
        if self.verdict == "declined" and self.category is None:
            msg = "a declined verdict must name one of the fixed categories"
            raise ValueError(msg)
        return self


_GUARDRAIL_SYSTEM_PROMPT = (
    "You are Rulecam's Safety and Policy Guardrail Classifier.\n"
    f"{DATA_NOT_INSTRUCTIONS_CLAUSE}\n\n"
    "Rulecam turns cameras into safety and flow sentinels, but strictly refuses:\n"
    "1. identity: facial recognition, matching faces, identifying or following named persons.\n"
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


def deterministic_check(text: str) -> Declined | None:
    """Run the curated patterns only.

    Args:
        text: The request text.

    Returns:
        The decline for the first matching category, or `None`.
    """
    normalized = normalize(text)
    for category, pattern, reason, alternative in _PATTERNS:
        if pattern.search(normalized):
            logger.info("guardrail_deterministic_decline", category=str(category))
            return Declined(category=category, reason=reason, alternative=alternative)
    return None


def curated_decline(reason: str) -> Declined | None:
    """Return the curated decline whose fixed reason is exactly `reason`, if there is one.

    A `declined` `FeasibilityVerdict` keeps the reason but not the category; for a decline made
    by the deterministic check the reason is one of the fixed policy texts, which names it.

    Args:
        reason: A recorded decline reason.

    Returns:
        The curated `Declined` (category, fixed reason and alternative), or `None` when the
        reason was written by the model.
    """
    for category, _pattern, fixed_reason, alternative in _PATTERNS:
        if reason == fixed_reason:
            return Declined(category=category, reason=fixed_reason, alternative=alternative)
    return None


def policy_words() -> frozenset[str]:
    """Return every word (case-folded) of the curated decline reasons and alternatives."""
    return frozenset(
        word.casefold()
        for _category, _pattern, reason, alternative in _PATTERNS
        for word in re.findall(r"\w+", f"{reason} {alternative}")
    )


def check_request(text: str, *, spark: StructuredLLM | None = None) -> GuardrailVerdict:
    """Evaluate a rule request against the guardrails.

    The deterministic check runs first and its declines are final. Only when it passes and
    `spark` is given does Spark classify the request into the same fixed categories.

    Args:
        text: The user's rule request or concern text.
        spark: Optional classifier for the second pass.

    Returns:
        `Allowed`, or `Declined(category, reason, alternative)`.

    Raises:
        ModelClientError: If the model pass fails; the check never falls back to allowing.
    """
    cleaned = text.strip()
    if not cleaned:
        return Allowed()
    declined = deterministic_check(cleaned)
    if declined is not None:
        return declined
    if spark is None:
        return Allowed()
    result = spark.structured(
        model=GUARDRAIL_MODEL,
        messages=[
            {"role": "system", "content": _GUARDRAIL_SYSTEM_PROMPT},
            {"role": "user", "content": f"User request:\n{cleaned}"},
        ],
        schema=_ModelClassification,
    )
    if result.verdict == "declined" and result.category is not None:
        logger.info("guardrail_model_decline", category=str(result.category))
        return Declined(
            category=result.category,
            reason=result.reason or f"Request violates policy on {result.category}.",
            alternative=result.alternative,
        )
    return Allowed()
