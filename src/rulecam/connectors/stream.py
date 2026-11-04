"""One live camera into rolling chunks, kept alive with reconnects (the connectors' engine).

`StreamWorker` runs ffmpeg for one camera in its own thread and does what the capture
`Chunker` does (same H.264 encoder options, same `<camera>_%06d.mp4` faststart segments, each
completed one probed and published on the bus as a `Chunk` on `chunk.ready`, so the runtime,
the chunk index and every other consumer see it exactly like a Pair-a-Phone or `rulecam capture`
chunk), plus what a camera that runs for weeks needs:

- **Reconnects with backoff.** When ffmpeg exits or stalls (no new frame for `stall_s`), it is
  restarted after 1, 2, 4, 8, 15, 30, 30... seconds (±20 % jitter); a session that stayed up for
  a minute resets the backoff. The input is resolved again on each start (a public stream's URL
  expires). A looped clip never ends; a clip played once ends the camera.
- **Health.** ffmpeg's `-progress` feed gives the frame counter once a second: measured fps
  over the last 10 s, the last frame's time, speed; its stderr gives the input codec, size and
  rate, and the last error lines (scrubbed of credentials). Restarts and errors are counted.
- **Numbering.** Segment numbers continue after the files already on disk, across reconnects and
  API restarts, so chunk ids never repeat.
- **Retention.** Only the newest `keep_chunks` files are kept (default one hour).
- **Size.** Frames wider than `max_width` are scaled down before encoding (a 4K camera costs
  the same to segment as a 720p one).
- **No orphans.** ffmpeg runs in its own process group and dies with Rulecam even on a crash;
  a previous run's ffmpeg for the same camera is killed before a new one starts
  (`connectors.process`).
"""

from __future__ import annotations

import contextlib
import random
import re
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import IO, Final, Literal

from pydantic import BaseModel

from rulecam.bus import Bus, Topic
from rulecam.capture import CaptureError, encoder_args, probe_chunk
from rulecam.connectors.errors import ConnectorError
from rulecam.connectors.process import clear_pidfile, reap_stale, spawn, write_pidfile
from rulecam.connectors.sources import ResolvedInput, scrub
from rulecam.contracts import Chunk, Clock, Provenance
from rulecam.log import get_logger

_log = get_logger(__name__)

BACKOFF_S: Final = (1.0, 2.0, 4.0, 8.0, 15.0, 30.0)
JITTER: Final = 0.2
STABLE_AFTER_S: Final = 60.0
FPS_WINDOW_S: Final = 10.0
POLL_S: Final = 0.2
STOP_GRACE_S: Final = 3.0
STDERR_LINES: Final = 40
_INDEX: Final = re.compile(r"_(\d{6,})$")
_INPUT_STREAM: Final = re.compile(
    r"Stream #0:\d+.*?: Video: (?P<codec>\w+).*?, (?P<w>\d{2,5})x(?P<h>\d{2,5})"
    r"(?:.*?, (?P<fps>[\d.]+) (?:fps|tbr))?"
)
_NOISE: Final = ("Last message repeated", "deprecated")
_ERROR_LEVELS: Final = ("[error]", "[fatal]", "[panic]")
_LEVEL: Final = re.compile(r"^(?:\[[^\]]*\]\s*)+")  # "[in#0 @ 0x..] [error] "

State = Literal["connecting", "live", "reconnecting", "stopped", "ended"]


class InputInfo(BaseModel):
    """What the camera sends, as ffmpeg reports it."""

    codec: str
    width: int
    height: int
    fps: float | None = None


class StreamHealth(BaseModel):
    """One camera's health, served with the camera (`GET /connectors/cameras`)."""

    state: State
    fps: float | None = None
    speed: float | None = None
    frames: int = 0
    chunks: int = 0
    connected_at: datetime | None = None
    first_chunk_s: float | None = None
    last_frame_at: datetime | None = None
    last_chunk_at: datetime | None = None
    last_chunk_id: str | None = None
    restarts: int = 0
    errors: int = 0
    last_error: str | None = None
    next_retry_at: datetime | None = None
    input: InputInfo | None = None
    title: str | None = None


@dataclass(frozen=True)
class StreamConfig:
    """How one camera is chunked.

    Attributes:
        camera_id: Stable camera id; chunk ids are `<camera_id>_<n>`.
        out_dir: Where its chunk files go.
        provenance: Recorded on every chunk.
        fps: Output frame rate.
        chunk_seconds: Chunk length.
        max_width: Wider frames are scaled down to this width.
        keep_chunks: How many chunk files to keep on disk.
        stall_s: Restart when no new frame came for this long.
        connect_timeout_s: Restart when the first frame has not come after this long.
        ends: The source ends by itself (a clip played once): a clean exit ends the camera.
    """

    camera_id: str
    out_dir: Path
    provenance: Provenance = Provenance.RECORDED
    fps: float = 10.0
    chunk_seconds: float = 3.0
    max_width: int = 1280
    keep_chunks: int = 1200
    stall_s: float = 20.0
    connect_timeout_s: float = 30.0
    ends: bool = False


