# `rulecam.capture`

Turns a camera into rolling **H.264, faststart** chunks (default 3 s at 10 fps) and publishes
each completed chunk on the bus as a `Chunk` (`chunk.ready`). Owner: **P1** (task P1-01).

## Public API

| Name | What it does |
|---|---|
| `Chunker(camera_id, source, out_dir, *, bus, clock, fps=10, chunk_seconds=3, realtime=False)` | `await run()` starts ffmpeg and publishes chunks until ffmpeg exits; `await stop()` terminates it (SIGTERM, SIGKILL after 2 s) and the last chunk is flushed |
| `ffmpeg_args(...)` / `input_args(...)` | Build the ffmpeg argument list (webcam via avfoundation/v4l2, `rtsp://` over TCP, or a file; `realtime` adds `-re`) |
| `probe_chunk(path) -> ChunkInfo` | Frame count, size and fps via ffprobe; raises `CaptureError` |

Keyframes are forced on chunk boundaries (`-g`, `-force_key_frames`), so segments cut exactly
every `chunk_seconds`. ffmpeg always runs as an argument list — never through a shell.

## CLI

```bash
uv run rulecam capture cam1 0                            # first webcam
uv run rulecam capture cam2 rtsp://camera.local/stream
uv run rulecam capture cam4 data/recordings/uc4.mp4 --realtime
```

## Tests

```bash
uv run pytest tests/capture
```

Needs `ffmpeg` and `ffprobe` on PATH (installed in CI).
