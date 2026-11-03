"""Where model reasoning is kept: `data/reasoning/<id>.txt`, content-addressed.

Spark Open and Glimmer both return `message.reasoning_content` (llama.cpp and Baseten parse the
thinking out of the reply). It is stored verbatim under its content hash and referenced by id, so
the UI and the evidence log can point at why a model decided what it did, and replayed runs write
byte-identical files.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Final

REASONING_ID_HEX: Final = 16


def reasoning_id(text: str) -> str:
    """Return the content-addressed id of a reasoning text (`so_` + 16 hex of its SHA-256).

    The same reasoning always gets the same id, so replayed runs are byte-identical (§5.4).
    """
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"so_{digest[:REASONING_ID_HEX]}"


class ReasoningStore:
    """Writes reasoning text to `<directory>/<id>.txt` and returns the id."""

    def __init__(self, directory: Path) -> None:
        """Create the store.

        Args:
            directory: Usually `data/reasoning`.
        """
        self._directory = directory

    def save(self, text: str) -> str:
        """Store `text` under its content-addressed id and return the id."""
        reasoning_id_ = reasoning_id(text)
        self._directory.mkdir(parents=True, exist_ok=True)
        (self._directory / f"{reasoning_id_}.txt").write_text(text, encoding="utf-8")
        return reasoning_id_

    def load(self, reasoning_id: str) -> str:
        """Return stored reasoning by id.

        Raises:
            ValueError: If the id is not a plain stored-reasoning id.
            FileNotFoundError: If nothing is stored under it.
        """
        if not reasoning_id.startswith("so_") or not reasoning_id[3:].isalnum():
            msg = f"invalid reasoning id {reasoning_id!r}"
            raise ValueError(msg)
        return (self._directory / f"{reasoning_id}.txt").read_text(encoding="utf-8")
