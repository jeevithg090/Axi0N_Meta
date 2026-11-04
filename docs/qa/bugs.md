# QA bug list (final hours before submission)

Owner of this file: QA (`p1/qa`). The lead forwards each open bug to its owner. Severity:
**P0** a judge hits something broken or misleading on the main path, or a secret leaks;
**P1** visible and confusing on a page a judge is likely to open, or it breaks the live demo;
**P2** polish, docs, or an edge a judge is unlikely to hit.

How the bugs were found: `tests/e2e` (API smoke and security, every route), `ui/e2e` (Playwright:
every page, the judge journey, the worker page on a 375 px phone, the API down), and a manual
pass in the browser on https://rulecam.vercel.app and on local builds of the agents' branches.
Re-run: see `docs/qa/README.md`.

Status as of 2026-11-04 01:20 UTC. Live = https://rulecam.vercel.app (deployed ~11 h before this
pass, API revision `559f976`).

## Open

| ID | Sev | Owner | Where | Summary |
|---|---|---|---|---|
| QA-B1 | P1 | lead / ops (pod `.env`) | live API | Owner login is off on the live pod: `/auth/me` says `login_enabled: false` |
| QA-B2 | P1 | UI agent (`lib/store.ts`, `ModeBadge`) + ops (`PUBLIC_DEMO`) | every live page | The badge says OFFLINE to every visitor while the API is up and LIVE |
| QA-B3 | P1 | warehouse (`ui/demo`) | `/demo?demo=1` | Three "Next golden run · Not recorded yet. Needs: … `rulecam golden capture`" blocks show internal TODOs to judges |
| QA-B4 | P1 | cleanup (deploy) | live UI | Production deploy is stale: `/system`, `/site`, `/streams`, `/audit`, `/extensions`, `/worker` answer 404 on rulecam.vercel.app |
| QA-B6 | P2 | warehouse (`/plans`), QA (`/cards`) | `/plans`, `/cards` | A visitor sees the API's raw "owner login required" instead of the owner-only notice with a Log in link |
| QA-B7 | P2 | QA (unowned `/alerts`) | `/alerts` | "Could not reach the Rulecam API" when the API answered 403 (it was reached; the visitor is not the owner) |
| QA-B8 | P2 | UI agent (`lib/stream.tsx`) | every page | Every page opens `wss://…/ws`; a visitor is refused, so each page logs 1–5 console errors, including the REPLAY page that needs no server |
| QA-B9 | P2 | ops (pod data) | `/planner` | The camera list shows 37 test cameras (`phone-66` … `phone-103`, `run5-030`) to visitors |
| QA-B10 | P2 | pitch (docs) | `docs/DEMO.md` | The checklist expects "✓ Chain intact · 32 records"; the replay shows 35 (the same doc says 35 above) |
| QA-B11 | P2 | warehouse (`ui/demo`) | `/demo?demo=1` | Step "audit" reads "audit_skipped: not audited, Spark Open failed (ModelTransportError)"; section 4 reads "0/0 decisions re-derived" |
| QA-B12 | P2 | QA (unowned `api/README.md`) | docs | `api/README.md` lists `GET /runtime/metrics` as owner-only; it is public (by design: no URLs or keys) |
| QA-B13 | P2 | warehouse (`evidence/verify_page`) | `/verify/` | The verify page has no way back to the app |
| QA-B14 | P1 | UI agent (`components/signage`) | `/monitor`, `/planner`, `/streams`, `/site`, `/incidents` (final-integration build) | 20 media files the pages play are not in the repo (`ui/public/videos/*.mp4` is gitignored, `/media/strips/cam05.jpg` does not exist): black 0:00 players and 404s |
| QA-B15 | P1 | UI agent (`components/signage/MonitorSignage.tsx`) | `/monitor` (final-integration build) | The sample walkthrough labels people "WORKER #208", "WORKER #204", "WORKER #201": reads as per-person identity, against the no-identity guardrail and the pitch |
| QA-B16 | P1 | warehouse (`ui/worker`) | `/worker/<plan>/<camera>` on a phone | Language and **Dispute** buttons are 22 px tall: too small to tap on the QR-poster page that is "mobile first, large tap targets" |
| QA-B17 | P2 | UI agent (`layout` / shell breadcrumb) | any unknown URL | The 404 page throws React hydration error #418: the breadcrumb renders the pathname, which differs between the prerendered `/_not-found` and the browser |

### QA-B1 · P1 · Owner login is off on the live pod

- **Repro:** `curl https://rulecam.vercel.app/rulecam-api/auth/me` →
  `{"owner":false,…,"login_enabled":false}`. `uv run pytest -m live tests/e2e -k login` fails.
