---
name: demo-preflight
description: Pre-demo checklist for Rulecam's live stack - warm the serverless GPUs, then run the docs/deploy.md checks (public API, owner auth, evidence, GPU health, metrics) and report pass/fail. Use before a demo, a recording, judging or the finals, or when asked whether the live site is ready.
---

# Demo preflight

Run these in order and report a table: check, expected, got, pass/fail. Stop and say so at the
first failure that would break the demo; do not "fix" the live stack without asking. IDs and
rollback steps are in the `rulecam-ops` skill. Never print key values.

## 1. Warm up (10 to 15 min before; costs $1.34/h per GPU while warm)

```bash
scripts/ops/warm.sh status      # workers.max should be 2 (SAM) and 3 (Glimmer); idle 300 s
scripts/ops/warm.sh on          # min 1 on both, waits until both /ping answer 200
```

`warm.sh` needs `RUNPOD_API_KEY` in the environment. If `workers.max` dropped (RunPod's idle
scale-down), say so: someone has to raise it in the RunPod console. **After the demo: `scripts/ops/warm.sh off`.**

## 2. Public API (through the Vercel site)

```bash
B=https://rulecam.vercel.app/rulecam-api
curl -s $B/health                                  # ok true, revision = the SHA we meant to ship,
                                                   # gpus.sam / gpus.glimmer warm (not unhealthy)
curl -s $B/mode                                    # {"mode":"live","badge":"LIVE"}
curl -s -o /dev/null -w '%{http_code}\n' -X PUT $B/mode -H 'Content-Type: application/json' \
  -d '{"mode":"replay"}'                           # 403
curl -s -o /dev/null -w '%{http_code}\n' -X PUT $B/mode -H 'X-Forwarded-For: 127.0.0.1' \
  -H 'Content-Type: application/json' -d '{"mode":"replay"}'   # 403
curl -s $B/auth/me                                 # "owner":false … "login_enabled":true
curl -s -o /dev/null -w '%{http_code}\n' $B/plans  # 200 with PUBLIC_DEMO=true, else 403
```

Compare `revision` with `git rev-parse HEAD` of the branch that should be live.

## 3. Evidence

Pick today's (or the demo's) day as `YYYY-MM-DD`:

```bash
curl -s -o /dev/null -w '%{http_code}\n' $B/evidence/<day>/chain    # 403
curl -s -o /dev/null -w '%{http_code}\n' $B/evidence/<day>/hashes   # 200
curl -s -o /dev/null -w '%{http_code}\n' $B/evidence/<day>/root     # 200
```

Then open `https://rulecam.vercel.app/rulecam-api/verify/?day=<day>`: the hashes match the root.

## 4. GPUs and metrics

```bash
curl -s https://7nv0ifjo2tmk00-8000.proxy.runpod.net/metrics | grep -E 'rulecam_dependency_(up|state)'
```

Expect `rulecam_dependency_up{service="glimmer"} 1` and `{service="sam"} 1`; while warm, state
`warm`. Do not curl the GPU URLs' `/health` directly (that wakes and bills workers); the
authenticated `/ping` in `warm.sh` is the GPU check.

## 5. By hand (ask the user to do these in the browser)

- Log in at `https://rulecam.vercel.app/login`: cookie `__Host-rulecam_owner` is HttpOnly,
  Secure, SameSite Strict; the planner, approve and questions controls appear; "Owner · Log out"
  removes them.
- The camera or the replay the demo uses shows live events on the dashboard.

## 6. On the API pod (optional, over SSH)

`uv run python -m rulecam.replay.preflight` with the key files exported, from `/workspace/rulecam`.
Run it detached if the session is flaky (see `rulecam-ops` gotchas).

## After

`scripts/ops/warm.sh off` as soon as the demo ends, then `scripts/ops/warm.sh status` to confirm
min is 0 on both.
