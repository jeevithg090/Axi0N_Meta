# `rulecam.api` and `rulecam.runtime` (P3-08)

## Purpose

One FastAPI app serves the UI, the phones and the verify page, and mirrors the event bus over
`/ws`. The runtime loop turns each `chunk.ready` into tracked, evaluated, verified, acted-on and
**hash-chained** events.

## Routes (this PR)

| Route | What |
|---|---|
| `GET /health` | site, cache mode, whether the runtime runs |
| `GET /mode`, `PUT /mode {mode}` | LIVE/REPLAY badge state; `PUT` publishes `mode.changed` |
| `WS /ws` | every bus topic as `{"topic", "payload"}` JSON frames |
| `GET /evidence/days`, `/evidence/{day}/chain`, `/evidence/{day}/verify` | the chain JSONL and its `ChainReport` |
| `PUT /runtime/plan`, `GET /runtime/metrics` | load an `ActivePlan`; lag, keyframe mode, counts |

Routers from other tasks are mounted through `AppServices.routers`: `/pair` (P1-09
`build_router`), `/correction` (P1-10 `correction_router`), and `/plans`, `/worker`, `/ack`,
`/interlock`, `/advice`, `/surprise` as they land.

## Runtime loop

`chunk.ready` → tracker (router segmenter, track store, `tracks.updated`) → `evaluate` per rule
on a 30 s sliding window (episodes already reported are dropped) → chain `candidate` +
`candidate.new` → strip (P4-05) → **act first if the policy says so** → Tier 1 → chain `verdict` +
`verdict.new` → policy (P3-05) → actions (P3-06) → chain `action` + `action.done`.

Strip rendering, the policy and actions are protocols in `runtime/loop.py`, so the loop runs with
fakes until those tasks land. Backpressure: a chunk arriving more than 6 s after it ended
switches to keyframe mode (one chunk in 3) until the loop catches up.

`Bus.bind_loop` (called in the app's lifespan) makes `publish` safe from worker threads.

## Commands

```bash
make api        # uvicorn rulecam.api.app:app --reload
make openapi    # writes ui/src/api/openapi.json (+ schema.d.ts once ui/ exists)
uv run pytest tests/api tests/runtime
```