- **Expected:** `login_enabled: true`, so the presenter can log in on `/login` (docs/DEMO.md
  "Owner logged in on the presenting laptop"). Today every owner page on the public site
  (planner, plans, monitor, alerts, ask, incidents, cards) is a dead end, and the "Log in" link
  leads to a login that answers 503.
- **Fix:** on the API pod, `uv run rulecam owner set-passphrase` → put `OWNER_PASSPHRASE_HASH`,
  `SESSION_SECRET` (32+ chars) and `PUBLIC_UI_URL=https://rulecam.vercel.app` in `.env`, restart
  the API (docs/deploy.md "Owner login"). Or decide the demo is shown from the pod's loopback and
  say so in DEMO.md.

### QA-B2 · P1 · The badge says OFFLINE while the stack is LIVE

- **Repro:** open https://rulecam.vercel.app/ (or any page but `/demo?demo=1`) as a visitor. The
  header badge reads **OFFLINE**; `GET /rulecam-api/mode` answers `{"mode":"live","badge":"LIVE"}`.
  The socket to `/ws` is refused (visitors may watch only when `PUBLIC_DEMO=true` and the origin is
  the UI's), and `badgeLabel` shows OFFLINE whenever the socket is closed.
- **Expected:** the landing promises "the badge above always says whether you are watching a LIVE
  run or a recorded REPLAY". A judge reads OFFLINE as "the system is down".
- **Fix (either):** set `PUBLIC_DEMO=true` (and `PUBLIC_UI_URL`) on the pod so the UI origin may
  watch `/ws` (read-only bus mirror; check the bus carries nothing private first); or, in the UI,
  when the socket is refused but `GET /mode` answered, show `LIVE · view only` (or the API's badge
  with a "no live feed for visitors" tooltip) instead of OFFLINE.

### QA-B3 · P1 · Internal TODOs on the judge's first page

- **Repro:** `/demo?demo=1`, bottom: three cards "Next golden run · … Not recorded yet. Needs:
  Record the 1 Nov campaign's T02 … `rulecam golden capture`, `golden record`, `golden export`."
- **Expected:** the replay page shows recorded runs only; pending slots are hidden from visitors
  (or one line: "Two more recorded runs are coming").
- **Fix:** render the pending golden slots only with `?dev=1` (or drop them from the public build).

### QA-B4 · P1 · Production UI deploy is stale

- **Repro:** `curl -o /dev/null -w '%{http_code}' https://rulecam.vercel.app/system` → 404 (also
  `/site`, `/streams`, `/audit`, `/extensions`, `/worker`); `ui/e2e` `pages.spec.ts` fails on them.
  `final-integration` builds all of them.
- **Fix:** redeploy the UI from the final branch (`vercel --prod` from `ui/`), then run
  `cd ui/e2e && npm run test:live` and `uv run pytest -m live tests/e2e`.

### QA-B6 · P2 · Raw "owner login required"

- **Repro:** visitor opens `/plans` → "Plans · owner login required"; `/cards` → "Rule cards ·
  owner login required".
- **Expected:** the shared `OwnerOnly` notice ("… is for the site owner. Log in to continue.").
- **Fix:** on a 403 from the API, render `<OwnerOnly what="…" checking={false} />`.

### QA-B7 · P2 · "/alerts" blames the network for a 403

- **Repro:** visitor opens `/alerts` → "Could not reach the Rulecam API; alerts will appear live."
- **Fix:** distinguish 403 (owner-only notice) from a network failure.

### QA-B8 · P2 · Console errors on every page from the refused socket

- **Repro:** open any page as a visitor, read the console: `WebSocket connection to
  'wss://7nv0ifjo2tmk00-8000.proxy.runpod.net/ws' failed` (1–5 times; the reconnect loop keeps
  trying). The REPLAY page opens it too.
- **Fix:** do not open the socket on replay pages; after a refusal (close before open) back off
  for longer and stop after a few tries for a visitor. Fixing QA-B2 with `PUBLIC_DEMO` fixes this.

### QA-B9 · P2 · Test cameras visible

- **Repro:** visitor opens `/planner` → "Cameras": 37 entries, `phone-66` … `phone-103`,
  `run5-030`, all "capture" except one.
- **Fix:** before the demo, clear old chunk folders / pairings on the pod (or show only cameras
  seen in the last hour).

### QA-B11 · P2 · Error names and 0/0 on the replay page

- **Repro:** `/demo?demo=1` section 1, step 9: "audit done · audit_skipped: not audited, Spark Open
  failed (ModelTransportError)". Section 4: "0/0 decisions re-derived from the stored track
  window".
- **Fix:** plain words ("audit: skipped, Muse Spark Open is not deployed"), and hide the re-derive
  line when there are no decisions (or say "no decisions to re-derive: nothing fired").

### QA-B13 · P2 · Verify page is a dead end

- **Repro:** `/demo?demo=1` → Verify it in your browser → no link back.
- **Fix:** a "← Back to Rulecam" link to `/` (or `document.referrer`).

### QA-B14 · P1 · Missing videos and stills behind the signage pages

- **Repro:** build `final-integration` (`cd ui && npm run build && npm start`), open `/monitor`:
  the "CAM-04 · Dock A" player is black at 0:00; the network tab shows
  `404 /videos/master_capabilities_demo.mp4`. Same on `/planner` (`cam02_intersection.mp4`),
  `/streams` (13 files `cam01_dock.mp4` … `cam12_corridor.mp4`), `/site`
  (`/media/strips/cam05.jpg`), `/incidents` (camera → video map). `ui/e2e` `pages.spec.ts`
  reports them as broken assets.
- **Cause:** `.gitignore` has `ui/public/videos/*.mp4` ("populated by
  scripts/seed_live_backend.py"); no checkout, CI build or Vercel deploy has them, and no
  `ui/public/media/` exists.
- **Fix:** commit small (≤ 2 MB, H.264, 10 s) clips under `ui/public/videos/` (un-ignore them), or
  point the players at the existing `public/golden/` and `public/strips/*.jpg` stills, or hide
  the player when the file is missing (`onError` → poster image + "clip not bundled").

### QA-B15 · P1 · "WORKER #208" labels on the monitor

- **Repro:** `/monitor` (final-integration build), scroll to the scenario cards: "WORKER #208
  Pedestrian · Right 1.18 m", "WORKER #204", "WORKER #201".
- **Expected:** no per-person identifiers anywhere (AGENTS.md rule 7; the pitch says workers are
  never identified). A judge reading "WORKER #208" sees an employee id.
- **Fix:** "Pedestrian A / B / C" (or "Person in zone"), no numbers that look like ids.

### QA-B16 · P1 · Worker page tap targets too small

- **Repro:** `ui/e2e` `worker.spec.ts` (375 px, stubbed worker page): buttons `EN`, `ES`,
  `Dispute` measure 22 px high.
- **Expected:** ≥ 44 px (Apple HIG / WCAG 2.5.5); the page's own comment says "large tap targets".
- **Fix:** `min-height: 44px; padding: 10px 14px` on the worker page buttons.

### QA-B17 · P2 · Hydration error on the 404 page

- **Repro:** open `/no-such-page` on a production build; console: "Uncaught Error: Minified React
  error #418". The header breadcrumb says "NO SUCH PAGE" from `usePathname()`, but the static
  `/_not-found` page was prerendered with another path.
- **Fix:** render the breadcrumb after mount (`useEffect`), or give `not-found.tsx` its own title.

## Fixed by QA

| ID | Sev | Where | Summary | Fix | Regression test |
|---|---|---|---|---|---|
| QA-B5 | P2 | every JSON route (API) | A non-UTF-8 body under a non-JSON content type crashed FastAPI's 422 handler (`UnicodeDecodeError`) → 500 | `src/rulecam/api/validation.py`: the 422 handler decodes bytes leniently | `tests/e2e/test_api_surface.py::test_public_writes_reject_bad_bodies_cleanly[binary]` |

## Checked and fine

- Every owner route (53 of 77 HTTP operations on the full app) refuses a visitor, a forged
  session cookie, a drive-by write to the owner's loopback from another origin, and an owner write
  without CSRF or from another origin; a real owner session reaches every owner read.
- No response (as visitor or owner) contains a traceback, a local path, a credential pattern or
  any configured secret (canary values in every secret setting, including webhook URLs).
- The API grants no CORS to a foreign origin (no `Access-Control-Allow-Origin`, preflight 405);
  the UI calls it through its same-origin rewrite.
- `/verify` static mount and `/pair/assets` do not traverse; `/evidence/{day}/chain` is owner-only
  and the public evidence routes carry hashes only. Login is rate-limited and never echoes.
- Live: every owner read answers 403; every public read is clean; every evidence day verifies
  (`ok`, and `n` agrees across verify, hashes and root); the UI rewrite reaches the same API.
- Live UI bundle: no credential pattern, no `.env` secret value, no server-only setting name in
  any page or `/_next/static` script; no source maps published.
- `/demo?demo=1` → Verify it in your browser: "✓ Chain intact · 35 records", matches the committed
  daily root; works with the API down.
