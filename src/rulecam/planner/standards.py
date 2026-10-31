"""Standards grounding and the citation text check (task P2-05, MP §3.2).

Spark 1.3 with the web-search tool proposes, per concern, a `Citation{ref, url, quoted_text}`.
A citation counts only if its quote is really on the cited page: `check_citation` fetches the
page and `citation_text_match` looks for the whitespace-normalised quote in its text. That is
what catches invented citations, and a failed match fails acceptance (P2-06).

Pages are fetched through a `ReplayCache` rooted at `data/standards_cache/` (provider `web`):
`record` stores the page text, `replay` serves it and raises `ReplayMiss` on a miss, so unit
tests and demos never touch the network. The URL comes from a model, so it is untrusted: only
`https` to a public host name is fetched, redirects are re-checked hop by hop, and pages are
size-capped. Page text and concern text are data, never instructions.
"""

from __future__ import annotations

import ipaddress
import json
import re
import unicodedata
from collections.abc import Callable
from html.parser import HTMLParser
from typing import Final, Literal, Protocol, TypeVar
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import BaseModel

from rulecam.clock import SystemClock
from rulecam.config import Settings
from rulecam.contracts.core import Frozen
from rulecam.log import get_logger
from rulecam.planner.errors import StandardsFetchError
from rulecam.planner.models import DATA_NOT_INSTRUCTIONS_CLAUSE, Concern
from rulecam.replay.cache import ReplayCache

_log = get_logger(__name__)

M = TypeVar("M", bound=BaseModel)

GROUNDING_MODEL: Final = "muse-spark-1.3"
MAX_SEARCH_CALLS: Final = 4
STANDARDS_PROVIDER: Final = "web"
STANDARDS_MODEL: Final = "standards-page"
STANDARDS_CACHE_DIRNAME: Final = "standards_cache"
MIN_QUOTE_CHARS: Final = 20
MAX_PAGE_BYTES: Final = 10 * 1024 * 1024
MAX_REDIRECTS: Final = 5
FETCH_TIMEOUT_S: Final = 15.0
_REDIRECT_STATUSES: Final = frozenset({301, 302, 303, 307, 308})

STANDARDS_SYSTEM_PROMPT = (
    "You are Rulecam's standards researcher. For the safety or compliance concern you are given, "
    "use web search to find the governing published standard (e.g. the FDA Food Code, OSHA "
    "29 CFR 1910/1926, NFPA 101, or a local code). Return its section reference (e.g. "
    "'Food Code §2-301.12'), the URL of the page you read it on, and a verbatim quote of at "
    "least one full sentence copied exactly from that page. Never paraphrase the quote and never "
    "cite a page you did not open; if you cannot find one, return no citation.\n"
    f"{DATA_NOT_INSTRUCTIONS_CLAUSE}"
)

PageGetter = Callable[[str], str]


class GroundingLLM(Protocol):
    """Structured output with the web-search tool (satisfied by `SparkClient`)."""

    def structured(
        self,
        *,
        model: str,
        messages: list[dict[str, object]],
        schema: type[M],
        reasoning_effort: Literal["low", "medium", "high"] = "low",
        enable_web_search: bool = False,
        max_tool_calls: int | None = None,
    ) -> M:
        """Return the model's answer validated against `schema`."""
        ...


class Citation(Frozen):
    """A clause of a published standard, with a verbatim quote from the cited page."""

    ref: str
    url: str
    quoted_text: str


class GroundedCitation(BaseModel):
    """Spark's answer: a citation, or none when it found no governing standard."""

    citation: Citation | None = None


class CitationCheck(Frozen):
    """The result of `citation_text_match` for one citation."""

    citation: Citation
    matched: bool
    detail: str


