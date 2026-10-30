# `rulecam.capture`

Turns a camera into rolling **H.264, faststart** chunks (default 3 s at 10 fps) and publishes
each completed chunk on the bus as a `Chunk` (`chunk.ready`). Owner: **P1** (task P1-01).

## Public API

| Name | What it does |
|---|---|
| `Chunker(camera_id, source, out_dir, *, bus, clock, fps=10, chunk_seconds=3, realtime=False)` | `await run()` starts ffmpeg and publishes chunks until ffmpeg exits; `await stop()` terminates it (SIGTERM, SIGKILL after 2 s) and the last chunk is flushed |
| `ffmpeg_args(...)` / `input_args(...)` | Build the ffmpeg argument list (webcam via avfoundation/v4l2, `rtsp://` over TCP, or a file; `realtime` adds `-re`) |
| `encoder_args(fps=, chunk_seconds=)` | The shared H.264 encoder options (also used by Pair-a-Phone) |
| `probe_chunk(path) -> ChunkInfo` | Frame count, size and fps via ffprobe; raises `CaptureError` |

Keyframes are forced on chunk boundaries (`-g`, `-force_key_frames`), so segments cut exactly
every `chunk_seconds`. ffmpeg always runs as an argument list — never through a shell.

## CLI

```bash
uv run rulecam capture cam1 0                            # first webcam
uv run rulecam capture cam2 rtsp://camera.local/stream
uv run rulecam capture cam4 data/recordings/uc4.mp4 --realtime
```

## Pair-a-Phone (`rulecam.capture.pair_phone`, task P1-09)

Any phone becomes a camera: the owner UI shows a QR code, the phone opens an HTTPS page, records
3 s clips, and the server transcodes each one into a normal `Chunk` (`chunk.ready`). A placement
coach streams per-concept SAM confidence bars back to the phone every ~2 s.

```python
from rulecam.capture.pair_phone import PairPhone, build_router

pair = PairPhone(
    bus=bus,
    clock=SystemClock(),
    segmenter=hosted_sam,
    settings=get_settings(),
    concepts=lambda: current_plan_concepts,
)
app.include_router(build_router(pair))  # P3-08 mounts it; routes live under /pair
```

| Route | Who | What |
|---|---|---|
| `POST /pair/token` | owner UI | `{token, url, qr_png_base64, expires_at}`: single-use, 10 min, URL from `PUBLIC_TUNNEL_URL` |
| `GET /pair/{token}` | phone | the camera page (`static/`: status bar, camera name, privacy notice) |
| `POST /pair/{token}/claim` | phone | claims the token once → `{camera_id: "phone-<n>", label, session}` |
| `POST /pair/{token}/chunk` | phone | multipart `file`, `video/webm` or `video/mp4`, ≤ 5 MB, header `X-Pair-Session` |
| `WS /pair/{token}/coach` | phone | first message `{"session": …}`; then `hello`, `bars`, `status`, `hint` messages |

- **Single-use tokens.** A token expires 10 minutes after it is issued unless a phone claims
  it. The claim returns a session key; every upload and the coach must present it, and a second
  claim gets 409. A paired phone keeps working after the 10 minutes.
- **Security** (task book §5.8). HTTPS only (plain HTTP is accepted only when addressed to
  localhost); token creation only from this machine (`require_owner`, which the API app may
  replace via `app.dependency_overrides`); 1 request/s per token (429 + `Retry-After`);
  415 for other content types, codecs or unreadable files; 413 over 5 MB (checked while
  streaming, before the body is buffered) or over 6 s; uploads are probed with the demuxer
  forced and `-protocol_whitelist file`; ffmpeg always runs as an argument list with
  `-nostdin` and a timeout; raw uploads are deleted after transcoding.
- **Transcode.** `encoder_args` from the chunker at 10 fps, faststart, even frame size; chunk
  ids are `phone-<n>_<6 digits>`, and `<n>` continues after any phone chunks already on disk.
- **Coach.** `FrameSegmenter` is any object with `segment_image(frame, concept)`, which the
  hosted SAM 3.1 client (P1-02) provides. Each new chunk's last frame is segmented once per
  concept; `PairPhone.push_hint(camera_id, text)` sends the planner's one-line hint.
- **Phone page.** Records video only (`audio: false`) from the rear camera. Each 3 s clip is
  its own MediaRecorder run, so every upload is a standalone file (timeslice fragments after
  the first lack the container header).
- **Tunnel.** Run the API with uvicorn's proxy headers (the default) behind the tunnel, so
  tunnelled requests are seen as `https`, then set `PUBLIC_TUNNEL_URL`.

## Tests

```bash
uv run pytest tests/capture
```

Needs `ffmpeg` and `ffprobe` on PATH (installed in CI).
