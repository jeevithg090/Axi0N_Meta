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

## Wire format of the P2 clients (P2-01, owner P2)

Spark and Muse Image use the same Responses + Files shape as hosted SAM 3.1, which was verified
live (issue #5); the shared pieces are in `meta_hosted/responses.py`. Base URL
`https://api.meta.ai/v1` (`META_API_BASE_URL`).

| Client | Model id | Request | Where the answer is |
|---|---|---|---|
| `SparkClient` | `muse-spark-1.3` (default), `muse-spark-1.2` (video with narration) | `POST /responses`, `stream: false` (or `background: true`, then `GET /responses/{id}` until not `queued`/`in_progress`); `text.format` = `json_schema`; `reasoning.effort`; `tools` (+ `{"type": "web_search"}`), `max_tool_calls`; never `tool_choice` | `output[]` `message` items, `content[]` parts of type `output_text`; `usage.input_tokens_details.cached_tokens` |
| `MuseImageClient` | `muse-image-1.0` | `POST /responses`, `stream: false`, `tools: [{"type": "image_generation", "size", "output_format": "png"}]`, references as `input_image` parts with `file_id`, `previous_response_id` | base64 `result` of the `image_generation_call` output item |
| `VoiceTranscribeClient` | `muse-voice-transcribe-1.0` | `POST /asr/transcribe`, multipart: `request` (JSON: `model`, `audioEncoding: "WAV"`, `mode`, `languageBias` as language names, `keywords`) and `audio` (mono 16-bit PCM WAV, 16/24 kHz, ≤ 10 min, ≤ 32 MB) | `{transcript, turns: [{transcript, speaker?}]}` |

Media for Spark and Muse Image is uploaded once with `POST /files` (`purpose: user_data`) and
referenced by `file_id` (`SparkClient.attach(path)` returns the typed content part:
`input_image`, `input_video`, `input_audio` or `input_file` for PDFs). The upload bytes are keyed
by SHA-256 in the replay cache, so REPLAY serves recorded file ids and then recorded answers.

Field names come from the official guides (speech-to-text, image-generation, file-handling,
structured-output, search-grounding and the Responses/voice API references, read Oct 31 2026).
Only SAM has been exercised against the live API so far; the Spark, Voice and Image shapes are
confirmed by the `live`-marked checks in `tests/clients/meta_hosted/test_p2_clients_live.py`
(`make live-test`), which have not been run yet.

```python
spark = SparkClient.from_settings(settings, clock=clock, bus=bus)  # always via the replay cache
pdf = spark.attach(Path("food_code.pdf"))
out = spark.structured(
    messages=[
        {"role": "system", "content": SYSTEM},  # keep stable: prompt caching
        {"role": "user", "content": [{"type": "input_text", "text": "Extract"}, pdf]},
    ],
    schema=Extracted,
)

voice = VoiceTranscribeClient.from_settings(settings, clock=clock, bus=bus)
voice.transcribe(wav, lang_bias=["en", "es"], keywords=["sanitizer"], diarize=True)
session = voice.start_streaming_session("DIARIZATION", api_key=key, keywords=["LOTO"])
session.handshake()  # first frame on wss://…/asr/realtime; keywords can't change afterwards

image = MuseImageClient.from_settings(settings, clock=clock, bus=bus)
image.generate("same scene, nobody at the sink", reference_images=[frame], size="1280x720")
```

- Spark: one validation retry with the errors appended, then `StructuredOutputError`. `strict`
  is off because pydantic schemas (defaults, open dicts, root unions) fall outside the strict
  subset; the reply is validated here instead.
- Voice: WAVs over 10 min / 32 MB are split on frame boundaries; split files get chunk-prefixed
  speaker labels. The API reports no language, so `lang` is the bias language when exactly one
  was given, else `und`.
- Image: 150 RPM bucket; more than 10 references, or any mask/inpainting argument, raises
  `ValueError` before any call. Images are saved under `data/images/`.

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
