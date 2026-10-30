"""A scripted transport that serves recorded-style replies and records every request."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from rulecam.clients.meta_hosted.sam_wire import FILES_PATH, WireMask

SAM_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "replay" / "sam"


def load_reply(name: str) -> dict[str, object]:
    """Load one recorded-style reply envelope from `tests/fixtures/replay/sam/`."""
    data: dict[str, object] = json.loads((SAM_FIXTURES / f"{name}.json").read_text("utf-8"))
    return data


class ScriptedTransport:
    """Answers `/files` with an upload reply and other paths with queued replies, in order."""

    def __init__(self, *replies: Mapping[str, object]) -> None:
        self._replies: deque[Mapping[str, object]] = deque(replies)
        self.requests: list[Mapping[str, object]] = []

    @property
    def segment_requests(self) -> list[Mapping[str, object]]:
        return [r for r in self.requests if r["path"] != FILES_PATH]

    @property
    def uploads(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.requests if r["path"] == FILES_PATH]

    def bodies(self) -> list[dict[str, Any]]:
        """The JSON bodies of every non-upload request, in order."""
        out = []
        for request in self.segment_requests:
            body = request["json"]
            assert isinstance(body, dict)
            out.append(body)
        return out

    def prompt_texts(self) -> list[str]:
        return [str(body["input"][0]["content"][0]["text"]) for body in self.bodies()]

    def __call__(self, request: Mapping[str, object]) -> Mapping[str, object]:
        self.requests.append(request)
        if request["path"] == FILES_PATH:
            return load_reply("upload_ok")
        if not self._replies:
            msg = f"no scripted reply left for {request['path']}"
            raise AssertionError(msg)
        return self._replies.popleft()


def ones_decoder(mask: WireMask) -> NDArray[np.uint8]:
    """A stand-in `MaskDecoder`: the whole raster is foreground."""
    return np.ones((mask.height, mask.width), dtype=np.uint8)
