# Rulecam UI (P3-09)

Next.js 15 (App Router), TypeScript `strict`, ESLint, Prettier and Vitest.

```bash
npm ci
npm run dev                         # http://localhost:3000; API at NEXT_PUBLIC_RULECAM_API (default :8000)
open "http://localhost:3000/monitor?demo=1"   # recorded UC1 frames, no server needed (badge: REPLAY)
npm run lint && npm run typecheck && npm test -- --run
```

- **Every screen** sits in `components/DashboardShell.tsx`, which shows the **LIVE/REPLAY
  badge** in its header. The badge comes from one shared `GET /health` poll (`lib/health.ts`,
  every 10 s), the same one behind the sidebar's GPU lines, the landing's status band and the
  use-case status chip, so they never disagree:
  - **REPLAY** on a golden replay (`?demo=…`, which polls nothing) or when the API runs in replay
    mode;
  - **LIVE** when `/health` answers `ok` in any other mode (the API's own `badge_for`: `live`
    and `record` are LIVE);
  - **OFFLINE** only when `/health` fails or is not `ok`; "…" until it first answers.

  The `/ws` event socket is an enhancement: it can fill the badge before `/health` answers, and
  it never makes it OFFLINE. Screens of hand-written content carry `SampleDataTag` (SAMPLE DATA)
  and a SAMPLE badge in the nav. HTTP calls go through the same-origin `/rulecam-api/*` rewrite in
  `next.config.ts`, since the API sends no CORS headers; the socket connects to the API directly.
  The shell has three frames: the landing page (`/`, a wordmark bar), the app (grouped sidebar:
  Use cases, Build, Run, Trust, System) and a bare frame with no sidebar for outside-facing
  screens (`/worker/<plan>/<camera>`, `/station/<id>`, `/staff`, `/glasses`, `/inspector/view`).
- **Clips:** the sample boards' `/videos/*.mp4` are git-ignored and not deployed; `ClipPlayer`
  shows each camera's committed still from `public/strips/` with "not bundled" instead of a black
  player, and plays a clip only once its path is listed in `BUNDLED_CLIPS` and committed.
- **Design system:** Console tokens in `app/globals.css` (light and dark), the component kit in
  `components/kit/` (see its README), the **relay HUD**, which lists only the models that ran in
  the current segment, greys non-Meta models and resets when the segment changes.
- **State:** `lib/store.ts` is a pure reducer that folds `/ws` frames (`lib/events.ts`) into
  `UiState`. It is unit-tested.
- **Live monitor** (`/monitor`): one rule card per rule and camera (greyed out with a **BLIND**
  label when the camera's coverage is BLIND), a latency table per model, and a verifier panel per
  event: the predicate trace, Tier 1 and Tier 2 verdicts, expandable Spark Open reasoning, scene
  text shown as data, and the actions taken.
- `make openapi` writes `src/api/openapi.json`; `npm run openapi` generates `src/api/schema.d.ts`.
- **Planner** (`/planner`):
  - a request composer (typed text, voice recorded in the browser, a photo, PDF or walk-through
    upload through `POST /plans/uploads`), sent with `POST /plans/requests`. Only the types the
    planner's Understand step reads are offered: JPEG/PNG/WebP, PDF, MP4, and voice, which is
    re-encoded in the browser as 16 kHz mono WAV (`lib/wav.ts`) for Voice Transcribe;
  - the ten fixed steps from `planner.step`;
  - camera tiles from `GET /cameras` plus the survey inventory.
- **Plans** (`/plans`, `/plans/[id]`):
  - a plain-language and YAML toggle;
  - feasibility cards (Observable / Caveat / Not observable / Declined, always in words), with
    the camera's first-person line, probe evidence and the fix or alternative;
  - the acceptance trace (passed/total, repair rounds, failing checks) and the Spark Open audit;
  - Approve, and a **diff** against the last approved version (rules added, removed or changed,
    plus settings) with *Approve vN* / *Keep vN-1*.
- `?demo=1` works on every screen: recorded UC1 frames plus a v1 (approved) and v2 (proposed) plan.
- **Alerts PWA** (`/alerts`, phone-first): your role is remembered on the device; each alert has big
  *On my way* / *False alarm* / *Escalate* buttons (`POST /alerts/{id}/respond`) and its escalation
  timeline from `action.done`.
- **Station display** (`/station/<camera>`, the sink tablet): a huge countdown driven by
  `station.progress` ("12 / 20 s"), frozen red at "8 / 20 s · Too short" on a failed wash, plus the
  coaching line from `station.coach`.
- **Rule CI** (`/ci`): per rule, from `ci.result` (`CIResult{rule_id, report, ci_green, summary}`)
  — green/red and the summary exactly as the backend computed them, then boundary traces,
  mutations and `report.red_team`: scenes, lookalikes (and what fooled it, with the fix), injection
  signs, the alert forecast against the budget. Demo payloads (`src/fixtures/ci-results.json`) are
  real serialised `CIResult`s, checked by `tests/rule_ci/test_ui_fixture.py`.
- **Card gallery** (`/cards`): rule cards with their CI verdict and summary, and declined cards that show the
  reason and alternative but **never the request text**.
- **Surprise Lens** (`/surprise`): the z-score curve with its alert line, the SAM labels at the hot
  spot, and *Draft a rule from this*, which only the demo offers: no backend route drafts a rule
  from a surprise yet, so live it is disabled and says so. Surprise never cuts power.
