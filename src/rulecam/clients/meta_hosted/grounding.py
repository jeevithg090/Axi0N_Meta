"""What a search-grounded Muse Spark reply was grounded on (`SparkClient.grounded`).

With `web_search` and `include: ["web_search_call.results"]`, each `web_search_call` output item
lists the pages the search returned (`results`: `url`, `title`, `snippet`), and the answer's
`output_text` parts carry `url_citation` annotations (the subset it cited). Search grounding
docs, read Nov 3 2026: https://dev.meta.ai/docs/search-grounding.

`normal_url` makes two spellings of one page compare equal (scheme and host case, `www.`, a
trailing slash, the fragment and `utm_*` tracking parameters), so a claim's source can be checked
against what was actually retrieved.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from rulecam.contracts import Frozen

INCLUDE_RESULTS = "web_search_call.results"


class SearchSource(Frozen):
    """One page a `web_search_call` returned."""

    url: str
    title: str = ""
    snippet: str = ""


@dataclass(frozen=True)
class Grounded[T]:
    """A validated answer with the pages its searches returned and the URLs it cited.

    Attributes:
        answer: The answer, validated against the requested schema.
        sources: Every page the searches returned (deduplicated by `normal_url`).
        cited: URLs of the answer's `url_citation` annotations.
        searches: How many `web_search_call` items ran.
    """

    answer: T
    sources: tuple[SearchSource, ...] = ()
    cited: tuple[str, ...] = ()
    searches: int = 0
    _index: frozenset[str] = field(default=frozenset(), repr=False, compare=False)

    def retrieved(self, url: str) -> SearchSource | None:
        """The returned page `url` names (after `normal_url`), or `None` if no search saw it."""
        key = normal_url(url)
        if key not in self._index:
            return None
        return next((s for s in self.sources if normal_url(s.url) == key), None)


def normal_url(url: str) -> str:
    """`url` with scheme and host lowercased, no `www.`, fragment, `utm_*` or trailing slash."""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    query = urlencode(
        [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not k.startswith("utm_")
        ]
    )
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower() or "https", host, path, query, ""))


def _items(body: Mapping[str, object], kind: str) -> Iterable[Mapping[str, object]]:
    output = body.get("output")
    for item in output if isinstance(output, list) else []:
        if isinstance(item, Mapping) and item.get("type") == kind:
            yield item


def search_sources(body: Mapping[str, object]) -> tuple[list[SearchSource], int]:
    """The pages every `web_search_call` of a reply returned, and how many searches ran."""
    sources: list[SearchSource] = []
    searches = 0
    for call in _items(body, "web_search_call"):
        searches += 1
        results = call.get("results")
        for hit in results if isinstance(results, list) else []:
            if isinstance(hit, Mapping) and isinstance(hit.get("url"), str) and hit["url"]:
                sources.append(
                    SearchSource(
                        url=str(hit["url"]),
                        title=str(hit.get("title") or ""),
                        snippet=str(hit.get("snippet") or ""),
                    )
                )
    return sources, searches


def cited_urls(body: Mapping[str, object]) -> list[str]:
    """URLs of the `url_citation` annotations on the reply's `output_text` parts."""
    urls: list[str] = []
    for message in _items(body, "message"):
        content = message.get("content")
        for part in content if isinstance(content, list) else []:
            annotations = part.get("annotations") if isinstance(part, Mapping) else None
            for note in annotations if isinstance(annotations, list) else []:
                if isinstance(note, Mapping) and note.get("type") == "url_citation":
                    url = note.get("url")
                    if isinstance(url, str) and url:
                        urls.append(url)
    return urls


def grounded[T](answer: T, bodies: Iterable[Mapping[str, object]]) -> Grounded[T]:
    """Collect sources and citations over every reply of one grounded call (a retry included)."""
    sources: dict[str, SearchSource] = {}
    cited: list[str] = []
    searches = 0
    for body in bodies:
        found, n = search_sources(body)
        searches += n
        for source in found:
            sources.setdefault(normal_url(source.url), source)
        cited += [u for u in cited_urls(body) if u not in cited]
    return Grounded(
        answer=answer,
        sources=tuple(sources.values()),
        cited=tuple(cited),
        searches=searches,
        _index=frozenset(sources),
    )
