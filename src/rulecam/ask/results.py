"""Tool results as the text Spark reads, cut only when they must be, and never silently.

A turn's results share one character budget (`RULECAM_ASK_MAX_RESULT_CHARS`). Small results go
through whole; what is left is shared among the large ones. A result over its share is cut so
that it stays valid JSON and says what was cut:

- a table (`rows`) keeps as many leading rows as fit and gains `rows_shown` and a `note`
  ("showing 412 of 1000 rows ...");
- several tables (`results`) share the budget the same way;
- anything else becomes `{"truncated_at_chars", "total_chars", "note", "partial_json"}`, the
  start of its JSON text as a string.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Final

DEFAULT_MAX_RESULT_CHARS: Final = 100_000
_ENVELOPE_CHARS: Final = 400  # room for the cut note and the wrapper keys


def dumps(value: object) -> str:
    """JSON text of a tool result (timestamps and other objects as strings)."""
    return json.dumps(value, default=str, ensure_ascii=False)


def fit_result(result: object, max_chars: int) -> str:
    """The result as JSON text of at most about `max_chars` characters, saying what was cut.

    Args:
        result: A tool's result (JSON-friendly).
        max_chars: The character budget.

    Returns:
        Valid JSON text; when cut, it says how much is shown.
    """
    text = dumps(result)
    if len(text) <= max_chars:
        return text
    if isinstance(result, Mapping) and isinstance(result.get("rows"), list):
        return dumps(_fit_rows(result, max_chars, len(text)))
    if isinstance(result, Mapping) and isinstance(result.get("results"), list):
        parts = fit_results(result["results"], max_chars - _ENVELOPE_CHARS)
        fitted = dumps({**result, "results": [json.loads(p) for p in parts]})
        if len(fitted) <= max_chars:
            return fitted
    return dumps(_partial(text, max_chars))


def fit_results(results: Sequence[object], max_chars: int) -> list[str]:
    """Several results within one budget: small ones whole, the rest share what is left.

    Args:
        results: The results, in order.
        max_chars: The budget for all of them together.

    Returns:
        Each result's JSON text, in the same order.
    """
    texts = [dumps(r) for r in results]
    shares = [0] * len(texts)
    left, waiting = max_chars, len(texts)
    for i in sorted(range(len(texts)), key=lambda k: len(texts[k])):
        shares[i] = min(len(texts[i]), max(0, left) // waiting)
        left -= shares[i]
        waiting -= 1
    return [
        text if len(text) <= share else fit_result(result, share)
        for result, text, share in zip(results, texts, shares, strict=True)
    ]


def _fit_rows(result: Mapping[str, object], max_chars: int, total_chars: int) -> dict[str, object]:
    """The table with as many leading rows as fit, and a note on how many that is."""
    rows: list[object] = result["rows"]  # type: ignore[assignment]
    total = result.get("total_rows") or len(rows)

    def cut(k: int) -> dict[str, object]:
        return {
            **result,
            "rows": rows[:k],
            "truncated": True,
            "rows_shown": k,
            "note": (
                f"showing {k} of {total} rows: the result was {total_chars} characters and "
                f"the limit is {max_chars}; select fewer columns, aggregate, or page with "
                "LIMIT/OFFSET to see the rest"
            ),
        }

    low, high = 0, len(rows)
    while low < high:
        mid = (low + high + 1) // 2
        if len(dumps(cut(mid))) <= max_chars:
            low = mid
        else:
            high = mid - 1
    return cut(low)


def _partial(text: str, max_chars: int) -> dict[str, object]:
    """The start of an over-long result as a string, wrapped so it stays valid JSON."""
    keep = max(0, max_chars - _ENVELOPE_CHARS)
    # Escaping can lengthen the kept text; shrink until the wrapper fits the budget.
    while True:
        wrapped = {
            "truncated_at_chars": keep,
            "total_chars": len(text),
            "note": (
                f"this result was {len(text)} characters and is cut at {keep}: partial_json "
                "is the start of its JSON text; ask for less (fewer instants, a narrower "
                "query) to see all of it"
            ),
            "partial_json": text[:keep],
        }
        if keep == 0 or len(dumps(wrapped)) <= max_chars:
            return wrapped
        keep = max(0, keep - max(1, len(dumps(wrapped)) - max_chars))
