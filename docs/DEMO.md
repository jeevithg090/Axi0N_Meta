# Rulecam demo: the 3-minute live script and the rehearsal checklist

Two ways to show Rulecam (PLAN-9.5 §9):

- **The golden REPLAY** (`https://rulecam.vercel.app/demo?demo=1`): a recorded live run, replayed
  with no model calls and labelled REPLAY. Anyone can open it; it works when every pod is off.
  Use it as the opener, and as the fallback whenever something live misbehaves.
- **The live demo** below: the owner's phone as the camera, on the deployed stack, labelled
  LIVE. Never present a replay as live, or the other way round: the badge in the header says
  which one the audience is watching, and it never switches silently.

## What the golden REPLAY shows (and what it does not)

`warehouse_driver` is the stock driver-only clip (a negative: nobody should be alerted). Recorded
on 1 Nov 2026 (third record, after the operator warm-up, P1-07), it shows honestly what the
deployed system did:

1. The request: "Keep people at least 2 metres away from a moving forklift."
2. The planner's steps with their real details (survey, calibration from a pallet, probes,
   compile, lint, acceptance), the rule in canonical form, and **Rule CI green: boundary 16/16,
   mutations 6/6** (with the warm-up traces and the `drop_operator_warmup` mutant), re-run
   offline and identical to the approval.
3. The runtime: **no candidate, no action, no alert**. The seated driver, first seen already on
   board, is held as the forklift's operator during the warm-up and then linked; the eval
   harness counts 0 false alerts on the negative clip. (The earlier record of 1 Nov fired once
   on the driver's first keyframes; that window is now the Rule CI and unit-test regression
   `tests/fixtures/rules/golden_warehouse_driver.json`.)
4. The evidence chain: 35 records intact from the day's first, the daily root, and the browser
   verify page that recomputes every SHA-256 itself.

Say it the way it happened: "the driver is the forklift's operator, so nothing fired". The REPLAY
has no strip or Glimmer verdict to show; use the live demo for a verified alert.

## Live demo script (3 minutes)

Roles: the **presenter** drives the laptop; the **actor** carries a phone-camera and a cart or
trolley as the forklift. Everything below is on the deployed stack; the badge reads LIVE.

| Time | Do | Say (short) |
|---|---|---|
| 0:00 | Open `/demo?demo=1`, scroll the four sections. | "A recorded run, replayed with zero model cost. Every claim here is re-derived in CI." |
| 0:25 | Click **Verify it in your browser**. | "Your browser recomputes every hash; nothing is trusted from us." |
| 0:40 | Switch to `/planner` (LIVE badge). Type the rule: "Keep people at least 2 metres away from a moving cart." | "Plain words in. The planner has to prove it can see what it needs." |
| 0:50 | Watch the steps: survey, probes, calibration (pallet or a 1 m floor square in view), compile, Rule CI. | "It calibrates from a pallet, probes the camera 24 times, and Rule CI tries to break the rule before we trust it." |
| 1:30 | Open the plan; read the acceptance trace; **Approve**. | "Nothing runs until a person approves a plan that passed every check." |
| 1:45 | Actor pushes the cart; a second person walks past it at about 1 m. | — |
| 2:05 | `/monitor`: the rule card, the candidate, the strip, Glimmer's verdict and reasoning. | "Glimmer looks at the strip before anyone is bothered." |
| 2:30 | Open the evidence verify page for today. | "Every decision is hash-chained; the public gets hashes, the owner gets the records." |
| 2:45 | Dispute a deliberate false alarm from the worker page (QR on the poster). | "A wrong alert becomes a permanent Rule CI regression." |
| 3:00 | End on the scorecard row that changed. | — |

If any live step stalls for more than 20 s: say so, switch to `/demo?demo=1` and continue the
story there. Do not wait on a spinner in front of the audience.

## Rehearsal checklist (run it 3 times; PLAN-9.5 §9.3)

Each rehearsal is one full run of the script above on the deployed stack. Record the numbers in
the table at the end; the `live_demo_rehearsed` check in `eval/checks.yaml` passes only with 3
rows inside the §2 targets.

**T-60 min, the stack**

- [ ] `scripts/deploy/runpod_status.sh`: API, SAM and Glimmer pods `RUNNING` and `ok`.
- [ ] `curl https://rulecam.vercel.app/rulecam-api/mode` → `{"mode":"live","badge":"LIVE"}`
      (if it says REPLAY or record, fix `.env` and restart before anything else).
- [ ] On the API pod: `uv run --no-sync python -m rulecam.replay.preflight` all green.
- [ ] `uv run rulecam scorecard --checks auto --api <public API URL>`: no new FAIL since the last
      run (`scene_text_firewall` stays FAIL until `HF_TOKEN` + the `ml` extra are installed).
- [ ] `/runtime/metrics`: `verifier.state` is `closed`, `response.actions_dry_run` is what you
      intend to show (keep `true` unless the interlock hardware is on the table).
- [ ] `/demo?demo=1` loads on the presenting laptop **and** on a phone (375 px), and the verify
      link shows "✓ Chain intact · 32 records".

**T-30 min, the scene**

- [ ] Phone fixed 2.5 to 3 m high, 30 to 45° down, landscape; a pallet or a 1 m floor square fully
      in view and flat (calibration). Same set-up as `eval/README.md` "Recording the owner's
      clips".
- [ ] Pair the phone (**Pair a phone** on `/planner` → Cameras, scan the QR; it calls `POST /pair/token`) and check `/cameras` shows a fresh chunk.
- [ ] Floor tape at 1 m and 3 m from the cart's path; the cart really moves (walking pace).
- [ ] Nobody filmed who has not agreed; no bathroom, bedroom or break room in view.
- [ ] Owner logged in on the presenting laptop (`/login`), or the laptop is the pod's loopback.

**During each rehearsal, note**

- [ ] Request typed → plan ready (s), and whether Rule CI was green.
- [ ] Event start (the pedestrian crosses 2 m) → verified alert on `/monitor` (s): target ≤ 10 s.
- [ ] `lag_s` p95 from `/runtime/metrics` during the run: target ≤ 6 s.
- [ ] Glimmer's verdict was decisive (not "unsure") and matched what happened.
- [ ] The dispute appeared as a regression (Rule CI) after it was upheld.

**After each rehearsal**

- [ ] `rulecam audit <today> demo`: chain intact and every decision reproduced.
- [ ] Re-approve the demo plan if an eval run replaced it (`eval/README.md`).

| # | Date | Plan ready (s) | Alert latency (s) | Lag p95 (s) | Glimmer decisive | Within §2? | Notes |
|---|---|---|---|---|---|---|---|
| 1 | | | | | | | |
| 2 | | | | | | | |
| 3 | | | | | | | |

## Fallbacks

| Problem | Do |
|---|---|
| Planner slow or a hosted model errors | Continue on `/demo?demo=1` (same story, recorded). |
| Glimmer pod down (`verifier.state` open) | Candidates go to the human queue with "verifier unavailable": show that, it is the designed behaviour. |
| Phone loses Wi-Fi | The camera card goes BLIND (never "compliant"); show it, then re-pair. |
| Vercel deploy rate-limited | The last deployed site still serves `/demo`; the API pod serves `/verify/?day=…` directly. |