Resolver = Callable[[], ResolvedInput]


def segment_index(path: Path) -> int | None:
    """The segment number in `<camera>_<n>.mp4`, or `None` for any other name."""
    match = _INDEX.search(path.stem)
    return int(match.group(1)) if match else None


def ffmpeg_command(config: StreamConfig, inputs: list[str], start_number: int) -> list[str]:
    """The full ffmpeg command for one session of a camera.

    Args:
        config: The camera's settings.
        inputs: Input options from the resolver, ending with `-i <input>`.
        start_number: The first segment number of this session.

    Returns:
        The argument list (never run through a shell).
    """
    return [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-nostats",
        "-loglevel",
        "level+info",
        "-progress",
        "pipe:1",
        "-stats_period",
        "1",
        *inputs,
        "-map",
        "0:v:0",
        "-vf",
        f"scale=w='min({config.max_width},iw)':h=-2",
        *encoder_args(fps=config.fps, chunk_seconds=config.chunk_seconds),
        "-f",
        "segment",
        "-segment_time",
        f"{config.chunk_seconds:g}",
        "-segment_start_number",
        str(start_number),
        "-reset_timestamps",
        "1",
        "-segment_format",
        "mp4",
        "-segment_format_options",
        "movflags=+faststart",
        str(config.out_dir / f"{config.camera_id}_%06d.mp4"),
    ]


def backoff_s(attempt: int, rng: random.Random) -> float:
    """Seconds to wait before reconnect `attempt` (0-based), with ±20 % jitter."""
    base = BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)]
    return base * (1.0 + rng.uniform(-JITTER, JITTER))


def error_lines(lines: Iterable[str], keep: int = 3) -> str:
    """The last `keep` error lines of ffmpeg's `level+info` log (else its last lines), joined.

    Args:
        lines: Log lines, each tagged with its level (`[in#0 @ 0x..] [error] ...`).
        keep: How many to keep.

    Returns:
        The lines without their level prefix, joined with ` | `; empty without any.
    """
    every = list(lines)
    errors = [line for line in every if any(level in line for level in _ERROR_LEVELS)]
    chosen = (errors or every)[-keep:]
    return " | ".join(_LEVEL.sub("", line) for line in chosen)


def parse_input(line: str) -> InputInfo | None:
    """The input video stream described on one ffmpeg stderr line, if it describes one."""
    match = _INPUT_STREAM.search(line)
    if match is None:
        return None
    fps = match.group("fps")
    return InputInfo(
        codec=match.group("codec"),
        width=int(match.group("w")),
        height=int(match.group("h")),
        fps=float(fps) if fps else None,
    )


