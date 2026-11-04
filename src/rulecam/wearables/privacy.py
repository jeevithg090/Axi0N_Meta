"""No looks, only safety: strip descriptions of people's clothing and appearance from model text.

The prompts already forbid it, but the live runs showed Muse Spark still writing "the cashier in
yellow" and "a brown and beige jacket and red cap". So every text a model returns for the glasses
goes through `scrub`: garment descriptions ("in a white shirt", "wearing a red cap"), colours worn
("in yellow", "yellow-shirted") are cut out, and a sentence about hair, age, skin or build is
dropped. Safety equipment and its absence ("no high-visibility vest", "a hard hat") are kept,
because that is what the wearer needs to hear.
"""

from __future__ import annotations

import re
from typing import Final

from pydantic import BaseModel

COLOURS: Final = (
    "black|white|grey|gray|silver|red|orange|yellow|green|blue|navy|teal|turquoise|purple|"
    "violet|pink|brown|beige|tan|khaki|cream|maroon|burgundy|olive|gold|golden|dark|light|"
    "bright|pale|striped|checked|plaid|patterned|floral|denim|leather"
)
GARMENTS: Final = (
    "jacket|shirt|t-shirt|tshirt|top|cap|hat|beanie|hoodie|hood|coat|dress|skirt|trousers|"
    "pants|jeans|shorts|uniform|sweater|jumper|cardigan|blouse|scarf|apron|headwear|headscarf|"
    "clothes|clothing|outfit|overalls|tracksuit|polo|tank top|sweatshirt|fleece|shoes|trainers|"
    "sneakers|boots|bag|backpack"
)
SAFETY: Final = re.compile(
    r"hi-?vis|high[- ]visibility|reflective|safety|hard ?hat|helmet|vest|gloves|goggles|"
    r"glasses|ear ?(?:defenders|protection)|mask|respirator|harness|steel[- ]toe",
    re.IGNORECASE,
)
_WORDS = r"(?:[\w'-]+(?:,)?\s+){0,5}?"
GARMENT_PHRASE: Final = re.compile(
    rf",?\s*\b(?:wearing|dressed in|in|with)\s+(?:a|an|the|their|his|her)?\s*{_WORDS}"
    rf"(?:{GARMENTS})\b(?:\s+and\s+(?:a|an)?\s*{_WORDS}(?:{GARMENTS})\b)*",
    re.IGNORECASE,
)
PEOPLE: Final = (
    "person|people|man|men|woman|women|worker|workers|employee|employees|staff|operator|driver|"
    "cashier|customer|customers|figure|figures|shopper|shoppers|steward|picker|child|children|someone|he|she|"
    "they|guard|attendant"
)
COLOUR_WORN: Final = re.compile(  # "the cashier in yellow": a colour worn, said of a person
    rf"(\b(?:{PEOPLE})\b)\s+(?:in|wearing|dressed in)\s+(?:{COLOURS})"
    rf"(?:\s*(?:,|and|or)\s*(?:{COLOURS}))*\b(?!\s+(?:{GARMENTS}|[\w-]+\s+(?:{GARMENTS})))",
    re.IGNORECASE,
)
COLOUR_SHIRTED: Final = re.compile(rf"\b(?:{COLOURS})-(?:shirted|clad|haired|jacketed)\s*", re.I)
LOOKS: Final = re.compile(
    r"\b(?:hair|haired|beard|bearded|moustache|mustache|bald|blonde?|brunette|redhead|skin|"
    r"complexion|elderly|teenager|teenage|middle-aged|\d+\s*(?:-\s*)?years?[- ]old|overweight|"
    r"obese|skinny|tattoo|tattooed|makeup|ethnic|race)\b",
    re.IGNORECASE,
)
SENTENCE: Final = re.compile(r"(?<=[.!?])\s+")


def _cut_garments(sentence: str) -> str:
    """Remove garment phrases that are not safety equipment."""

    def keep_safety(match: re.Match[str]) -> str:
        phrase = match.group(0)
        if SAFETY.search(phrase):
            return phrase
        before = sentence[: match.start()].rstrip().lower()
        if before.endswith(("no", "without", "not")):
            return phrase  # "no high-visibility clothing": the absence is safety information
        return ""

    return GARMENT_PHRASE.sub(keep_safety, sentence)


def _tidy(text: str) -> str:
    """Fix what cutting leaves behind: doubled spaces, stray commas, 'is with no'."""
    text = re.sub(
        r"\b(is|are)\s+with\s+no\b", lambda m: "has no" if m.group(1) == "is" else "have no", text
    )
    text = re.sub(r"\b(is|are)\s+(?=[,.;])", "", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def scrub(text: str) -> tuple[str, int]:
    """Text without descriptions of how people look, and how many edits that took."""
    if not text:
        return text, 0
    kept: list[str] = []
    edits = 0
    for sentence in SENTENCE.split(text.strip()):
        if LOOKS.search(sentence):
            edits += 1
            continue
        cleaned = COLOUR_SHIRTED.sub("", sentence)
        cleaned = COLOUR_WORN.sub(r"\1", cleaned)
        cleaned = _cut_garments(cleaned)
        if cleaned != sentence:
            edits += 1
            cleaned = _tidy(cleaned)
        if cleaned:
            kept.append(cleaned)
    return " ".join(kept), edits


def scrub_model[M: BaseModel](model: M) -> tuple[M, int]:
    """A copy of `model` with every string (in lists and nested models too) scrubbed."""
    total = 0

    def clean(value: object) -> object:
        nonlocal total
        if isinstance(value, str):
            text, edits = scrub(value)
            total += edits
            return text
        if isinstance(value, list):
            return [clean(v) for v in value]
        if isinstance(value, BaseModel):
            return value.model_copy(
                update={k: clean(getattr(value, k)) for k in type(value).model_fields}
            )
        return value

    fields = {name: clean(getattr(model, name)) for name in type(model).model_fields}
    return model.model_copy(update=fields), total
