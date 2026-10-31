# `rulecam.clients`

Every Meta model call goes through this package (task book §5.1); feature code never makes raw
HTTP calls.

## Shared pieces

| Module | What it gives you |
|---|---|
| `transport.py` | `Transport` (a request mapping in, a reply mapping out — the seam the P3-02 replay cache wraps), `HttpxTransport` (real HTTPS, bearer auth), `Sender` (token bucket + jittered backoff on 429/5xx, at most 4 tries, honours `Retry-After`, drains on `x-ratelimit-remaining-requests: 0`) |
| `ratelimit.py` | Thread-safe `TokenBucket` |
| `relay.py` | `ModelUsed`, the `relay.model_used` payload (`model`, `segment`, `latency_ms`, `camera_id`, `attempts`) |
| `errors.py` | `ModelHTTPError`, `ModelResponseError`, `StructuredOutputError`, `ClientConfigError` |

## `meta_hosted/spark.py` — `SparkClient` (P2-01, owner P2)

Implements `rulecam.contracts.StructuredLLM` on Meta's hosted Responses API.

```python
spark = SparkClient(transport, clock=SystemClock(), bus=bus)
response = spark.structured(
    model="muse-spark-1.3",
    messages=[{"role": "user", "content": "Extract safety hazards"}],
    schema=HazardsOutput,
    reasoning_effort="low",
    background=False,
    enable_web_search=True,
    max_tool_calls=5,
)
```

- Structured schema output via Pydantic model (`schema.model_json_schema()`).
- Background job polling (`background=True`).
- Validation error recovery: retries once with schema error feedback, then raises `StructuredOutputError`.
- Tracks prompt caching via `cached_tokens`.

## `meta_hosted/voice.py` — `VoiceTranscribeClient` (P2-01, owner P2)

Implements `rulecam.contracts.Transcriber` for speech transcription and diarization.

```python
voice = VoiceTranscribeClient(transport, clock=SystemClock(), bus=bus)
segments = voice.transcribe(
    audio_bytes, lang_bias=["en", "es"], keywords=["sanitizer"], diarize=True
)
# Streaming session with start-only parameters:
session = voice.start_streaming_session("DIARIZATION", keywords=["safety"], language_bias=["en"])
```

- Splits audio files exceeding 10 minutes or 32 MB into WAV chunks automatically.
- Enforces session-start-only configuration for `keywords` and `languageBias`.

## `meta_hosted/image.py` — `MuseImageClient` (P2-01, owner P2)

Generates synthetic scenes, posters, and red-team visual lookalikes.

```python
image_client = MuseImageClient(
    transport, clock=SystemClock(), bus=bus, output_dir=Path("data/images")
)
res = image_client.generate("Kitchen workstation with sanitizer bottle", size="1024x1024")
# res.path -> Path to saved PNG
```

- Rate-limited to 150 RPM.
- Accepts at most 10 reference images; rejects mask/inpainting parameters.
- Chaining support via `previous_response_id`.

## `meta_hosted/sam.py` — `HostedSam31` (P1-02, owner P1)

Implements `rulecam.contracts.Segmenter`.

```python
sam = HostedSam31(transport, clock=SystemClock(), bus=bus, mask_decoder=None)
sam.segment(chunk, "hard hat")  # concept prompt
sam.segment(chunk, "person", box_prompts={1: (x1, y1, x2, y2)})  # re-prompt; >16 are split
await sam.segment_concepts(chunk, ["person", "forklift"])  # one request per concept
sam.segment_image(Path("frame.jpg"), "sink", camera_id="cam1")  # single-frame probe
sam.costs.snapshot()  # per-camera cost panel
```

- One concept per request. Concept text and box/point prompts are never combined:
  `negative_points` without `box_prompts` raises `ValueError` before any call.
- Box prompts go on frame 0 (`forward`) or the last frame (`backward`).
- Zero matches returns `[]`; detections are sorted by `(frame_idx, object_id)`.
- Cost: `max(50, usage.video_frames_processed)` frames at $0.20/1,000; images $2.50/1,000.
- `HostedSam31.from_settings` wraps the transport in the replay cache (P3-02) in every mode:
  `replay` needs no API key and a miss raises `ReplayMiss`; `record` stores only 2xx replies;
  replay hits report the recorded `latency_ms` on `relay.model_used`.
- Verified against the live API on Oct 30 2026 (issue #5). The real replies are in
  `tests/fixtures/replay/sam/live/`.
- Masks: payloads are decoded by `meta_hosted/sam_masks.py`, the default `MaskDecoder`. It wraps
  Meta's `meta-sam-parser` (SAM License; see WHAT-WE-BUILT.md). `Mask.rle` is full-frame COCO
  compressed RLE as JSON (`rulecam.perception.masks`). Passing `mask_decoder=None` skips
  decoding, which gives `Mask.rle == ""` (`NO_MASK`) so consumers fall back to the box.
- Every wire-format detail (and every unverified assumption, marked `ASSUMPTION`) lives in
  `meta_hosted/sam_wire.py`.

## Tests

```bash
uv run pytest tests/clients                 # recorded-style fixtures, no network
RULECAM_MODE=live uv run pytest -m live tests/clients   # real API, by hand only
```
