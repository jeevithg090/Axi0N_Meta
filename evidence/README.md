# Evidence

What backs the claims in the README and the video. Every item here was captured from a real run
of the code in this repository; nothing is mocked up.

## Screenshots (`screenshots/`)

| File | Shows | Task |
|---|---|---|
| `P3-07b-verify-intact.jpg` | The browser verify page on an intact evidence log: every SHA-256 and the Merkle root recomputed in the browser, no server | P3-07b |
| `P3-07b-verify-broken-117.jpg` | The same page after record 117 was changed: the chain breaks at exactly that record | P3-07b |
| `P3-09a-monitor-375px.jpg` | The live monitor and verifier panel at phone width (375 px) | P3-09a |
| `P3-09b-planner-375px.jpg` | The planner screen at 375 px | P3-09b |
| `P3-09b-feasibility-375px.jpg` | The feasibility screen: what a camera can and cannot check, before anything goes live | P3-09b |
| `P3-09b-plan-diff-375px.jpg` | The plan diff against the last approved plan | P3-09b |

## Golden runs

Recorded live runs live in `ui/public/golden/` and replay with no model calls (`/demo?demo=1`,
labelled REPLAY). See [`docs/DEMO.md`](../docs/DEMO.md) for what each run does and does not show.

## Evaluation

Measured numbers are in [`eval/RESULTS.md`](../eval/RESULTS.md).

## Checking an evidence log yourself

```bash
uv run rulecam audit 2026-11-01 <site>   # chain intact + every decision re-derived (E9)
```

or open the verify page and drop the day's chain file on it. A changed, removed or reordered record breaks the
chain at that record, and the daily Merkle root no longer matches.
