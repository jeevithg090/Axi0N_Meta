"""Capture a camera (webcam, RTSP or file) into rolling H.264 faststart chunks (task P1-01).

Each completed chunk is probed with ffprobe and published on the bus as a `Chunk`
(topic `chunk.ready`). ffmpeg always runs as an argument list (never through a shell), so a
hostile source string cannot inject commands.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path

from rulecam.bus import Bus, Topic
from rulecam.capture.errors import CaptureError
from rulecam.contracts import Chunk, Clock, Provenance
from rulecam.log import get_logger

_log = get_logger(__name__)

STOP_TIMEOUT_S = 2.0


@dataclass(frozen=True)
class ChunkInfo:
    """What ffprobe reports about one chunk."""

    n_frames: int
    width: int
    height: int
    fps: float


def input_args(
    source: str | int, *, realtime: bool = False, platform: str = sys.platform
) -> list[str]:
    """Return the ffmpeg input arguments for a source.

    Args:
        source: A webcam index (int), an `rtsp://` URL, or a file path.
        realtime: Read a file at its native frame rate (for replaying recordings live).
        platform: `sys.platform`, injectable for tests.

    Returns:
        Arguments to place before the output options.
    """
    if isinstance(source, int):
        if platform == "darwin":
            return ["-f", "avfoundation", "-framerate", "30", "-i", f"{source}:none"]
        return ["-f", "v4l2", "-i", f"/dev/video{source}"]
    if source.startswith("rtsp://"):
        return ["-rtsp_transport", "tcp", "-i", source]
    return [*(["-re"] if realtime else []), "-i", source]


def encoder_args(*, fps: float, chunk_seconds: float) -> list[str]:
    """Return the video-only H.264 encoder options shared by every capture path.

    Keyframes are forced every `chunk_seconds`, so a chunk starts on a keyframe.

    Args:
        fps: Output frame rate.
        chunk_seconds: Chunk length in seconds (sets the GOP).

    Returns:
        Arguments to place between the input and the output options.
    """
    gop = str(round(fps * chunk_seconds))
    return [
        "-an",
        "-r",
        f"{fps:g}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-tune",
        "zerolatency",
        "-pix_fmt",
        "yuv420p",
        "-g",
        gop,
        "-keyint_min",
        gop,
        "-sc_threshold",
        "0",
        "-force_key_frames",
        f"expr:gte(t,n_forced*{chunk_seconds:g})",
    ]


def ffmpeg_args(
    source: str | int,
    pattern: Path,
    *,
    fps: float,
    chunk_seconds: float,
    realtime: bool = False,
) -> list[str]:
    """Build the full ffmpeg command for segmenting a source into faststart H.264 chunks.

    Keyframes are forced every `chunk_seconds` so segments cut exactly on chunk boundaries.

    Args:
        source: See `input_args`.
        pattern: Output pattern, e.g. `out/cam1_%06d.mp4`.
        fps: Output frame rate.
        chunk_seconds: Segment length in seconds.
        realtime: See `input_args`.

    Returns:
        The argument list for `asyncio.create_subprocess_exec`.
    """
    return [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        *input_args(source, realtime=realtime),
        *encoder_args(fps=fps, chunk_seconds=chunk_seconds),
        "-f",
        "segment",
        "-segment_time",
        f"{chunk_seconds:g}",
        "-reset_timestamps",
        "1",
        "-segment_format",
        "mp4",
        "-segment_format_options",
        "movflags=+faststart",
        str(pattern),
    ]


def probe_chunk(path: Path) -> ChunkInfo:
    """Probe a chunk with ffprobe.

    Args:
        path: The chunk file.

    Returns:
        Frame count, size and frame rate of the first video stream.

    Raises:
        CaptureError: If ffprobe fails or the file has no video stream.
    """
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-count_frames",
        "-show_entries",
        "stream=nb_read_frames,width,height,avg_frame_rate",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603
    streams = json.loads(result.stdout or "{}").get("streams", [])
    if result.returncode != 0 or not streams:
        msg = f"ffprobe failed for {path}: {result.stderr.strip()}"
        raise CaptureError(msg)
    stream = streams[0]
    rate = stream.get("avg_frame_rate", "0/0")
    if rate in ("0/0", "0/1"):
        msg = f"{path} has no usable frame rate"
        raise CaptureError(msg)
    return ChunkInfo(
        n_frames=int(stream["nb_read_frames"]),
        width=int(stream["width"]),
        height=int(stream["height"]),
        fps=float(Fraction(rate)),
    )


class Chunker:
    """Runs ffmpeg for one camera and publishes each completed chunk on the bus.

    A segment is complete when the next segment file appears, or when ffmpeg exits.
    """

    def __init__(
        self,
        camera_id: str,
        source: str | int,
        out_dir: Path,
        *,
        bus: Bus,
        clock: Clock,
        fps: float = 10.0,
        chunk_seconds: float = 3.0,
        realtime: bool = False,
        poll_s: float = 0.2,
        provenance: Provenance = Provenance.RECORDED,
    ) -> None:
        """Configure a chunker; nothing starts until `run()`.

        Args:
            camera_id: Stable camera identifier, used in chunk ids and file names.
            source: Webcam index, RTSP URL or file path.
            out_dir: Directory for chunk files (created if missing).
            bus: Bus to publish `chunk.ready` on.
            clock: Clock giving the capture start time.
            fps: Output frame rate.
            chunk_seconds: Chunk length in seconds.
            realtime: For file sources, read at native speed instead of as fast as possible.
            poll_s: How often to look for newly completed segments.
            provenance: Provenance recorded on every chunk.
        """
        self.camera_id = camera_id
        self._source = source
        self._out_dir = out_dir
        self._bus = bus
        self._clock = clock
        self._fps = fps
        self._chunk_seconds = chunk_seconds
        self._realtime = realtime
        self._poll_s = poll_s
        self._provenance = provenance
        self._process: asyncio.subprocess.Process | None = None
        self._published: set[Path] = set()
        self._next_start: datetime = clock.now()

    @property
    def pattern(self) -> Path:
        """The ffmpeg output pattern for this camera's chunks."""
        return self._out_dir / f"{self.camera_id}_%06d.mp4"

    async def run(self) -> None:
        """Start ffmpeg and publish chunks until it exits or `stop()` is called.

        Raises:
            CaptureError: If ffmpeg exits with an error before producing any chunk.
        """
        self._out_dir.mkdir(parents=True, exist_ok=True)
        self._next_start = self._clock.now()
        args = ffmpeg_args(
            self._source,
            self.pattern,
            fps=self._fps,
            chunk_seconds=self._chunk_seconds,
            realtime=self._realtime,
        )
        self._process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
        )
        _log.info("capture_started", camera_id=self.camera_id, pid=self._process.pid)
        while self._process.returncode is None:
            self._publish_completed(include_last=False)
            try:
                await asyncio.wait_for(self._process.wait(), timeout=self._poll_s)
            except TimeoutError:
                continue
        self._publish_completed(include_last=True)
        await self._check_exit()

    async def stop(self) -> None:
        """Terminate ffmpeg (SIGTERM, then SIGKILL after 2 s); `run()` flushes the last chunk."""
        process = self._process
        if process is None or process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=STOP_TIMEOUT_S)
        except TimeoutError:
            process.kill()
            await process.wait()
        _log.info("capture_stopped", camera_id=self.camera_id)

    async def _check_exit(self) -> None:
        process = self._process
        if process is None or process.returncode in (0, -15, 255) or self._published:
            return
        stderr = b"" if process.stderr is None else await process.stderr.read()
        msg = f"ffmpeg exited with {process.returncode}: {stderr.decode(errors='replace')[-500:]}"
        raise CaptureError(msg)

    def _publish_completed(self, *, include_last: bool) -> None:
        segments = sorted(self._out_dir.glob(f"{self.camera_id}_*.mp4"))
        ready = segments if include_last else segments[:-1]
        for path in ready:
            if path not in self._published:
                self._publish(path)

    def _publish(self, path: Path) -> None:
        self._published.add(path)
        try:
            info = probe_chunk(path)
        except CaptureError:
            _log.warning("chunk_unreadable", camera_id=self.camera_id, path=str(path))
            return
        chunk = Chunk(
            chunk_id=path.stem,
            camera_id=self.camera_id,
            path=str(path),
            start_ts=self._next_start,
            fps=info.fps,
            n_frames=info.n_frames,
            width=info.width,
            height=info.height,
            provenance=self._provenance,
        )
        self._next_start += timedelta(seconds=info.n_frames / info.fps)
        self._bus.publish(Topic.CHUNK_READY, chunk)
        _log.info("chunk_ready", camera_id=self.camera_id, chunk_id=chunk.chunk_id)
