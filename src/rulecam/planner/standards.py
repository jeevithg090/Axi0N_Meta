"""Standards grounding and citation verification (task P2-05, MP §3.2).

Extracts formal regulatory citations (e.g. FDA Food Code §2-301.12, OSHA 1926.501)
using Spark 1.3 web-search grounding, and rigorously verifies that quoted text
appears verbatim in retrieved source documents (`citation_text_match`).
Caches retrieved regulatory texts under `data/standards_cache/`.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import httpx
from pydantic import BaseModel, Field

from rulecam.clients.meta_hosted.spark import SPARK_1_3
from rulecam.contracts import StructuredLLM
from rulecam.contracts.core import Frozen
from rulecam.log import get_logger
from rulecam.planner.models import DATA_NOT_INSTRUCTIONS_CLAUSE, Concern
from rulecam.replay.errors import ReplayMiss

_log = get_logger(__name__)

DEFAULT_STANDARDS_CACHE_DIR = Path("data/standards_cache")

STANDARDS_GROUNDING_PROMPT = (
    "You are Rulecam's regulatory compliance officer. Given a safety or operational concern, "
    "retrieve the authoritative official regulation (e.g. FDA Food Code, OSHA 1910/1926, NFPA 101, "
    "or local health codes). Return exact citation reference (e.g. 'Food Code §2-301.12'), "
    "the official URL, and the verbatim excerpt from the standard that mandates this rule.\n"
    f"{DATA_NOT_INSTRUCTIONS_CLAUSE}"
)


class Citation(Frozen):
    """An official regulatory or industry standard citation."""

    ref: str
    url: str
    quoted_text: str


class GroundedCitationResponse(BaseModel):
    """Schema for Spark grounded citation return."""

    citation: Citation | None = Field(default=None)


def normalize_text(text: str) -> str:
    """Normalize whitespace and punctuation for robust citation matching."""
    # Collapse all whitespace sequences into a single space
    cleaned = re.sub(r"\s+", " ", text).strip().lower()
    # Normalize curved quotes and dashes to plain ascii
    return (
        cleaned.replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u2014", "-")
        .replace("\u2013", "-")
    )


def citation_text_match(citation: Citation, page_text: str) -> bool:
    """Verify that `citation.quoted_text` occurs in `page_text`.

    Both exact substring matching and normalized whitespace matching are checked.

    Args:
        citation: The Citation holding the quoted regulatory clause.
        page_text: The full text of the regulatory webpage or document.

    Returns:
        True if the quoted clause is verified in the page text; False otherwise.
    """
    if not citation.quoted_text.strip():
        return False

    # 1. Exact match
    if citation.quoted_text in page_text:
        return True

    # 2. Whitespace- and punctuation-normalized match
    norm_quote = normalize_text(citation.quoted_text)
    norm_page = normalize_text(page_text)
    return norm_quote in norm_page


class StandardsCache:
    """Local disk cache for retrieved regulatory standard pages."""

    def __init__(self, cache_dir: Path = DEFAULT_STANDARDS_CACHE_DIR) -> None:
        """Initialise standards cache directory."""
        self.cache_dir = cache_dir

    def _url_key(self, url: str) -> str:
        return hashlib.sha256(url.strip().lower().encode("utf-8")).hexdigest()

    def get(self, url: str) -> str | None:
        """Retrieve cached text for `url` if present on disk."""
        key = self._url_key(url)
        path = self.cache_dir / f"{key}.txt"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    def put(self, url: str, text: str) -> None:
        """Store document text on disk under the url's hash key."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        key = self._url_key(url)
        path = self.cache_dir / f"{key}.txt"
        path.write_text(text, encoding="utf-8")


def fetch_standard_text(
    url: str,
    *,
    cache: StandardsCache | None = None,
    mode: str = "live",
) -> str:
    """Fetch text of a regulation from web or disk cache according to execution mode.

    Args:
        url: The web URL of the standard page.
        cache: Optional StandardsCache (defaults to standard cache dir).
        mode: Execution mode ('live', 'record', 'replay').

    Returns:
        The text content of the page.

    Raises:
        ReplayMiss: When running in 'replay' mode and the url is not in cache.
    """
    active_cache = cache or StandardsCache()

    # Replay mode: must hit disk cache
    if mode == "replay":
        cached = active_cache.get(url)
        if cached is None:
            raise ReplayMiss(provider="standards", model="web", key=active_cache._url_key(url))
        return cached

    # Live or record mode: check cache first, otherwise fetch
    cached = active_cache.get(url)
    if cached is not None:
        return cached

    # Perform live fetch
    try:
        resp = httpx.get(url, timeout=10.0, follow_redirects=True)
        resp.raise_for_status()
        text = resp.text
    except Exception as err:
        _log.warning("standards_fetch_failed", url=url, error=str(err))
        return ""

    if mode == "record":
        active_cache.put(url, text)

    return text


def ground_concern_standard(
    concern: Concern | str,
    *,
    spark: StructuredLLM,
) -> Citation | None:
    """Query Spark 1.3 with web search grounding to identify the governing standard.

    Args:
        concern: The safety or compliance concern.
        spark: StructuredLLM client.

    Returns:
        Citation if an official standard was identified, or None.
    """
    text = concern.text if isinstance(concern, Concern) else str(concern)
    ref_hint = (
        f" (Existing citation hint: {concern.standard_ref})"
        if isinstance(concern, Concern) and concern.standard_ref
        else ""
    )
    user_prompt = f"Safety concern: {text}{ref_hint}\nFind governing regulation and verbatim text:"

    res = spark.structured(
        model=SPARK_1_3,
        messages=[
            {"role": "system", "content": STANDARDS_GROUNDING_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        schema=GroundedCitationResponse,
    )
    return res.citation
