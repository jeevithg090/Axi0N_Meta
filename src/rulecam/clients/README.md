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
