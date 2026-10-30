"""Validate, transcode and sample phone uploads with ffprobe/ffmpeg (task P1-09).

Uploads come from outside, so they are untrusted (task book §5.8):
- the demuxer is forced from the declared type and only the `file` protocol is allowed, so a
  disguised playlist cannot make ffmpeg open other files or URLs;
- codec, frame size and duration are checked before anything is transcoded;
- every tool runs as an argument list with `-nostdin` and a timeout, never through a shell.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from rulecam.capture.chunker import encoder_args
from rulecam.capture.pair_phone.errors import TranscodeError, UnsupportedMediaError

UploadKind = Literal["webm", "mp4"]

CONTENT_TYPES: dict[str, UploadKind] = {"video/webm": "webm", "video/mp4": "mp4"}
ALLOWED_CODECS: dict[UploadKind, frozenset[str]] = {
    "webm": frozenset({"vp8", "vp9", "av1"}),
    "mp4": frozenset({"h264", "hevc", "av1"}),
}
MAX_SIDE_PX = 4096
PROBE_TIMEOUT_S = 10.0
TRANSCODE_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class UploadInfo:
    """What ffprobe reports about an uploaded clip."""

    codec: str
    width: int
    height: int
    duration_s: float | None


def media_kind(content_type: str | None) -> UploadKind:
    """Map a declared content type such as `video/webm;codecs=vp8` to an upload kind.

    Raises:
        UnsupportedMediaError: The type is missing or not `video/webm` / `video/mp4`.
    """
    essence = (content_type or "").split(";", 1)[0].strip().lower()
    kind = CONTENT_TYPES.get(essence)
    if kind is None:
        msg = f"expected video/webm or video/mp4, got {essence or 'no content type'}"
        raise UnsupportedMediaError(msg)
    return kind


def _hardened_input(path: Path, kind: UploadKind) -> list[str]:
    return ["-protocol_whitelist", "file", "-f", kind, "-i", str(path)]


def probe_args(path: Path, kind: UploadKind) -> list[str]:
    """Build the ffprobe command that inspects an upload with the demuxer forced to `kind`."""
    return [
        "ffprobe",
        "-v",
        "error",
        *_hardened_input(path, kind),
        "-select_streams",
        "v:0",
        "-show_entries",
        "format=duration:stream=codec_name,width,height",
        "-of",
        "json",
    ]


def parse_probe(stdout: str, kind: UploadKind) -> UploadInfo:
    """Check ffprobe's JSON for an allowed video stream.

    Args:
        stdout: ffprobe's JSON output.
        kind: The declared upload kind.

    Returns:
        Codec, size and (when the container records it) duration.

    Raises:
        UnsupportedMediaError: No video stream, a disallowed codec, or an absurd frame size.
    """
    report = json.loads(stdout or "{}")
    streams = report.get("streams") or []
    if not streams:
        msg = f"no video stream found in the {kind} upload"
        raise UnsupportedMediaError(msg)
    stream = streams[0]
    codec = str(stream.get("codec_name", ""))
    if codec not in ALLOWED_CODECS[kind]:
        msg = f"codec {codec or 'unknown'} is not allowed in {kind}"
        raise UnsupportedMediaError(msg)
    width, height = int(stream.get("width", 0)), int(stream.get("height", 0))
    if not (0 < width <= MAX_SIDE_PX and 0 < height <= MAX_SIDE_PX):
        msg = f"frame size {width}x{height} is outside 1..{MAX_SIDE_PX} px"
        raise UnsupportedMediaError(msg)
    raw_duration = report.get("format", {}).get("duration")
    duration = None if raw_duration in (None, "N/A") else float(raw_duration)
    return UploadInfo(codec=codec, width=width, height=height, duration_s=duration)


def video_filter(fps: float) -> str:
    """Return the filter chain: even frame size (H.264 4:2:0 needs it), then exactly `fps`.

    The `fps` filter resamples variable-rate phone video without the extra trailing frames
    that the output `-r` option alone can add.
    """
    return f"scale=trunc(iw/2)*2:trunc(ih/2)*2,fps={fps:g}"


def transcode_args(
    src: Path,
    dst: Path,
    *,
    kind: UploadKind,
    fps: float,
    chunk_seconds: float,
    max_seconds: float,
) -> list[str]:
    """Build the ffmpeg command turning an upload into one H.264 faststart chunk.

    The encoder options are the P1-01 chunker's (`encoder_args`). `-t` caps the duration even
    when the container does not record one, and `-n` never overwrites an existing chunk.

    Args:
        src: The raw upload.
        dst: The chunk to write (`.mp4`).
        kind: The declared upload kind (forces the demuxer).
        fps: Output frame rate.
        chunk_seconds: Nominal chunk length (sets the GOP).
        max_seconds: Hard cap on the output duration.

    Returns:
        The argument list for `asyncio.create_subprocess_exec`.
    """
    return [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        *_hardened_input(src, kind),
        "-map",
        "0:v:0",
        "-t",
        f"{max_seconds:g}",
        "-vf",
        video_filter(fps),
        *encoder_args(fps=fps, chunk_seconds=chunk_seconds),
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        "-n",
        str(dst),
    ]


def last_frame_args(chunk: Path, frame: Path) -> list[str]:
    """Build the ffmpeg command that writes the last frame of a chunk as a JPEG."""
    return [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-sseof",
        "-0.5",
        "-i",
        str(chunk),
        "-frames:v",
        "1",
        "-q:v",
        "3",
        "-y",
        str(frame),
    ]


async def run_tool(args: list[str], *, timeout_s: float) -> str:
    """Run ffmpeg or ffprobe without a shell and return its stdout.

    Args:
        args: The full argument list (program first).
        timeout_s: Kill the process if it runs longer than this.

    Returns:
        The process's standard output.

    Raises:
        TranscodeError: The process failed, timed out or could not start.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        msg = f"{args[0]} could not start: {exc}"
        raise TranscodeError(msg) from exc
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        msg = f"{args[0]} timed out after {timeout_s:g} s"
        raise TranscodeError(msg) from exc
    if process.returncode != 0:
        detail = stderr.decode(errors="replace")[-300:]
        msg = f"{args[0]} exited with {process.returncode}: {detail}"
        raise TranscodeError(msg)
    return stdout.decode(errors="replace")


async def probe_upload(path: Path, kind: UploadKind) -> UploadInfo:
    """Inspect an upload with ffprobe and check it is an allowed video.

    Raises:
        UnsupportedMediaError: ffprobe cannot read it as `kind`, or it fails `parse_probe`.
    """
    try:
        stdout = await run_tool(probe_args(path, kind), timeout_s=PROBE_TIMEOUT_S)
    except TranscodeError as exc:
        msg = f"not a readable {kind} video"
        raise UnsupportedMediaError(msg) from exc
    return parse_probe(stdout, kind)