class StreamWorker:
    """Keeps one camera's ffmpeg running and publishes its chunks (see the module docstring)."""

    def __init__(
        self,
        config: StreamConfig,
        resolve: Resolver,
        *,
        bus: Bus,
        clock: Clock,
        secrets: tuple[str, ...] = (),
        seed: int | None = None,
    ) -> None:
        """Configure the worker; nothing runs until `start()`.

        Args:
            config: The camera's settings.
            resolve: Gives ffmpeg's input options; called on every (re)connect. May raise
                `ConnectorError` (counted as an error, then retried with backoff).
            bus: Where chunks are published (`chunk.ready`).
            clock: Gives chunk start times and health timestamps.
            secrets: Credentials to scrub from anything ffmpeg prints.
            seed: Seed for the backoff jitter (tests).
        """
        self.config = config
        self._resolve = resolve
        self._bus = bus
        self._clock = clock
        self._secrets = secrets
        self._rng = random.Random(seed)  # noqa: S311 - jitter, not security
        self._lock = threading.Lock()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._health = StreamHealth(state="stopped")
        self._latest: Chunk | None = None
        self._published: set[Path] = set()
        self._frames_seen: deque[tuple[float, int]] = deque()
        self._session_frames = 0
        self._last_frame_mono = 0.0
        self._started_mono = 0.0
        self._stderr: deque[str] = deque(maxlen=STDERR_LINES)

    # ---- public -------------------------------------------------------------------------

    def start(self) -> None:
        """Start the worker thread (no-op when it is running)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stopping.clear()
        self._update(state="connecting", next_retry_at=None)
        self._thread = threading.Thread(
            target=self._supervise, name=f"stream-{self.config.camera_id}", daemon=True
        )
        self._thread.start()

    def stop(self, timeout_s: float = 5.0) -> None:
        """Stop ffmpeg and the thread; the last complete chunk is still published."""
        self._stopping.set()
        self._terminate()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout_s)
        self._update(state="stopped", next_retry_at=None)

    def health(self) -> StreamHealth:
        """A copy of the current health."""
        with self._lock:
            return self._health.model_copy()

    def latest_chunk(self) -> Chunk | None:
        """The newest published chunk, if any."""
        with self._lock:
            return self._latest

    @property
    def running(self) -> bool:
        """Whether the worker thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    # ---- supervision --------------------------------------------------------------------

    def _supervise(self) -> None:
        attempt = 0
        while not self._stopping.is_set():
            uptime = self._session()
            if self._stopping.is_set() or self.health().state == "ended":
                return
            if uptime >= STABLE_AFTER_S:
                attempt = 0
            delay = backoff_s(attempt, self._rng)
            attempt += 1
            with self._lock:
                restarts = self._health.restarts + 1
            self._update(
                state="reconnecting",
                restarts=restarts,
                next_retry_at=self._clock.now() + timedelta(seconds=delay),
            )
            _log.info("stream_retry", camera_id=self.config.camera_id, delay_s=round(delay, 1))
            if self._stopping.wait(delay):
                return
            self._update(state="connecting", next_retry_at=None)

    def _session(self) -> float:
        """Run ffmpeg once; returns how long it stayed up (seconds)."""
        try:
            resolved = self._resolve()
        except ConnectorError as exc:
            self._error(str(exc))
            return 0.0
        secrets = (*self._secrets, *resolved.secrets)
        self.config.out_dir.mkdir(parents=True, exist_ok=True)
        start_number = self._next_index()
        args = ffmpeg_command(self.config, resolved.args, start_number)
        self._stderr.clear()
        self._frames_seen.clear()
        self._session_frames = 0
        self._started_mono = time.monotonic()
        self._last_frame_mono = 0.0
        if resolved.title:
            self._update(title=resolved.title)
        reap_stale(self.config.out_dir)
        try:
            child = spawn(args)
        except OSError as exc:
            self._error(f"ffmpeg could not start: {exc}")
            return 0.0
        process = child.process
        write_pidfile(self.config.out_dir, process.pid)
        self._process = process
        readers = [
            threading.Thread(target=self._read_progress, args=(process.stdout,), daemon=True),
            threading.Thread(target=self._read_stderr, args=(process.stderr, secrets), daemon=True),
        ]
        for reader in readers:
            reader.start()
        _log.info("stream_started", camera_id=self.config.camera_id, start=start_number)
        stalled = self._watch(process)
        self._publish_ready(include_last=True)
        for reader in readers:
            reader.join(timeout=2.0)
        uptime = time.monotonic() - self._started_mono
        code = process.returncode
        self._process = None
        child.release()
        clear_pidfile(self.config.out_dir)
        if self._stopping.is_set():
            return uptime
        if code == 0 and self.config.ends and not stalled:
            self._update(state="ended", fps=None)
            _log.info("stream_ended", camera_id=self.config.camera_id)
            return uptime
        self._error(stalled or self._exit_reason(code))
        return uptime

    def _watch(self, process: subprocess.Popen[bytes]) -> str | None:
        """Publish segments while ffmpeg runs; kill it when it stalls. Returns why, if stalled."""
        while process.poll() is None:
            if self._stopping.wait(POLL_S):
                self._terminate()
                break
            self._publish_ready(include_last=False)
            reason = self._stall_reason()
            if reason is not None:
                self._terminate()
                return reason
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=STOP_GRACE_S)
        return None

    def _stall_reason(self) -> str | None:
        now = time.monotonic()
        if self._last_frame_mono == 0.0:
            if now - self._started_mono > self.config.connect_timeout_s:
                return f"no video after {self.config.connect_timeout_s:.0f} s"
            return None
        if now - self._last_frame_mono > self.config.stall_s:
            return f"stream stalled: no new frame for {self.config.stall_s:.0f} s"
        return None

    def _exit_reason(self, code: int | None) -> str:
        return f"ffmpeg exited ({code}): {error_lines(self._stderr)}"[:600]

    def _terminate(self) -> None:
        process = self._process
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=STOP_GRACE_S)
        except subprocess.TimeoutExpired:
            process.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=STOP_GRACE_S)

    def _error(self, message: str) -> None:
        with self._lock:
            errors = self._health.errors + 1
        self._update(errors=errors, last_error=message, fps=None)
        _log.warning("stream_error", camera_id=self.config.camera_id, errors=errors)

    # ---- ffmpeg output ------------------------------------------------------------------

    def _read_progress(self, stream: IO[bytes] | None) -> None:
        """Parse `-progress` blocks: `key=value` lines ending with `progress=...`."""
        if stream is None:
            return
        block: dict[str, str] = {}
        for raw in stream:
            key, _, value = raw.decode(errors="replace").strip().partition("=")
            block[key] = value
            if key == "progress":
                self._on_progress(block)
                block = {}

    def _on_progress(self, block: dict[str, str]) -> None:
        try:
            frame = int(block.get("frame", "0"))
        except ValueError:
            return
        now = time.monotonic()
        if frame <= self._session_frames:
            return
        added = frame - self._session_frames
        self._session_frames = frame
        if self._last_frame_mono == 0.0:
            self._update(state="live", connected_at=self._clock.now(), last_error=None)
        self._last_frame_mono = now
        self._frames_seen.append((now, frame))
        while self._frames_seen and now - self._frames_seen[0][0] > FPS_WINDOW_S:
            self._frames_seen.popleft()
        first_t, first_frame = self._frames_seen[0]
        fps = (frame - first_frame) / (now - first_t) if now - first_t >= 1.0 else None
        speed_text = block.get("speed", "").rstrip("x").strip()
        try:
            speed: float | None = float(speed_text)
        except ValueError:
            speed = None
        with self._lock:
            total = self._health.frames + added
            previous_fps = self._health.fps
        self._update(
            frames=total,
            last_frame_at=self._clock.now(),
            fps=round(fps, 2) if fps is not None else previous_fps,
            speed=speed,
        )

    def _read_stderr(self, stream: IO[bytes] | None, secrets: tuple[str, ...]) -> None:
        if stream is None:
            return
        found_input = False
        for raw in stream:
            line = scrub(raw.decode(errors="replace").rstrip(), secrets)
            if not line or any(n in line for n in _NOISE):
                continue
            found_input = found_input or "Output #" in line
            info = None if found_input else parse_input(line)
            if info is not None:
                found_input = True
                self._update(input=info)
            self._stderr.append(line[:300])

    # ---- chunks -------------------------------------------------------------------------

    def _segments(self) -> list[Path]:
        found = [
            (index, path)
            for path in self.config.out_dir.glob(f"{self.config.camera_id}_*.mp4")
            if (index := segment_index(path)) is not None
        ]
        return [path for _, path in sorted(found)]

    def _next_index(self) -> int:
        indexes = [segment_index(p) for p in self._segments()]
        known = [i for i in indexes if i is not None]
        self._published.update(self._segments())
        return max(known) + 1 if known else 0

    def _publish_ready(self, *, include_last: bool) -> None:
        segments = self._segments()
        ready = segments if include_last else segments[:-1]
        for path in ready:
            if path not in self._published:
                self._published.add(path)
                self._publish(path)
        self._prune(segments)

    def _publish(self, path: Path) -> None:
        try:
            info = probe_chunk(path)
        except CaptureError:
            _log.warning("chunk_unreadable", camera_id=self.config.camera_id, chunk=path.name)
            path.unlink(missing_ok=True)
            return
        if info.n_frames <= 0 or info.fps <= 0:
            path.unlink(missing_ok=True)
            return
        now = self._clock.now()
        duration = timedelta(seconds=info.n_frames / info.fps)
        previous = self.latest_chunk()
        start = now - duration
        if previous is not None:  # a burst (stream start) must not overlap the last chunk
            start = max(
                start, previous.start_ts + timedelta(seconds=previous.n_frames / previous.fps)
            )
        chunk = Chunk(
            chunk_id=path.stem,
            camera_id=self.config.camera_id,
            path=str(path),
            start_ts=start,
            fps=info.fps,
            n_frames=info.n_frames,
            width=info.width,
            height=info.height,
            provenance=self.config.provenance,
        )
        with self._lock:
            self._latest = chunk
            chunks = self._health.chunks + 1
            first = self._health.first_chunk_s
            if first is None:
                first = round(time.monotonic() - self._started_mono, 2)
        self._update(
            chunks=chunks, last_chunk_at=now, last_chunk_id=chunk.chunk_id, first_chunk_s=first
        )
        self._bus.publish(Topic.CHUNK_READY, chunk)
        _log.info("chunk_ready", camera_id=self.config.camera_id, chunk_id=chunk.chunk_id)

    def _prune(self, segments: list[Path]) -> None:
        excess = len(segments) - self.config.keep_chunks
        for path in segments[: max(excess, 0)]:
            path.unlink(missing_ok=True)
            self._published.discard(path)

    def _update(self, **changes: object) -> None:
        with self._lock:
            self._health = self._health.model_copy(update=changes)
