---
name: demo-preflight
description: Pre-demo checklist for Rulecam's live stack (Meta API only, site on Vercel, API on the team's Mac behind a Cloudflare tunnel) - check the local servers and tunnels, run the model and use-case checks, then the public API, owner auth and evidence checks, and report pass/fail. Use before a demo, a recording, judging or the finals, or when asked whether the live site is ready.
---

# Demo preflight

Run these in order and report a table: check, expected, got, pass/fail. Stop and say so at the
first failure that would break the demo; do not "fix" the live stack without asking. How it
runs and how to restart it: `docs/deploy.md` "Meta API only". Never print key values.

Since 4 Nov 2026 there are no GPUs to warm: every model call goes to the Meta API (Tier 1 is
Muse Spark 1.3, Tier 2 is Muse Wonder 1.0, SAM 3.1 hosted). The site is the Vercel project
`rulecam-live` (`PUBLIC_UI_URL`); the API runs on the team's Mac behind a Cloudflare quick tunnel
(`PUBLIC_TUNNEL_URL` in `.env.live`). A tunnel that restarted has a new address: redeploy the site
with `NEXT_PUBLIC_RULECAM_API` set to it (never `localhost`, or visitors count as the owner);
`docs/deploy.md` "The site on Vercel" has the commands. The site's address does not change.

## 1. Local API and tunnel

```bash
curl -s http://127.0.0.1:8000/health      # ok true, runtime true, models.tier1 muse-spark-1.3 (meta-api)
pgrep -fl cloudflared                      # one tunnel, to :8000
curl -s $PUBLIC_TUNNEL_URL/health          # same answer; if not, the tunnel dropped (see above)
pgrep -fl caffeinate                       # keeps the Mac awake (closing the lid on battery still sleeps it)
```

## 2. Models and use cases (from the repo root, live settings loaded)

```bash
set -a; . ./.env.live; set +a
uv run python scripts/ops/model_proofs.py                         # Tier 1, Tier 2, plan audit, Ask review: all PASS
uv run python scripts/ops/preflight_usecases.py --api http://127.0.0.1:8000
```

Expected: supermarket and warehouse lines green. `api / models` stays yellow while Prompt Guard 2
is not loaded, and `crowd / assess` yellow while no crowd camera runs.

## 3. Public API (through the site)

```bash
B=$PUBLIC_UI_URL/rulecam-api
curl -s $B/health                                  # ok true, models all meta-api
curl -s $B/mode                                    # {"mode":"live","badge":"LIVE"}
curl -s -o /dev/null -w '%{http_code}\n' -X PUT $B/mode -H 'Content-Type: application/json' \
  -d '{"mode":"replay"}'                           # 403
curl -s -o /dev/null -w '%{http_code}\n' -X PUT $B/mode -H 'X-Forwarded-For: 127.0.0.1' \
  -H 'Content-Type: application/json' -d '{"mode":"replay"}'   # 403
curl -s $B/auth/me                                 # "owner":false … "login_enabled":true
curl -s -o /dev/null -w '%{http_code}\n' $B/plans  # 200 (PUBLIC_DEMO=true since 4 Nov), else 403
curl -s -o /dev/null -w '%{http_code}\n' $B/connectors/cameras   # 403
```

Then for a visitor (no login): `$PUBLIC_UI_URL/demo?demo=1`, the verify link in `README.md`,
`/usecases/warehouse`, `/staff` and `/glasses` answer 200.

## 4. Evidence

Pick today's (or the demo's) day as `YYYY-MM-DD`:

```bash
curl -s -o /dev/null -w '%{http_code}\n' $B/evidence/<day>/chain    # 403
curl -s -o /dev/null -w '%{http_code}\n' $B/evidence/<day>/hashes   # 200
curl -s -o /dev/null -w '%{http_code}\n' $B/evidence/<day>/root     # 200
```

Then open `$PUBLIC_UI_URL/rulecam-api/verify/?day=<day>`: the hashes match the root.

## 5. By hand (ask the user to do these in the browser)

- Log in at `$PUBLIC_UI_URL/login`: cookie `__Host-rulecam_owner` is HttpOnly, Secure, SameSite
  Strict; the planner, approve and questions controls appear; "Owner · Log out" removes them.
- The sidebar lists Tier 1, Tier 2 and SAM 3.1 on the Meta API, and the cameras show live
  events on the dashboard.

## After

Nothing to switch off: the Meta API bills per call. A looped clip on a camera with a
high-severity rule keeps calling hosted SAM 3.1 (`/runtime/metrics` shows the spend); remove
that camera (`DELETE /connectors/cameras/<id>` from the Mac) when the demo is over.
