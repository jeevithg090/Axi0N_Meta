"""Hosted vs self-hosted SAM 3.1 on the same chunks: detections, frames, latency, agreement.

Run on the API pod (task P1-03), with `META_API_KEY` and `META_API_BASE_URL` for hosted SAM and
`SAM_BASE_URL` plus `SAM_API_KEY` for the GPU pod, exported the way `start.sh` does:

    RULECAM_MODE=live uv run python scripts/sam_pod/compare.py \
        --concept forklift --concept person /workspace/chunks/cam1/*.mp4

Every chunk is segmented once per concept by each backend (text prompt, forward, with
confidences), exactly as the runtime calls them. Each row prints the detections, the frames with
at least one detection, the latency and `agree`: the share of the chunk's frames on which both
backends agree whether the concept is present. The self-hosted `/health` memory is printed before
and after the run, so the same run checks that GPU memory comes back after every session.
`--min-agreement` makes the exit code 1 when any row (or a failed call) falls below it.
Hosted calls are billed (at least 50 frames each) and go through the replay cache in `live` mode.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rulecam.contracts.core import Chunk, Detection
from rulecam.contracts.protocols import Segmenter

GIB = 1024**3
COLUMNS = (
    ("chunk", 28),
    ("concept", 14),
    ("frames", 6),
    ("h_det", 6),
    ("s_det", 6),
    ("h_frm", 6),
    ("s_frm", 6),
    ("h_ms", 7),
    ("s_ms", 7),
    ("agree", 6),
)

Probe = Callable[[Path], Chunk]
HealthFetch = Callable[[], Mapping[str, Any] | None]


@dataclass(frozen=True)
class Run:
    """One backend's answer for one chunk and concept.

    Attributes:
        detections: How many detections came back.
        frames: Frame indices with at least one detection.
        latency_ms: Wall-clock milliseconds of the call.
        error: The exception's type and message when the call failed.
    """

    detections: int
    frames: frozenset[int]
    latency_ms: int
    error: str | None = None


@dataclass(frozen=True)
class Row:
    """Hosted and self-hosted runs for one chunk and concept."""

    chunk: str
    concept: str
    n_frames: int
    hosted: Run
    self_hosted: Run

    @property
    def agreement(self) -> float | None:
        """Share of frames where both backends agree on presence; `None` if a call failed."""
        if self.hosted.error or self.self_hosted.error:
            return None
        return presence_agreement(self.hosted.frames, self.self_hosted.frames, self.n_frames)


def presence_agreement(hosted: frozenset[int], self_hosted: frozenset[int], n_frames: int) -> float:
    """Share of a chunk's frames on which both backends agree whether the concept is present.

    Args:
        hosted: Frames with a hosted detection.
        self_hosted: Frames with a self-hosted detection.
        n_frames: Frames in the chunk.

    Returns:
        A value in [0, 1]; 1 when both see the concept on exactly the same frames.
    """
    disagree = len(hosted ^ self_hosted)
    return 1.0 - disagree / max(n_frames, 1)


def run_segmenter(
    segmenter: Segmenter,
    chunk: Chunk,
    concept: str,
    timer: Callable[[], float] = time.perf_counter,
) -> Run:
    """Segment one concept in one chunk and summarise the answer; never raises.

    Args:
        segmenter: Hosted or self-hosted SAM.
        chunk: The chunk.
        concept: The text prompt.
        timer: Monotonic seconds.

    Returns:
        The summary, with `error` set when the call failed.
    """
    started = timer()
    try:
        detections: list[Detection] = segmenter.segment(chunk, concept)
    except Exception as exc:  # one failed call must not stop the comparison
        return Run(0, frozenset(), _ms(started, timer), f"{type(exc).__name__}: {exc}")
    frames = frozenset(d.frame_idx for d in detections)
    return Run(len(detections), frames, _ms(started, timer))


def compare(
    chunks: Sequence[Chunk],
    concepts: Sequence[str],
    hosted: Segmenter,
    self_hosted: Segmenter,
    timer: Callable[[], float] = time.perf_counter,
) -> list[Row]:
    """Run both backends on every chunk and concept.

    Args:
        chunks: The chunks.
        concepts: The concepts.
        hosted: Hosted SAM 3.1.
        self_hosted: Self-hosted SAM 3.1.
        timer: Monotonic seconds.

    Returns:
        One row per chunk and concept, in input order.
    """
    return [
        Row(
            chunk=Path(chunk.path).name,
            concept=concept,
            n_frames=chunk.n_frames,
            hosted=run_segmenter(hosted, chunk, concept, timer),
            self_hosted=run_segmenter(self_hosted, chunk, concept, timer),
        )
        for chunk in chunks
        for concept in concepts
    ]


def format_rows(rows: Sequence[Row]) -> str:
    """Render the comparison table, the failed calls and a per-concept summary.

    Args:
        rows: Output of `compare`.

    Returns:
        Plain text for the terminal.
    """
    lines = [_line([name for name, _ in COLUMNS])]
    for row in rows:
        agreement = row.agreement
        lines.append(
            _line(
                [
                    row.chunk,
                    row.concept,
                    str(row.n_frames),
                    _count(row.hosted, row.hosted.detections),
                    _count(row.self_hosted, row.self_hosted.detections),
                    _count(row.hosted, len(row.hosted.frames)),
                    _count(row.self_hosted, len(row.self_hosted.frames)),
                    str(row.hosted.latency_ms),
                    str(row.self_hosted.latency_ms),
                    "-" if agreement is None else f"{agreement:.2f}",
                ]
            )
        )
    errors = [
        f"  {row.chunk} {row.concept} {side}: {run.error}"
        for row in rows
        for side, run in (("hosted", row.hosted), ("self-hosted", row.self_hosted))
        if run.error
    ]
    if errors:
        lines += ["", "errors:", *errors]
    lines += ["", "summary (frames with detections / frames; mean agreement; median ms):"]
    for concept in dict.fromkeys(row.concept for row in rows):
        lines.append(_summary(concept, [row for row in rows if row.concept == concept]))
    return "\n".join(lines)


def format_memory(label: str, health: Mapping[str, Any] | None) -> str:
    """One line with the self-hosted server's GPU memory from `/health`.

    Args:
        label: "before" or "after".
        health: The `/health` JSON, or `None` when it could not be read.

    Returns:
        The line.
    """
    memory = (health or {}).get("memory") or {}
    if "allocated_bytes" not in memory:
        return f"self-hosted memory {label}: unavailable"
    return (
        f"self-hosted memory {label}: allocated {memory['allocated_bytes'] / GIB:.2f} GiB, "
        f"reserved {memory.get('reserved_bytes', 0) / GIB:.2f} GiB, "
        f"sessions {memory.get('sessions', '?')}, guard trips {memory.get('guard_trips', '?')}"
    )


def worst_agreement(rows: Sequence[Row]) -> float:
    """Lowest agreement over the rows; a failed call counts as 0."""
    values = [row.agreement for row in rows]
    return min((0.0 if value is None else value for value in values), default=1.0)


def probe_file(path: Path) -> Chunk:  # pragma: no cover - needs ffprobe and a real chunk file
    """Build a `Chunk` for a file with ffprobe.

    Args:
        path: An MP4 chunk.

    Returns:
        The chunk; the camera id is the parent folder's name.
    """
    from rulecam.capture.chunker import probe_chunk  # noqa: PLC0415

    info = probe_chunk(path)
    return Chunk(
        chunk_id=hashlib.sha256(str(path).encode()).hexdigest()[:16],
        camera_id=path.parent.name or "compare",
        path=str(path),
        start_ts=datetime.now(UTC),
        fps=info.fps,
        n_frames=info.n_frames,
        width=info.width,
        height=info.height,
    )


def live_backends() -> tuple[Segmenter, Segmenter, HealthFetch]:  # pragma: no cover - network
    """Build hosted and self-hosted SAM and a `/health` reader from `Settings`.

    Returns:
        `(hosted, self_hosted, fetch_health)`.
    """
    import httpx  # noqa: PLC0415

    from rulecam.clients.meta_hosted.sam import HostedSam31  # noqa: PLC0415
    from rulecam.clock import SystemClock  # noqa: PLC0415
    from rulecam.config import get_settings  # noqa: PLC0415
    from rulecam.perception.sam_runtime import HttpSamWorker, ModalSam31  # noqa: PLC0415

    settings = get_settings()
    hosted = HostedSam31.from_settings(settings, clock=SystemClock())
    self_hosted = ModalSam31(HttpSamWorker.from_settings(settings))
    base_url = (settings.sam_base_url or "").rstrip("/")

    def fetch_health() -> Mapping[str, Any] | None:
        try:
            reply = httpx.get(f"{base_url}/health", timeout=10.0)
            body: Mapping[str, Any] = reply.json()
        except (httpx.HTTPError, ValueError):
            return None
        return body

    return hosted, self_hosted, fetch_health


def main(
    argv: Sequence[str] | None = None,
    *,
    backends: Callable[[], tuple[Segmenter, Segmenter, HealthFetch]] = live_backends,
    probe: Probe = probe_file,
) -> int:
    """Compare hosted and self-hosted SAM on the given chunks and print the table.

    Args:
        argv: Command-line arguments (defaults to `sys.argv[1:]`).
        backends: Builds `(hosted, self_hosted, fetch_health)` (fakes in tests).
        probe: Builds a `Chunk` for a file.

    Returns:
        The exit code: 1 when `--min-agreement` is set and a row falls below it.
    """
    parser = argparse.ArgumentParser(description="Compare hosted and self-hosted SAM 3.1.")
    parser.add_argument("chunks", nargs="+", type=Path, help="MP4 chunk files")
    parser.add_argument("--concept", action="append", required=True, help="repeatable")
    parser.add_argument("--min-agreement", type=float, default=None)
    args = parser.parse_args(argv)
    hosted, self_hosted, fetch_health = backends()
    chunks = [probe(path) for path in args.chunks]
    print(format_memory("before", fetch_health()))
    rows = compare(chunks, args.concept, hosted, self_hosted)
    print(format_rows(rows))
    print(format_memory("after", fetch_health()))
    if args.min_agreement is not None and worst_agreement(rows) < args.min_agreement:
        return 1
    return 0


def _ms(started: float, timer: Callable[[], float]) -> int:
    return round((timer() - started) * 1000)


def _count(run: Run, value: int) -> str:
    return "ERR" if run.error else str(value)


def _line(cells: Sequence[str]) -> str:
    return " ".join(
        cell[:width].ljust(width) for cell, (_, width) in zip(cells, COLUMNS, strict=True)
    )


def _summary(concept: str, rows: Sequence[Row]) -> str:
    total = sum(row.n_frames for row in rows)
    hosted = sum(len(row.hosted.frames) for row in rows)
    self_hosted = sum(len(row.self_hosted.frames) for row in rows)
    agreements = [a for a in (row.agreement for row in rows) if a is not None]
    mean = f"{sum(agreements) / len(agreements):.2f}" if agreements else "-"
    return (
        f"  {concept}: hosted {hosted}/{total}, self-hosted {self_hosted}/{total}, "
        f"agreement {mean}, hosted {_median([r.hosted.latency_ms for r in rows])} ms, "
        f"self-hosted {_median([r.self_hosted.latency_ms for r in rows])} ms"
    )


def _median(values: Sequence[int]) -> int:
    ordered = sorted(values)
    return ordered[len(ordered) // 2] if ordered else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
