"""Open-weight inference evidence log (`evidence/<model>/inference-log.jsonl`).

The open-weight bonus needs proof that Glimmer and Spark Open actually ran. Each call appends one
JSON line with the model, quantisation, hardware, latency and a hash of the request. Prompts and
images are never written here.
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from pydantic import Field

from rulecam.contracts import Frozen


class InferenceLine(Frozen):
    """One logged model call.

    Attributes:
        ts: When the call finished (UTC).
        model: Model name.
        quant: Quantisation, empty when hosted.
        hardware: Where it ran.
        latency_ms: Wall-clock latency including retries.
        request_hash: SHA-256 of the request body.
        ok: Whether a reply was received.
        request_id: Provider request ID, when the provider returns one (Baseten).
        finish_reason: Why generation stopped (`stop`, or `length` when cut at `max_tokens`).
        usage: Token counts and server timings the reply reported (`prompt_tokens`,
            `completion_tokens`, `reasoning_tokens`, `predicted_ms`, ...).
        reasoning_ref: Id of the stored reasoning (`data/reasoning/<id>.txt`), when there was some.
    """

    ts: datetime
    model: str
    quant: str = ""
    hardware: str = ""
    latency_ms: int = Field(ge=0)
    request_hash: str
    ok: bool
    request_id: str | None = None
    finish_reason: str | None = None
    usage: dict[str, float] = Field(default_factory=dict)
    reasoning_ref: str | None = None


class EvidenceLog:
    """Appends `InferenceLine`s to a JSONL file (thread-safe within one process)."""

    def __init__(self, path: Path) -> None:
        """Create the log; the file and its directory are created on first append.

        Args:
            path: The JSONL file.
        """
        self._path = path
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        """The JSONL file."""
        return self._path

    def append(self, line: InferenceLine) -> None:
        """Append one line.

        Args:
            line: The call to record.
        """
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line.model_dump_json() + "\n")
