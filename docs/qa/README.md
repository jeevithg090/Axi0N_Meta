# QA suites

Bugs found by these suites and by hand are in [`bugs.md`](bugs.md).

## API smoke and security (`tests/e2e`)

```bash
uv run pytest tests/e2e                 # local, no network: runs in `make test` and CI (~40 s)
uv run pytest -m live tests/e2e         # read-only checks of the deployed API and UI bundle
RULECAM_E2E_API=https://<pod>-8000.proxy.runpod.net RULECAM_E2E_UI=https://<preview>.vercel.app \
  uv run pytest -m live tests/e2e       # another deployment
```

- `test_api_surface.py` builds the full app exactly as `uvicorn rulecam.api.app:app` does (every
  router, owner login on, canary values in every secret setting) and checks **every** HTTP route,
  including ones hidden from the OpenAPI: owner routes refuse a visitor, a forged session, a
  cross-origin write to the owner's loopback, and owner writes without CSRF or from another
  origin; nothing answers 5xx or leaks a traceback, local path, credential or canary; no CORS
  grant; static mounts do not traverse; login is rate-limited.
- A new route is owner-only unless it is added to `surface.PUBLIC` with a reason, so a route that
  is public by accident fails CI the day it lands.
- `test_live_api.py` (GET and OPTIONS only, paced ~3/s) and `test_live_bundle.py` (every page and
  `/_next/static` script of the UI, checked for credential patterns and for the values in a local
  `.env`, compared but never printed).

## UI end-to-end (`ui/e2e`, Playwright)

```bash
cd ui/e2e && npm ci                               # @playwright/test 1.64.1 (chromium 1248)
npx playwright install chromium                   # once, if the browser is missing
E2E_BASE_URL=http://localhost:3000 npx playwright test       # a running UI (next dev / start)
npm run test:live                                 # https://rulecam.vercel.app
E2E_SERVER="npm --prefix .. run start" npx playwright test   # start the built UI first
npx playwright show-report                        # traces and screenshots of failures
```

Two projects, desktop (1280×800) and phone (375×812). Every test fails on a console error, an
uncaught page error or a broken same-origin asset (`fixtures.ts`; filed noise is listed there with
its bug id).

- `pages.spec.ts`: every page loads (< 400), settles (no stuck "Loading…"), shows a LIVE / REPLAY /
  OFFLINE badge (REPLAY on `?demo=1`, never flipping to LIVE), has no horizontal overflow, and a
  visitor never sees the API's raw "owner login required"; a real 404 for unknown URLs.
- `journey.spec.ts`: every internal link on the landing resolves; each `/usecases/*` page opens;
  `/demo?demo=1` → **Verify it in your browser** → "Chain intact", the record count and the root
  match the link; replay links stay REPLAY; `/ask` and `/incidents` give a visitor a clear state.
- `worker.spec.ts`: the worker rules page on a 375 px phone with a stubbed API (rules, codes, tap
  targets ≥ 40 px, no identity words) and with the API down.
- `api-down.spec.ts`: every `/rulecam-api` call answers 503 and the socket is refused: no page
  hangs or says LIVE; the golden replay and its verify page still work.

The ui package's `tsconfig.json` and ESLint skip `e2e/` (its own `package.json` and lockfile), so
the app's dependencies and CI are unchanged.
