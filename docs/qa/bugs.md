# QA bug list (final hours before submission)

Owner of this file: QA (`p1/qa`). The lead forwards each open bug to its owner. Severity:
**P0** a judge hits something broken or misleading on the main path, or a secret leaks;
**P1** visible and confusing on a page a judge is likely to open, or it breaks the live demo;
**P2** polish, docs, or an edge a judge is unlikely to hit.

How the bugs were found: `tests/e2e` (API smoke and security, every route), `ui/e2e` (Playwright:
every page, the judge journey, the worker page on a 375 px phone, the API down), and a manual
pass in the browser on https://rulecam.vercel.app and on local builds of the agents' branches.
Re-run: see `docs/qa/README.md`.

Status as of 2026-11-04 01:15 UTC. Live = https://rulecam.vercel.app (deployed ~11 h before this
pass, API revision `559f976`). Branch builds tested: `p1/final-integration` 0b35805,
`p1/ui-hallmark`, `p1/uc-warehouse`, `p1/uc-crowd`, `p1/uc-supermarket` (heads at 00:55 UTC).

## Open

| ID | Sev | Owner | Where | Summary |
|---|---|---|---|---|
| QA-B18 | **P0** | supermarket (`ui ask`; `components/signage/AskSignage.tsx`) | `/ask` (final-integration and every agent branch) | Ask fabricates answers: when the API refuses (a visitor) or fails, `runSynthesizedAgent` plays a canned trace ending "muse-spark-open accepted the answer" with a fake receipt; the page also opens on a canned exchange. No SAMPLE tag |
| QA-B1 | P1 | lead / ops (pod `.env`) | live API | Owner login is off on the live pod: `/auth/me` says `login_enabled: false` |
| QA-B2 | P1 | UI agent (`lib/store.ts`, `ModeBadge`) + ops (`PUBLIC_DEMO`) | every live page | The badge says OFFLINE to every visitor while the API is up and LIVE |
| QA-B3 | P1 | warehouse (`ui/demo`) | `/demo?demo=1` | Three "Next golden run · Not recorded yet. Needs: … `rulecam golden capture`" blocks show internal TODOs to judges |
| QA-B4 | P1 | cleanup (deploy) | live UI | Production deploy is stale: `/system`, `/site`, `/streams`, `/audit`, `/extensions`, `/worker` answer 404 on rulecam.vercel.app |
| QA-B6 | P2 | warehouse (`/plans`) | `/plans` | A visitor sees the API's raw "owner login required" ("Site Plans Registry · … owner login required") instead of the owner-only notice; `/cards` is fixed on `p1/qa`, and `usePlans()` now returns `forbidden` for `/plans` to use |
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
| QA-B19 | P1 | UI agent (`components/signage/SystemSignage.tsx`) | `/system` | Made-up numbers shown as live: "Candidates Emitted 7", "Tier-1 Verifications 7 / 0" (wrong field names, so always 7), mode "LIVE" when `/health` fails, cost $0.14 and cap $0.50 defaults, "Muse Spark Open 70B" Tier 2, "10.0 Hz Engine" |
| QA-B20 | P1 | supermarket (`ui site`; `components/signage/SiteSignage.tsx`) | `/site` | Seeded web-context results (FSA recalls naming real brands, "Searches: 15 · Pages Read: 87", "✓ source picked from the pages the search returned", "LIVE EVAL VERIFIED (PR #145)") shown as today's live research with no SAMPLE/recorded label |
| QA-B21 | P1 | lead (merge order) | landing (`p1/ui-hallmark`) | The new landing links `/usecases/supermarket`, `/usecases/warehouse`, `/usecases/crowd`; each page lives on its own branch, so the landing has dead links unless all three merge with it |
| QA-B22 | P2 | warehouse (`/ci`, `/worker`), supermarket (`/advice`, `/site`) | 375 px phone | Content is cut off at the right edge: the shell's `main` scrolls sideways (`/site` label to 492 px, a span to 941 px; `/advice` cards to 417 px; `/ci` trace rows, `/worker` camera select to ~390 px) |
| QA-B23 | P1 | warehouse (`/usecases/warehouse`), crowd (`/usecases/crowd`) | use-case pages, visitor | A visitor sees the API's raw "owner login required" on both use-case pages; `/usecases/warehouse` also shows a raw "Not Found" and "Loading the story…" forever when the API lacks `/warehouse/story` (UI deployed before the API) |
| QA-B24 | P1 | supermarket | `/usecases/supermarket` | No branch has this page yet (`p1/uc-supermarket` has no `ui/src/app/usecases/`), but the new landing links it |
| QA-B25 | P2 | warehouse | `/usecases/warehouse` | Two nested `<main>` landmarks (the shell's and the page's): invalid HTML, confuses screen readers |
| QA-B26 | P2 | warehouse (`ui/demo`) | `/demo?demo=1` | The first card is tagged "RECORDED LIVE RUN · 1 NOV 2026 · NOT A REPLAY, NOT LIVE NOW" while the header badge says REPLAY: two labels that contradict each other on the judge's first page |
| QA-B27 | P2 | UI agent (`components/demo/SystemStatus.tsx`, `lib/health.ts`) | landing (`p1/ui-hallmark`) | "The system right now" says **OFFLINE · The Rulecam API is not reachable** whenever the API is up in REPLAY mode (`isLive` needs `mode === "live"`), right above "API ok · Mode replay"; the header badge says REPLAY. The DEMO.md fallback (switch to replay) would show this |
| QA-B29 | P2 | compliance | `/inspector` (`p1/compliance`) | A visitor sees the API's raw "owner login required" on the proof portal (the pack list/creation is owner-only; show the `OwnerOnly` notice and keep the explanation) |
| QA-B28 | P2 | crowd | `/usecases/crowd` | With no crowd camera the page shows operator hints to judges ("crowd watch needs RULECAM_PROTOTYPE_MODE=true and RULECAM_CROWD_EVERY_S > 0", "scripts/crowd/feed_clip.py") and two empty dropdowns; no recorded crowd run to look at |

