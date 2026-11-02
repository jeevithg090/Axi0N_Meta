# Rulecam 9.5 scorecard, 2026-11-02

- live runs: /workspace/evals/out/caviar-corridor/runs/2026-11-02T212403Z against http://localhost:8000
- report: /workspace/evals/out/caviar-corridor/2026-11-02.json

Targets are PLAN-9.5 §2. A criterion without data is NOT MEASURED, never a pass.

## Areas

| Area | Target score | Result |
|---|---|---|
| Alert quality | 9.5 | FAIL |
| Real-time | 9.5 | FAIL |
| Cost | 9 | FAIL |
| Perception | 9.5 | FAIL |
| Planner | 9.5 | FAIL |
| Safety and privacy | 9.5 | NOT MEASURED |
| Deployment and ops | 9.5 | NOT MEASURED |
| Demo | 10 | NOT MEASURED |

## Criteria

| Area | Target | Measured | Result |
|---|---|---|---|
| Alert quality | Precision ≥ 0.92 per rule | n/a (no alerts yet) | NOT MEASURED |
| Alert quality | Recall ≥ 0.9 per rule | caviar_shop_enter 0.00 | FAIL |
| Alert quality | 0 false alerts on the negative set | n/a (no negative clips run) | NOT MEASURED |
| Alert quality | Glimmer decisive (not unsure) on ≥ 85% of candidates | n/a (0/0) | NOT MEASURED |
| Alert quality | Glimmer verdict matches the label on ≥ 90% (WS1a) | n/a (0/0 decisive) | NOT MEASURED |
| Real-time | p95 lag ≤ 6 s (2 cameras, 30 min) | 15.1 s over 536 samples (7 real-time eval runs) | FAIL |
| Real-time | p95 alert latency (event start → verified alert) ≤ 10 s | n/a over 0 matched alerts | NOT MEASURED |
| Real-time | Keyframe mode never triggered | triggered (7 real-time eval runs) | FAIL |
| Cost | ≤ $0.15 per camera-hour idle | n/a over 0 metered runs; GPU $2.68/h ÷ 1 camera, default = $2.680 per camera-hour | NOT MEASURED |
| Cost | ≤ $0.60 per camera-hour active | $2.680 over 7 metered runs; GPU $2.68/h ÷ 1 camera, default = $2.680 per camera-hour | FAIL |
| Cost | One GPU pod carries the cameras the cost assumes (soak at the real-time targets) | no sharing assumed (1 camera per GPU) | PASS |
| Cost | Budget alarm works | not checked (`budget_alarm`) | NOT MEASURED |
| Perception | Detection agreement with hosted SAM ≥ 0.95 | not checked (`sam_agreement`) | NOT MEASURED |
| Perception | ID switches ≤ 1 per minute per camera | worst 46.69/min over 7 labelled tracks | FAIL |
| Perception | HOTA ≥ 0.6 on our clips | worst 0.29 over 7 labelled tracks | FAIL |
| Planner | Same request → same canonical rule 20/20 | identical: 1/7 (per request; needs 20 runs) | FAIL |
| Planner | 100% activatable where the scene allows, honest refusals where it doesn't | 100% (7/7 runs as expected) | PASS |
| Safety and privacy | Owner login | not checked (`owner_login`) | NOT MEASURED |
| Safety and privacy | No public route exposes candidate traces | not checked (`evidence_routes_private`) | NOT MEASURED |
| Safety and privacy | Guardrail tests pass | not checked (`guardrail_tests`) | NOT MEASURED |
| Safety and privacy | Scene-text firewall live | not checked (`scene_text_firewall`) | NOT MEASURED |
| Deployment and ops | One command brings up the stack on a clean account | not checked (`one_command_deploy`) | NOT MEASURED |
| Deployment and ops | Survives a pod restart | not checked (`survives_restart`) | NOT MEASURED |
| Deployment and ops | Health checks and metrics on a dashboard | not checked (`metrics_dashboard`) | NOT MEASURED |
| Demo | Recorded golden REPLAY on the site | not checked (`golden_replay_live`) | NOT MEASURED |
| Demo | Scripted 3-minute live demo rehearsed 3 times | not checked (`live_demo_rehearsed`) | NOT MEASURED |

## Per rule

| Rule | Clips | TP | FP | FN | Precision | Recall | False alerts on negatives | Latency p95 |
|---|---|---|---|---|---|---|---|---|
| caviar_shop_enter | 7 | 0 | 0 | 19 | n/a | 0.00 | 0 | n/a |

## Per run

| Clip | Rule | Status | Outcome | Speed | Candidates | Alerts | Events | TP/FP/FN | Tier 1 decisive | Lag p95 | $/camera-hour |
|---|---|---|---|---|---|---|---|---|---|---|---|
| caviar_enter_exit_crossing_paths1cor | caviar_shop_enter | labelled | activated | 1x | 0 | 0 | 1 | 0/0/1 | 0/0 | 15.1 s | $2.680 |
| caviar_one_shop_one_wait1cor | caviar_shop_enter | labelled | activated | 1x | 0 | 0 | 3 | 0/0/3 | 0/0 | 15.1 s | $2.680 |
| caviar_one_stop_enter2cor | caviar_shop_enter | labelled | activated | 1x | 0 | 0 | 2 | 0/0/2 | 0/0 | 15.1 s | $2.680 |
| caviar_shop_assistant2cor | caviar_shop_enter | labelled | activated | 1x | 0 | 0 | 5 | 0/0/5 | 0/0 | 15.1 s | $2.680 |
| caviar_three_past_shop1cor | caviar_shop_enter | labelled | activated | 1x | 0 | 0 | 2 | 0/0/2 | 0/0 | 15.1 s | $2.680 |
| caviar_two_enter_shop1cor | caviar_shop_enter | labelled | activated | 1x | 0 | 0 | 1 | 0/0/1 | 0/0 | 15.1 s | $2.680 |
| caviar_walk_by_shop1cor | caviar_shop_enter | labelled | activated | 1x | 0 | 0 | 5 | 0/0/5 | 0/0 | 15.1 s | $2.680 |

Matching: temporal IoU ≥ 0.3; alerts closer than 3 s merge into one episode; episodes widened to ≥ 1 s. Latency and lag use real-time runs only.
