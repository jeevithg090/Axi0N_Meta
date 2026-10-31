"""Soak the self-hosted SAM server with one chunk and watch GPU memory (task P1-03).

Runs on the GPU pod with the sam venv's Python (standard library only), against the local
server, with the key from the mode-600 file (never argv):

    SAM_API_KEY="$(cat /workspace/.sam_api_key)" /workspace/sam-venv/bin/python \
        /workspace/rulecam/scripts/sam_pod/soak.py --calls 20 --concept forklift chunk.mp4

Each call posts the chunk as a text-prompt `/segment` request and prints the status, objects,
latency and `/health` memory (allocated / reserved / peak GiB, guard trips). The exit code is 1 when
a call fails or allocated memory after the last call is more than `--max-growth-gb` above the
level after the first call: the leak check for the live OOM finding.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

GIB = 1024**3
DEFAULT_URL = "http://127.0.0.1:8090"

Post = Callable[[dict[str, Any]], tuple[int, dict[str, Any]]]
Health = Callable[[], dict[str, Any]]


def segment_body(
    video: bytes, concept: str, width: int, height: int, n_frames: int
) -> dict[str, Any]:
    """Build a `/segment` body: a forward text-prompt `SamRequest` and the base64 chunk.

    Args:
        video: The MP4 bytes.
        concept: The text prompt.
        width: Frame width in pixels.
        height: Frame height in pixels.
        n_frames: Frames in the chunk.

    Returns:
        The JSON body.
    """
    request = {
        "text": concept,
        "box_prompts": [],
        "negative_points": [],
        "direction": "forward",
        "include_confidence": True,
        "width": width,
        "height": height,
        "n_frames": n_frames,
        "video_sha256": "0" * 64,
    }
    return {"request": request, "video_b64": base64.b64encode(video).decode("ascii")}


def memory_line(health: Mapping[str, Any]) -> str:
    """Summarise `/health` memory in GiB.

    Args:
        health: The `/health` JSON.

    Returns:
        `alloc=… reserved=… peak=… guard_trips=…`, or `memory=unavailable`.
    """
    memory = health.get("memory") or {}
    if "allocated_bytes" not in memory:
        return "memory=unavailable"
    return (
        f"alloc={memory['allocated_bytes'] / GIB:.2f} "
        f"reserved={memory['reserved_bytes'] / GIB:.2f} "
        f"peak={memory.get('peak_allocated_bytes', 0) / GIB:.2f} "
        f"guard_trips={memory.get('guard_trips', 0)}"
    )


def soak(
    calls: int,
    body: dict[str, Any],
    post: Post,
    health: Health,
    *,
    max_growth_gb: float,
    out: Callable[[str], None] = print,
    timer: Callable[[], float] = time.perf_counter,
) -> int:
    """Post the same body `calls` times and check that allocated memory stays flat.

    Args:
        calls: How many `/segment` calls.
        body: The request body.
        post: Sends the body, returns `(status, json)`.
        health: Reads `/health`.
        max_growth_gb: Allowed growth of allocated memory from the first to the last call.
        out: Prints one line.
        timer: Monotonic seconds.

    Returns:
        0 when every call succeeded and memory stayed within the limit, otherwise 1.
    """
    out(f"start {memory_line(health())}")
    failed = 0
    allocated: list[int] = []
    for i in range(1, calls + 1):
        started = timer()
        status, reply = post(body)
        ms = round((timer() - started) * 1000)
        state = health()
        allocated.append(int((state.get("memory") or {}).get("allocated_bytes", 0)))
        if status == 200:  # noqa: PLR2004
            detail = f"objects={len(reply.get('objects', ()))}"
        else:
            failed += 1
            detail = f"error={reply.get('error', {}).get('message', reply)}"
        out(f"call {i:3d} status={status} {detail} ms={ms} {memory_line(state)}")
    growth = (allocated[-1] - allocated[0]) / GIB if allocated else 0.0
    ok = failed == 0 and growth <= max_growth_gb
    out(f"{'PASS' if ok else 'FAIL'}: {failed} failed calls, allocated growth {growth:.2f} GiB")
    return 0 if ok else 1


def probe(path: Path) -> tuple[int, int, int]:  # pragma: no cover - needs opencv and a chunk
    """Read width, height and frame count of a chunk with OpenCV (in the sam venv).

    Args:
        path: The chunk.

    Returns:
        `(width, height, n_frames)`, the frames counted by decoding, as sam3 does.
    """
    import cv2  # type: ignore[import-not-found]  # noqa: PLC0415 - only in the sam venv

    capture = cv2.VideoCapture(str(path))
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    n_frames = 0
    while capture.read()[0]:
        n_frames += 1
    capture.release()
    return width, height, n_frames


def http_client(base_url: str, api_key: str) -> tuple[Post, Health]:  # pragma: no cover - network
    """Build `post` and `health` for a server.

    Args:
        base_url: The server, e.g. `http://127.0.0.1:8090`.
        api_key: The Bearer key.

    Returns:
        `(post, health)`.
    """

    def post(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        request = urllib.request.Request(  # noqa: S310 - the operator's own server URL
            f"{base_url}/segment",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as reply:  # noqa: S310
                return reply.status, json.loads(reply.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def health() -> dict[str, Any]:
        with urllib.request.urlopen(f"{base_url}/health", timeout=10) as reply:  # noqa: S310
            result: dict[str, Any] = json.loads(reply.read())
            return result

    return post, health


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - needs the GPU pod
    """Parse arguments and soak the server.

    Args:
        argv: Command-line arguments (defaults to `sys.argv[1:]`).

    Returns:
        The exit code of `soak`.
    """
    parser = argparse.ArgumentParser(description="Soak the self-hosted SAM server.")
    parser.add_argument("chunk", type=Path)
    parser.add_argument("--concept", default="person")
    parser.add_argument("--calls", type=int, default=20)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--max-growth-gb", type=float, default=0.5)
    args = parser.parse_args(argv)
    api_key = os.environ.get("SAM_API_KEY", "")
    if not api_key:
        msg = "SAM_API_KEY must be set (from /workspace/.sam_api_key)"
        raise SystemExit(msg)
    width, height, n_frames = probe(args.chunk)
    body = segment_body(args.chunk.read_bytes(), args.concept, width, height, n_frames)
    post, health = http_client(args.url.rstrip("/"), api_key)
    return soak(args.calls, body, post, health, max_growth_gb=args.max_growth_gb)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