### QA-B18 · P0 · Ask fabricates answers when the API says no

- **Repro:** build any branch, open `/ask`. The page opens on a finished exchange ("Two sub-2.0m
  encounters were recorded today … CONFIRMED VIOLATION …", "Review · muse-spark-open accepted the
  answer", "receipt 9f41e8c20a7b"). Type any question as a visitor (the API answers 403) or with
  the API down: `executeQuestion` catches the failure and calls `runSynthesizedAgent`, which
  streams a canned SQL step, a canned Glimmer look, a canned Spark Open review and a canned answer
  with a fake receipt. Nothing on the page says it is sample content; the badge says LIVE/OFFLINE.
- **Why P0:** a judge asks a question and gets a made-up answer that claims a model review that
  is not deployed (README: Spark Open is not live). Also the header claims "11 camera streams" and
  lists an `identity_spans` table (reads as identity tracking).
- **Fix:** delete the synthesized fallback; on 403 render `<OwnerOnly what="Asking about the
  cameras" />`, on other errors show the error. If a worked example must stay, put it under
  `<SampleDataTag what="An example exchange, written for the interface …" />`, and never label it
  with a model or receipt. Drop `identity_spans` and the fixed "11".

### QA-B19 · P1 · /system shows invented runtime numbers

- **Repro:** `/system` on a build with a running API: "Candidates Emitted **7**", "Tier-1
  Verifications Completed / Failed **7 / 0**" while `/runtime/metrics` says `"candidates": 0`.
  `SystemSignage.tsx` reads `runtime?.candidates_emitted ?? 7` and
  `runtime?.verifications_completed ?? 7`; the API sends `candidates` and has no
  `verifications_completed`, so it is always 7. Other fallbacks shown as data:
  `health?.mode ?? "LIVE"` (says LIVE when `/health` fails), `cost ?? 0.14`, cap `?? 0.5` (the
  API's default is 12), `tier2 ?? "spark-open"` / `"Muse Spark Open 70B"`, "10.0 Hz Engine",
  `revision ?? "dev-worktree (HEAD)"`, `workers_max ?? 1`.
- **Fix:** render "—" (or "unknown") for every missing field; map to the real fields
  (`candidates`, `verifications_failed`, `verify_errors`); never default the mode to LIVE.

### QA-B20 · P1 · /site shows seeded research as today's

- **Repro:** `/site` (owner or visitor, API without `/context` data): "Around the site today",
  two FSA recall notices naming real brands (Walkers, Gilbert's Kosher Foods), a Bonfire Night
  event, "Searches: 15 · Pages Read: 87", "✓ SOURCE PICKED FROM THE PAGES THE SEARCH RETURNED",
  "LIVE EVAL VERIFIED (PR #145) ✓". These come from `SEEDED_CONTEXTS` (asked_at 2026-11-03).
- **Expected:** if it is a recorded run, say "Recorded 3 Nov 2026 run (REPLAY)" on it; if it is
  hand-written, `SampleDataTag`. Check that each recall item really came from the search (recall
  notices about real companies must not be invented).
- **Fix:** label the seeded block; prefer showing it only in `?demo=1`.

### QA-B21 · P1 · Landing links need all three use-case branches

- **Repro:** `p1/ui-hallmark` build: `ui/e2e` `journey.spec.ts` → "404 /usecases/supermarket",
  "404 /usecases/warehouse", "404 /usecases/crowd".
- **Fix (lead):** merge `p1/uc-supermarket`, `p1/uc-warehouse` and `p1/uc-crowd` before (or with)
  `p1/ui-hallmark`, or hide a card whose page is not in the build. Re-run `journey.spec.ts` on the
  merged build.

### QA-B23 · P1 · Use-case pages show API refusals to visitors

- **Repro:** `ui/e2e` `visitor.spec.ts` (the API answers only its public routes, as the deployed
  API does for a visitor) on the `p1/uc-warehouse` and `p1/uc-crowd` builds: both pages contain
  "owner login required". With an API that lacks the branch's routes (`/warehouse/story`,
  `/warehouse/reports` → 404), `/usecases/warehouse` shows "Not Found" in red and "Loading the
  story…" never ends.
- **Expected:** judges reach these pages from the landing without logging in: show the recorded
  story (labelled REPLAY / recorded) or the owner-only notice for the controls, and an empty or
  error state instead of a loader when a call fails.
- **Fix:** catch 403 → `OwnerOnly` for the owner-only panels, keep the public explanation visible;
  catch 404/5xx → "Not available on this server" and stop the loader. Deploy the API before the UI.

### QA-B26 · P2 · "NOT A REPLAY" under a REPLAY badge

- **Fix:** "RECORDED ON THE DEPLOYED STACK · 1 NOV 2026" (drop "not a replay"), or explain once:
  "REPLAY = a recorded run; nothing on this page is live".

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

- **Repro:** visitor opens `/plans` → "Plans · owner login required" (live) / "Site Plans
  Registry · GET /plans (0 registered) · … owner login required" (branch builds).
- **Expected:** the shared `OwnerOnly` notice ("… is for the site owner. Log in to continue.").
- **Fix:** `const { forbidden } = usePlans()` (added on `p1/qa`) → render
  `<OwnerOnly what="The site's plans" checking={false} />`, as `/cards` now does.

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

## Merge risk (for the lead)

`git merge-tree` of each branch head (01:20 UTC) into `p1/final-integration` 0b35805:

| Branch | Result |
|---|---|
| `p1/ui-hallmark`, `p1/uc-crowd`, `p1/staff-app`, `p1/compliance`, `p1/pitch`, `p1/qa` | clean |
| `p1/uc-warehouse` | conflict in `src/rulecam/api/app.py` |
| `p1/uc-supermarket` | conflicts in `src/rulecam/api/app.py`, `ui/src/components/signage/AskSignage.tsx` |
| `p1/glasses` | conflict in `src/rulecam/api/app.py` |
| `p1/staff-app` + `p1/compliance`, `p1/ui-hallmark` + `p1/uc-warehouse` | conflict in `src/rulecam/api/app.py` |

`app.py` is where every branch mounts its router. After resolving it, run
`uv run pytest tests/e2e`: a router mounted without the owner dependency fails
`test_every_owner_route_refuses_a_visitor` (unless it is meant to be public: add it to
`surface.PUBLIC` with the reason).

## Fixed by QA

| ID | Sev | Where | Summary | Fix | Regression test |
|---|---|---|---|---|---|
| QA-B5 | P2 | every JSON route (API) | A non-UTF-8 body under a non-JSON content type crashed FastAPI's 422 handler (`UnicodeDecodeError`) → 500 | `src/rulecam/api/validation.py`: the 422 handler decodes bytes leniently | `tests/e2e/test_api_surface.py::test_public_writes_reject_bad_bodies_cleanly[binary]` |
| QA-B6 (cards) | P2 | `/cards` | Raw "owner login required" | `usePlans` reports `forbidden`; the page renders `OwnerOnly` | `ui/src/app/cards/owner-only.test.tsx`, `ui/e2e` `visitor.spec.ts` |
| QA-B7 | P2 | `/alerts` | "Could not reach the Rulecam API" on a 403 | 403 → `OwnerOnly`; a network failure still says so | `ui/src/app/cards/owner-only.test.tsx` |

## Checked and fine

- `/staff` (`p1/staff-app`, desktop and 375 px): onboarding, role, feed for the owner; a visitor
  whose feed is refused is not told "Nothing needs you right now" without an explanation
  (`ui/e2e` `staff.spec.ts`). `/glasses` (`p1/glasses`): loads clean on both viewports, owner and
  visitor; it says the phone camera stands in for the glasses. `/inspector` (`p1/compliance`):
  clean for the owner (see QA-B29 for visitors).

- Branch APIs (01:10 UTC heads of `p1/glasses`, `p1/staff-app`, `p1/compliance`,
  `p1/uc-supermarket`, `p1/uc-warehouse`, `p1/uc-crowd`): `tests/e2e/test_api_surface.py` run on
  each branch's code: every new route refuses visitors, forged sessions, cross-origin and
  CSRF-less writes; no leaks; only failure is QA-B5 (fixed here, arrives with the merge).
  `p1/compliance` adds two deliberately public routes, `GET /proof/shared/{token}` and
  `…/document` (the token is the key): listed in `surface.PUBLIC_PENDING`; move them to `PUBLIC`
  when it merges.

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
