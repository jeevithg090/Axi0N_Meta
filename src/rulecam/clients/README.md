# `rulecam.clients`

Every Meta model call goes through this package (task book §5.1); feature code never makes raw
HTTP calls.

## Shared pieces

| Module | What it gives you |
|---|---|
| `transport.py` | `Transport` (a request mapping in, a reply mapping out — the seam the P3-02 replay cache wraps), `HttpxTransport` (real HTTPS, bearer auth), `Sender` (token bucket + jittered backoff on 429/5xx, at most 4 tries, honours `Retry-After`, drains on `x-ratelimit-remaining-requests: 0`) |
| `ratelimit.py` | Thread-safe `TokenBucket` |
| `relay.py` | `ModelUsed`, the `relay.model_used` payload (`model`, `segment`, `latency_ms`, `camera_id`, `attempts`) |
| `errors.py` | `ModelHTTPError`, `ModelResponseError`, `ClientConfigError` |

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
- `HostedSam31.from_settings` refuses `RULECAM_MODE=replay` (replay must come from the cache).
- Masks: the payload format is only implemented in Meta's `meta-sam-parser`; inject a
  `MaskDecoder`. Without one, `Mask.rle` is `""` (`NO_MASK`) and consumers use the box.
  `Mask.rle` is COCO compressed RLE as JSON (`rulecam.perception.masks`).
- Every wire-format detail (and every unverified assumption, marked `ASSUMPTION`) lives in
  `meta_hosted/sam_wire.py`.

## Tests

```bash
uv run pytest tests/clients                 # recorded-style fixtures, no network
RULECAM_MODE=live uv run pytest -m live tests/clients   # real API, by hand only
```
