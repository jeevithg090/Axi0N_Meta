# `rulecam.api` and `rulecam.runtime` (P3-08)

## Purpose

One FastAPI app serves the UI, the phones and the verify page, and mirrors the event bus over
`/ws`. The runtime loop turns each `chunk.ready` into tracked, evaluated, verified, acted-on and
**hash-chained** events.

## Routes

| Route | What |
|---|---|
| `GET /health` | site, cache mode, whether the runtime runs |
| `GET /mode`, `PUT /mode {mode}` | LIVE/REPLAY badge state; `PUT` (owner) publishes `mode.changed` |
| `WS /ws` | (owner) every bus topic as `{"topic", "payload"}` JSON frames |
| `GET /evidence/days`, `/evidence/{day}/chain`, `/evidence/{day}/verify` | the chain JSONL and its `ChainReport` |
| `PUT /runtime/plan`, `GET /runtime/metrics` | (owner) load an `ActivePlan`; lag, keyframe mode, blind windows, failures |
| `GET/POST /plans`, `GET /plans/{id}`, `POST /plans/{id}/approve` | in-memory `SitePlan` registry (create/approve are owner-only; approve publishes `plan.updated`) |
| `POST /plans/uploads`, `POST /plans/requests` | (owner) planner intake: store a WAV/PDF/photo/MP4 (content type and leading bytes checked) as `uploads/<sha>.<ext>`; send request items, whose files must be such uploads, to the Site Planner runner |
| `GET /cameras` | paired phones and cameras that sent chunks |
| `GET /metrics/perception` | P1-03 `PerceptionMetricsReport` (SAM calls, gating, cost per camera-hour) |
| `/pair/...` | P1-09 Pair-a-Phone (`build_router`) |
| `POST /correction` | P1-10 correction loop (`correction_router`) |
| `/verify/` | static verify page, served from `<data dir>/verify` |

**Owner-only** means both the request host and the client are loopback, and a browser request
carries no `Origin` from another host (`require_owner` from Pair-a-Phone), because the same app is
reachable through the public tunnel and any web page can make the owner's browser call localhost. `/worker`, `/ack`,
`/interlock`, `/advice` and `/surprise` are mounted through `AppServices.routers` as they land.

## Runtime loop

`chunk.ready` → `Tracker` on the `SegmenterRouter` runtime segmenter (appends to the Parquet track
store, publishes `tracks.updated`) → `evaluate` per rule on a 30 s sliding window (episodes
already reported are dropped) → chain `candidate` + `candidate.new` → strip (P4-05, given the
plan's `privacy_mode`; `ActivePlan` defaults to `strict`) → **act first if the policy says so** →
Tier 1 → chain `verdict` + `verdict.new` → policy (P3-05) → actions (P3-06) → chain `action` +
`action.done`.

`runtime/factory.py` builds the real loop (`RULECAM_RUNTIME=true`): hosted SAM through the replay
cache, self-hosted SAM when Modal tokens are set, `Tracker`, a lock-guarded `ParquetTrackStore`
shared with the correction loop, and the Glimmer Tier 1 verifier. Until P3-05, P3-06 and P4-05
land it uses conservative placeholders: nothing acts first, no action runs, and no frames are
attached to candidates.

**Backpressure:** a chunk arriving more than 6 s after it ended switches to keyframe mode (one
chunk in 3) until the loop catches up. Skipped chunks are coverage gaps: a rule window that
overlaps one is not evaluated (`windows_blind`), so dropped frames never read as absences.
A chunk that fails with a `RulecamError` is logged and counted (`chunks_failed`); the loop goes on.

`Bus.bind_loop` (called in the app's lifespan) makes `publish` safe from worker threads.

## Commands

```bash
make api        # uvicorn rulecam.api.app:app --reload
make openapi    # writes ui/src/api/openapi.json (+ schema.d.ts once ui/ exists)
uv run pytest tests/api tests/runtime
```