def ground_concern_standard(concern: Concern | str, *, spark: GroundingLLM) -> Citation | None:
    """Ask Spark, with web search, which standard governs a concern.

    The returned citation is a claim until `check_citation` confirms it.

    Args:
        concern: The concern (its `standard_ref`, if any, is passed as a hint).
        spark: Structured output with web search.

    Returns:
        The proposed citation, or `None`.
    """
    data: dict[str, str] = {"concern": concern if isinstance(concern, str) else concern.text}
    if isinstance(concern, Concern) and concern.standard_ref:
        data["standard_ref_hint"] = concern.standard_ref
    result = spark.structured(
        model=GROUNDING_MODEL,
        messages=[
            {"role": "system", "content": STANDARDS_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
        ],
        schema=GroundedCitation,
        enable_web_search=True,
        max_tool_calls=MAX_SEARCH_CALLS,
    )
    return result.citation


_TRANSLATE: Final = str.maketrans(
    {0x201C: '"', 0x201D: '"', 0x2018: "'", 0x2019: "'", 0x2013: "-", 0x2014: "-"}
)


def normalize_text(text: str) -> str:
    """Normalise text for quote matching.

    Applies Unicode NFKC (so non-breaking spaces become spaces), curly quotes and en/em dashes
    to ASCII, case folding, and collapses every whitespace run to one space.

    Args:
        text: Any text.

    Returns:
        The normalised text.
    """
    folded = unicodedata.normalize("NFKC", text).translate(_TRANSLATE).casefold()
    return re.sub(r"\s+", " ", folded).strip()


def citation_text_match(citation: Citation, page_text: str) -> bool:
    """Return True when the citation's quote occurs in the page text.

    The quote must be at least 20 characters after normalisation, so a fragment like "food"
    cannot pass.

    Args:
        citation: The citation to check.
        page_text: The text of the cited page.

    Returns:
        Whether the quote occurs exactly or after `normalize_text` on both sides.
    """
    quote = normalize_text(citation.quoted_text)
    if len(quote) < MIN_QUOTE_CHARS:
        return False
    return citation.quoted_text in page_text or quote in normalize_text(page_text)


class _TextExtractor(HTMLParser):
    """Collects visible text; block-level tags become spaces, inline tags join their text."""

    _SKIP: Final = frozenset({"script", "style", "noscript", "template"})
    _BLOCK: Final = frozenset(
        {
            "p",
            "div",
            "br",
            "li",
            "ul",
            "ol",
            "tr",
            "td",
            "th",
            "table",
            "section",
            "article",
            "header",
            "footer",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "blockquote",
            "pre",
            "dd",
            "dt",
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        self._skipping += tag in self._SKIP
        if tag in self._BLOCK:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skipping:
            self._skipping -= 1
        if tag in self._BLOCK:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """Return the visible text of an HTML page (no scripts or styles, entities decoded).

    Args:
        html: The page source.

    Returns:
        Its text; block elements are separated by spaces, inline markup (`<b>`, `<a>`) is
        removed without splitting words.
    """
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return "".join(parser.parts)


def check_fetchable(url: str) -> None:
    """Refuse URLs that are not `https` to a public host name.

    Args:
        url: A URL proposed by the model.

    Raises:
        StandardsFetchError: For another scheme, credentials in the URL, `localhost`, or an IP
            literal that is not globally routable.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.username or parts.password:
        msg = f"only https URLs to a host are fetched, got {url!r}"
        raise StandardsFetchError(msg)
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        msg = f"refusing to fetch a local host: {host}"
        raise StandardsFetchError(msg)
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if not address.is_global:
        msg = f"refusing to fetch a non-public address: {host}"
        raise StandardsFetchError(msg)


def https_get_text(url: str, *, transport: httpx.BaseTransport | None = None) -> str:
    """Fetch a standards page and return its text (the live `PageGetter`).

    Args:
        url: The page URL.
        transport: The httpx transport (tests inject `httpx.MockTransport`).

    Returns:
        The page text; HTML is reduced to its visible text.

    Raises:
        StandardsFetchError: If the URL is refused, the fetch fails, the page is too large, or
            it is not HTML or plain text (PDF standards need a text page or a manual upload).
    """
    with httpx.Client(
        transport=transport, timeout=FETCH_TIMEOUT_S, follow_redirects=False
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            check_fetchable(url)
            try:
                reply = client.get(url)
            except httpx.HTTPError as err:
                msg = f"fetching {url} failed: {err}"
                raise StandardsFetchError(msg) from err
            if reply.status_code not in _REDIRECT_STATUSES:
                return _reply_text(url, reply)
            url = urljoin(url, reply.headers.get("location", ""))
    msg = f"too many redirects fetching {url}"
    raise StandardsFetchError(msg)


def _reply_text(url: str, reply: httpx.Response) -> str:
    if reply.status_code != httpx.codes.OK:
        msg = f"fetching {url} returned HTTP {reply.status_code}"
        raise StandardsFetchError(msg)
    if len(reply.content) > MAX_PAGE_BYTES:
        msg = f"{url} is larger than {MAX_PAGE_BYTES} bytes"
        raise StandardsFetchError(msg)
    content_type = reply.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type in {"text/html", "application/xhtml+xml"}:
        return html_to_text(reply.text)
    if content_type == "text/plain":
        return reply.text
    msg = f"{url} is {content_type or 'of unknown type'}, not an HTML or text page"
    raise StandardsFetchError(msg)


class StandardsLibrary:
    """Standards page text, served through a replay cache."""

    def __init__(self, cache: ReplayCache, *, get: PageGetter = https_get_text) -> None:
        """Create the library.

        Args:
            cache: Replay cache for page text (normally rooted at `data/standards_cache/`).
            get: Fetches a page's text in `live` and `record` mode.
        """
        self._cache = cache
        self._get = get

    @classmethod
    def from_settings(cls, settings: Settings) -> StandardsLibrary:
        """Build a library on `<RULECAM_DATA_DIR>/standards_cache` in `RULECAM_MODE`.

        Args:
            settings: Application settings.

        Returns:
            The library.
        """
        directory = settings.rulecam_data_dir / STANDARDS_CACHE_DIRNAME
        return cls(ReplayCache(directory, settings.rulecam_mode, clock=SystemClock()))

    def page_text(self, url: str) -> str:
        """Return the text of the page at `url`.

        Args:
            url: The page URL.

        Returns:
            The page text.

        Raises:
            ReplayMiss: In replay mode, if the page was never recorded.
            StandardsFetchError: In live or record mode, if the page cannot be fetched.
        """
        entry = self._cache.call(
            STANDARDS_PROVIDER, STANDARDS_MODEL, {"url": url}, lambda: {"text": self._get(url)}
        )
        text = entry.response.get("text")
        if not isinstance(text, str):
            msg = f"cached standards entry for {url} has no text"
            raise StandardsFetchError(msg)
        return text


def check_citation(citation: Citation, library: StandardsLibrary) -> CitationCheck:
    """Fetch the cited page and check that the quote is on it.

    A page that cannot be fetched fails the check (with the reason in `detail`); a replay miss
    is a setup error and propagates.

    Args:
        citation: The citation to verify.
        library: Where page text comes from.

    Returns:
        The check result.
    """
    try:
        page = library.page_text(citation.url)
    except StandardsFetchError as err:
        _log.warning("citation_fetch_failed", ref=citation.ref, error=str(err))
        return CitationCheck(citation=citation, matched=False, detail=f"unverifiable: {err}")
    matched = citation_text_match(citation, page)
    detail = "quote found on the cited page" if matched else "quote not found on the cited page"
    _log.info("citation_checked", ref=citation.ref, matched=matched)
    return CitationCheck(citation=citation, matched=matched, detail=detail)
