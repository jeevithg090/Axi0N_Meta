# Chain Mode, kitchen-basics demo run (4 Nov 2026)

Build: branch `p1/final-integration`. Command: `uv run rulecam chain demo chains/kitchen-basics`
(about 4 s on a laptop, no models, no network). The same report is served by the owner-only
`GET /chain/packs/kitchen-basics` route on the API pod (see "Live API" below).

## What is real and what is SIMULATED

| Part | Source | Real or SIMULATED |
|---|---|---|
| Pack proof (lint and Rule CI at 7 knob settings) | `pack.yaml`, the real planner lint and Rule CI engine | Real computation on the pack's reference zones |
| Store fits (ready, needs setup, blocked) | `stores/*.yaml`: four **SAMPLE** store setups (Main St, Harbour Rd, Station Sq, Airport are made up) | Real computation on SAMPLE inputs |
| Scorecard percentages and 95% intervals | `simulation.yaml` (seed 14, 12 hours, 6 outcomes per hour) | **SIMULATED** |
| Station Sq kitchen camera BLIND for 3.6 h ("moved") | `simulation.yaml` | **SIMULATED** |
| Fix-first items 2, 5 and 6 (camera coverage, Harbour Rd at 62%) | built from the simulated scorecard | **SIMULATED** |
| Fix-first items 1, 3 and 4 (missing calibration, private-space block, no alert channel) | the store fits | Real computation on SAMPLE inputs |

No real store has run this pack. The scorecard's `provenance` field is `simulated` and the
report prints "SIMULATED" next to every number drawn from it.

## Result

1. Pack proof: **releasable**. 3 rules, Rule CI green at 7 settings: hand wash before the line
   (default, `wash_seconds=40`, `wash_window=120`), fire exit clear (default,
   `blocked_seconds=30`), delivery to cold storage (default, `dock_seconds=300`). Boundary and
   mutation scores per store: hand wash 13/13 and 5/5, fire exit 7/7 and 4/4, delivery 6/6 and
   4/4.
2. Store fits (SAMPLE stores): Main St 3/3 ready; Harbour Rd 3/3 ready with two refused
   overrides (`wash_seconds=10s` below HQ's 20-40 s range, and lowering fire-exit severity);
   Station Sq 2/3 (no alert channel for `kitchen_lead`); Airport 1/3 (back-cam has no ground
   calibration for the egress rule; the cold-storage rule is **blocked** because a zone is named
   `staff_break_room`, a private space that can never be watched).
3. Scorecard (**SIMULATED**, 2 Nov 08:00-20:00 UTC): chain hand wash 85%, Harbour Rd 62%
   (95% interval 51-73%, flagged below the chain), Station Sq BLIND on two rules (its camera
   watched 69% of the window), every other cell 88-96%.
4. Fix first: six items, two HIGH (Airport fire exit needs a calibration; Station Sq camera
   moved, SIMULATED).

## Live API

After the deploy of `0b35805` (see "Deploy" in `reports/eval/2026-11-04-muse-code-live.md`),
called as the owner from the pod's loopback (the public URL answers 403, owner-only):

- `GET /chain/packs`: one pack, `kitchen-basics` v3, 3 templates, 4 stores, `"simulated": true`.
- `GET /chain/packs/kitchen-basics`: HTTP 200 in 6.4 s; `releasable: true`; ready rules per
  store 3, 3, 2, 1 (the same as the CLI); scorecard `provenance: simulated`.

`POST /chain/prove` and `POST /chain/fit` (pack and store YAML in the body) are covered by
`tests/chain/test_api.py`; they were not called on the pod.
