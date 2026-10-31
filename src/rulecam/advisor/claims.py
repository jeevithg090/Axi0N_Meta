"""Slot-only claim text and the claim-strength linter (task P2-12, SA §4 step 13, §5, §13).

- Templates contain words and `{slots}`, never digits; code fills each slot from a measured
  metric result, so no number on a card is written by a model.
- Causal wording is capped at the evidence grade. Grade "A" means a switchback verdict and is the
  only grade that may say "caused"; below it, causal verbs become "coincided with".
- Overclaims from SA §13 ("proves", "guarantees", "hallucination-free", ...) and per-person
  framing (SA §5) are rejected outright, never rewritten.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Final, Literal

from rulecam.advisor.errors import ClaimError
from rulecam.contracts.advisor import SlotClaim

Grade = Literal["A", "B", "C"]

SLOT: Final = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
DIGIT: Final = re.compile(r"\d")

# Causal wording allowed only with a switchback verdict (grade A); longest phrases first.
CAUSAL_DOWNGRADES: Final[tuple[tuple[re.Pattern[str], str], ...]] = tuple(
    (re.compile(rf"\b{pattern}\b", re.IGNORECASE), replacement)
    for pattern, replacement in (
        (r"resulted\s+in", "coincided with"),
        (r"results\s+in", "coincides with"),
        (r"led\s+to", "coincided with"),
        (r"leads\s+to", "coincides with"),
        (r"due\s+to", "alongside"),
        (r"because\s+of", "alongside"),
        (r"caused\s+by", "coincided with"),
        (r"caused", "coincided with"),
        (r"causes", "coincides with"),
        (r"causing", "coinciding with"),
        (r"drove", "coincided with"),
        (r"reduced", "coincided with lower"),
        (r"improved", "coincided with better"),
    )
)

# SA §13 "never say": rejected at every grade.
OVERCLAIMS: Final = re.compile(
    r"\b(proves?|proved|proven|guarantee[sd]?|eliminate[sd]?|hallucination-free|"
    r"learns\s+your\s+business|certainly|definitely)\b",
    re.IGNORECASE,
)

# Questions or claims about individual people (SA §5). Matching declines; false positives are
# safe (the owner gets a station alternative), misses are not.
PER_PERSON: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(
        r"\bwho\s+(is|was|were|are|took|takes|made|makes|spent|spends|walked|did|does|has|had|"
        r"works?|worked)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(which|what)\s+(worker|employee|driver|operator|picker|packer|person|staff|aide|"
        r"cook|crew\s+member)s?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(by|per)[\s-](worker|employee|person|driver|operator|picker|individual|"
        r"staff\s+member|name)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(rank|compare|score|rate)\s+(the\s+)?(workers|employees|drivers|operators|pickers|"
        r"staff|people|crew)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(slowest|fastest|laziest|best|worst)\s+(\w+\s+)?(worker|employee|driver|operator|"
        r"picker|packer|person|aide|cook)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bindividual\s+(productivity|performance|speed|output)\b", re.IGNORECASE),
    re.compile(r"\b(meet|met|missed)\s+(their|his|her)\s+quotas?\b", re.IGNORECASE),
    re.compile(r"\b(most|longest)\s+breaks?\b|\bbreak\s?room\b", re.IGNORECASE),
    re.compile(r"\btrack\s+\w+'s\b", re.IGNORECASE),
    re.compile(r"\btrack_id\b", re.IGNORECASE),
    re.compile(r"\bcompare\s+[A-Z][a-z]+\s+(and|with|vs\.?)\s+[A-Z][a-z]+\b"),
    re.compile(r"\bqui[eé]n(es)?\b", re.IGNORECASE),
    re.compile(r"\b(cu[aá]l|qu[eé])\s+(empleado|trabajador|operador|conductor)", re.IGNORECASE),
    re.compile(r"\brendimiento\s+individual\b", re.IGNORECASE),
    re.compile(r"\bempac[oó]\s+[A-ZÁÉÍÓÚÑ]"),
)

# Person nouns that make a claim per-person framed, even without a question form.
PERSON_NOUNS: Final = re.compile(
    r"\b(worker|employee|driver|operator|picker|packer|individual|staff\s+member|aide|"
    r"person|people)s?\b",
    re.IGNORECASE,
)


def is_per_person(text: str) -> bool:
    """Return whether `text` asks about, or frames a claim around, individual people."""
    return any(pattern.search(text) for pattern in PER_PERSON)


def assert_no_digits(template: str) -> None:
    """Reject a template with a digit outside its `{slots}`.

    Args:
        template: Text with `{slots}`.

    Raises:
        ClaimError: If any digit appears outside a slot name.
    """
    found = DIGIT.search(SLOT.sub("", template))
    if found is not None:
        msg = f"template contains the digit {found.group(0)!r} outside a slot: {template!r}"
        raise ClaimError(msg)


def lint_claim_text(text: str, grade: Grade) -> str:
    """Cap the wording of `text` at the evidence grade.

    Args:
        text: Claim or explanation text (no digits are checked here; see `assert_no_digits`).
        grade: Evidence grade; only "A" (a switchback verdict) may keep causal verbs.

    Returns:
        The text with causal verbs downgraded to "coincided with" below grade A.

    Raises:
        ClaimError: If the text overclaims (SA §13) or is framed around people (SA §5).
    """
    overclaim = OVERCLAIMS.search(text)
    if overclaim is not None:
        msg = f"claim overstates the evidence ({overclaim.group(0)!r}): {text!r}"
        raise ClaimError(msg)
    if is_per_person(text) or PERSON_NOUNS.search(text):
        msg = f"claim is framed around people, not stations: {text!r}"
        raise ClaimError(msg)
    if grade == "A":
        return text
    result = text
    for pattern, replacement in CAUSAL_DOWNGRADES:
        result = pattern.sub(replacement, result)
    return result


def build_slot_claim(template: str, slots: Mapping[str, str], grade: Grade) -> SlotClaim:
    """Build a linted `SlotClaim` whose slots exactly match the measured results named.

    Args:
        template: Text with `{slots}`, never digits.
        slots: Slot name to metric result id.
        grade: Evidence grade.

    Returns:
        The claim, with wording capped at `grade`.

    Raises:
        ClaimError: If the template has digits, overclaims, is per-person, or its slots do not
            match `slots` exactly.
    """
    assert_no_digits(template)
    linted = lint_claim_text(template, grade)
    named = set(SLOT.findall(linted))
    if named != set(slots):
        msg = f"template slots {sorted(named)} do not match the evidence slots {sorted(slots)}"
        raise ClaimError(msg)
    return SlotClaim(template=linted, slots=dict(slots), grade=grade)


def render_slot_claim(claim: SlotClaim, values: Mapping[str, str]) -> str:
    """Fill a claim's slots with formatted measured values, keyed by metric result id.

    Args:
        claim: The claim.
        values: Metric result id to its formatted value.

    Returns:
        The rendered sentence.

    Raises:
        ClaimError: If a slot's metric result has no value (a number is never made up).
    """
    missing = sorted(rid for rid in claim.slots.values() if rid not in values)
    if missing:
        msg = f"no measured value for metric results {missing}"
        raise ClaimError(msg)
    return claim.template.format(**{slot: values[rid] for slot, rid in claim.slots.items()})
