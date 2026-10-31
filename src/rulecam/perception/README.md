# `rulecam.perception`

Pixels to detections, tracks and coverage (owner: P1). "Hosted to plan, open weights to run"
(MP §1, §3.4, §10): hosted SAM 3.1 for probes and Rule CI, self-hosted SAM 3.1 for the
continuous runtime, with a motion gate and a static cache so the runtime makes as few calls as
it can.

## Modules

| Module | Task | Public API |
|---|---|---|
| `sam_runtime.py` | P1-03 | `ModalSam31` (a `Segmenter`), `ModalWorker`, `SamRequest` / `SamResponse` (wire format), `SamWorker` |
| `sam_worker.py` | P1-03 | `Sam31Engine` (runs SAM 3.1 on a GPU), `build_app()` and `deploy()` for the Modal app `rulecam-sam` |
| `motion_gate.py` | P1-03 | `MotionGate`, pure `motion_stats` / `decide`, `GateConfig`, `GateDecision` |
| `static_cache.py` | P1-03 | `StaticCache` (listens on `coverage.changed`), `moved_camera`, `StaticServedSegmenter` (runtime) |
| `router.py` | P1-03 | `SegmenterRouter`, `Purpose`, `RoutingPolicy`, `FallbackSegmenter`, `MeteredSegmenter`, `CallMeter` |
| `metrics.py` | P1-03 | `PerceptionMetrics` (`report()` is the body of `GET /metrics/perception`) |
| `masks.py` | P1-02 | `encode_rle` / `decode_rle`: `Mask.rle` as JSON `{"size": [h, w], "counts": "<COCO RLE>"}` |
| `errors.py` | shared | `PerceptionError`; P1-03 adds `SamRuntimeError`, `SamRuntimeUnavailableError`, `SamResponseError` |

## How the P1-03 pieces fit

```python
metrics = PerceptionMetrics()
router = SegmenterRouter(
    HostedSam31(...),
    ModalSam31(bus=bus, metrics=metrics),
    clock=SystemClock(),
    metrics=metrics,
    on_fallback=alert,
)
runtime = router.for_purpose(Purpose.RUNTIME)  # self-hosted, hosted while Modal is down
gate, static = MotionGate(metrics=metrics), StaticCache(runtime)
asyncio.create_task(static.run(bus))  # drops a camera's cache when it moves

decision = gate.decide(chunk, active_rules)  # dynamic concepts to run on this chunk
for concept in decision.run:
    runtime.segment(chunk, concept)
for concept in static_concepts:  # ConceptSpec.static is true
    static.get(chunk, concept, calibration=calibration_id)
```

`coverage.changed` payloads that invalidate the static cache: the watchdog's `{"camera_id": ...,
"state": "blind", "reason": "moved", "moved": true, "ts": ...}`, or simply `{"camera_id": ...,
"state": "moved"}`.

## Deploying self-hosted SAM 3.1

Needs `uv sync --extra ml`, and `HF_TOKEN` (with access to the gated `facebook/sam3.1`),
`MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` in `.env`:

```bash
uv run python -m rulecam.perception.sam_worker
```

This deploys `rulecam-sam` on one warm L4 container. The GPU image installs Meta's `sam3` package
from GitHub at a pinned commit, because the SAM 3.1 checkpoint has no Transformers integration and
`sam3` needs numpy < 2, so it cannot live in the repo's lock.

## Tests

```bash
uv run pytest tests/perception              # unit tests, no network, no GPU
uv run pytest tests/perception -m gpu       # the real Modal call (deployed app + tokens)
```
