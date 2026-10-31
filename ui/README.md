# Rulecam UI (P3-09)

Next.js 15 (App Router), TypeScript `strict`, ESLint, Prettier and Vitest.

```bash
npm ci
npm run dev                         # http://localhost:3000; API at NEXT_PUBLIC_RULECAM_API (default :8000)
open "http://localhost:3000/monitor?demo=1"   # recorded UC1 frames, no server needed (badge: REPLAY)
npm run lint && npm run typecheck && npm test -- --run
```

- **Every screen** sits in `components/Shell.tsx`: the **LIVE/REPLAY badge**, set from
  `GET /mode` and `mode.changed` (OFFLINE while the socket is down or reconnecting, and the last
  mode is forgotten until `GET /mode` answers again; it never switches silently). HTTP calls go
  through the same-origin `/rulecam-api/*` rewrite in `next.config.ts`, since the API sends no
  CORS headers; the socket connects to the API directly,
  and the **relay HUD**, which lists only the models that ran in the current segment, greys
  non-Meta models and resets when the segment changes.
- **State:** `lib/store.ts` is a pure reducer that folds `/ws` frames (`lib/events.ts`) into
  `UiState`. It is unit-tested.
- **Live monitor** (`/monitor`): one rule card per rule and camera (greyed out with a **BLIND**
  label when the camera's coverage is BLIND), a latency table per model, and a verifier panel per
  event: the predicate trace, Tier 1 and Tier 2 verdicts, expandable Spark Open reasoning, scene
  text shown as data, and the actions taken.
- `make openapi` writes `src/api/openapi.json`; `npm run openapi` generates `src/api/schema.d.ts`.
