# Crowd watch, live run on Jülich bottleneck footage (3 Nov 2026)

Build `4ebcc9d` (PR #144) on the API pod, `RULECAM_CROWD_EVERY_S=5`, `RULECAM_CROWD_ASSESS_EVERY_S=10`.

## Setup

- Clip: `small-030_c_56_h0.mp4` (Jülich bottleneck experiment, overhead fisheye, 960×720, 64.8 s, people in orange caps).
- Fed in real time as a paired phone camera (`phone-102`, 22 chunks of ~3 s) with `/workspace/crowd/live_run.sh`.
- Watch: a Hindi instruction ("concert hall entrance seen from above; people push toward the narrow gate on the right; warn us before the crowd at the gate becomes dangerous, tell stewards what to do, announce calmly in Hindi and English"), role `stewards`, four operator floor points (4 × 4 m), zones `gate approach (2 x 2 m)` and `entry corridor`, concept `head`, grid 1.
- SAM: Meta hosted SAM 3.1 (fallback; the serverless RTX 4090 endpoint had no capacity, see below). Spark: Meta hosted. Glimmer: serverless endpoint, no worker could start.

## Readings against ground truth (every 6 s)

The run has published trajectories (Jülich data archive, `2018crowdqueue`, run `030_c_56_h0`: 75
participants, 5.6 m corridor, head positions at 25 fps). Trajectory frames 0–1619 cover the 64.8 s
clip exactly, and each reading is placed on the clip through the chunk index (`start_ts`, 3 s per
chunk). Pixels were mapped to metres by fitting a homography between SAM's `orange cap` detections
and the trajectories at 7 frames (iterative closest point, RANSAC): 315 matched heads, median
residual 2.3 cm, and drawn back on a frame every trajectory sits on a cap. Each zone polygon goes
through that map; "true" is the number of tracked heads inside it. Scoring script and output:
`/workspace/crowd/score_juelich.py`, `score030.json` on the API pod.

Gate approach (2 × 2 m zone):

| Clip t | System people | True people | System density (/m²) | True density (/m²) |
|---|---|---|---|---|
| 1 s | 16 | 15 | 4.0 | 3.8 |
| 7 s | 33 | 36 | 8.2 | 9.0 |
| 13 s | 32 | 32 | 8.0 | 8.0 |
| 19 s | 28 | 30 | 7.0 | 7.5 |
| 25 s | 29 | 31 | 7.2 | 7.8 |
| 31 s | 27 | 27 | 6.7 | 6.8 |
| 37 s | 22 | 22 | 5.5 | 5.5 |
| 43 s | 19 | 18 | 4.7 | 4.5 |
| 49 s | 14 | 13 | 3.5 | 3.3 |
| 55 s | 7 | 6 | 1.8 | 1.5 |
| 61 s | 2 | 2 | 0.5 | 0.5 |

| Zone | Readings | Count (system ÷ truth) | Mean count error | Density (system ÷ truth, median) |
|---|---|---|---|---|
| Gate approach, live, `head` | 11 | 0.99 | 1.0 people | 1.00 |
| Entry corridor, live, `head` | 11 | 0.99 | 1.4 people | 0.78 |
| Gate 2 × 2 m, offline every 2 s, `head` | 32 | 0.95 | 1.7 people | 1.00 |
| Gate 2 × 2 m, offline every 2 s, `orange cap` | 32 | 0.97 | 1.3 people | 1.00 |
| Corridor, offline every 2 s, `orange cap` | 32 | 0.99 | 0.7 people | 0.78 |

- **Counts are right**, including at 9 people/m²: SAM 3.1 on an overhead view found 95–99% of the
  heads in the zones. "View" counts run 8–15% above the participants because SAM also counts staff
  and bystanders, who are not in the trajectories.
- **Gate densities are right** (within about 10%). The real peak in front of the gate was 9.5
  people/m² at 4 s, held above 7 for about 30 s, between the 6 s readings; the highest reading was
  8.2 at 7 s (truth 9.0).
- **Corridor densities are 22% low**: its polygon reaches well past the four floor points (a
  4 × 4 m square at the gate), so the four-point homography extrapolates its area as 37.5 m²
  against 29.1 m² true. Zones should stay inside the marked floor points, or more points should be
  marked.
- **The floor points matter:** these were fitted to head positions, which is why the gate area
  comes out right. Points clicked on the floor would be off for heads by the head-height parallax
  (the trajectories are on a head plane 1.76 m above the floor), so a site calibration needs the
  counted plane's height.

## What the warden did

1. 16:41:04 reading → Spark assessment at 16:41:20 (9.9 s): `warning`, alert. Why: gate approach 4.0 people/m², pressure 0.137 /s² above the 0.02 turbulence onset. Staff actions: hold entry inflow, open another route, send stewards to the gate front. Announcements in Hindi and English.
2. Glimmer check: `crowd_check_failed` at 16:43:20 (ReadTimeout after the client's 120 s timeout; no Glimmer worker).
3. Alert `crowd-9b1657daaaf0` sent at 16:43:39 without a check (by design: an unreachable verifier does not hold a safety warning): MMS-TTS WAVs for both languages, notifier id `al-1`, chained as candidate + actions (`chain_hash bd9fc13c…`).
4. Next assessment only at 16:43:46 on the 16:42:04 reading (`normal`, 6.8 s). The peak (8.2/m² at 16:41:10) was never assessed: one assessment runs at a time and the stuck check held the slot.

## After the fix (build `559f976`, 18:10 UTC, same clip as `phone-103`)

`559f976` gives Glimmer 20 s (`RULECAM_CROWD_CHECK_TIMEOUT_S`), sends the alert unchecked past that
with `check_note`, delivers alerts on their own threads so assessments keep running, and notifies
staff before synthesising speech. Glimmer still had no GPU.

| Reading (clip t) | Gate density | Spark (done at) | Alert sent | Reading → alert |
|---|---|---|---|---|
| 18:10:35 (1 s) | 4.0 | `warning` (18:10:54, 11.3 s) | 18:11:18, unchecked after 20 s | 43 s |
| 18:10:48 (13 s) | 8.0 | `critical` (18:11:08, 12.8 s) | 18:11:30, unchecked after 20 s | 41 s |
| 18:11:06 (31 s) | 6.7 | `critical` (18:11:19) | none: a critical alert was in flight | |
| 18:11:30 (55 s) | 1.8 | `normal` (18:11:45) | | |

- The peak is now assessed and escalated: `critical` at 8.0/m² (truth 8.0), "well into the 6-7
  crush range", with stop-inflow and close-gate actions and Hindi and English announcements; the
  run before it never assessed this reading.
- Reading to alert fell from 2 min 35 s to about 40 s: Spark about 12 s, Glimmer's 20 s, notify
  and speech 2-4 s, plus the time the reading waits for its turn.
- No duplicate: the 18:11:06 `critical` came while the first `critical` was in flight and was not
  sent again.
- Both alerts: notifier ids `al-1`, `al-2`, two WAVs each, chained (`7bb908f8…`, `b086a13c…`).
  Glimmer's calls ended in ReadTimeout at 18:12:55 and 18:13:09, so no late verdict was chained.

## Findings

- **Works end to end, and the measurements are right:** plain-language Hindi instruction → SAM count per zone (95–99% of the true heads) → density per zone (within about 10% at the gate) → Spark level, actions and bilingual announcements → speech, notifier, evidence chain.
- **The warning was right but too mild once the crowd peaked:** Spark warned at the first reading (truth 3.8/m², rising), but the crowd then stood at 7–9.5/m² for about 30 s, and that reading was never assessed (next finding). The 14:27 run, which did assess a dense reading, said `critical`.
- **Alert latency 2 min 35 s** from reading to alert when Glimmer is down: the check waited out the 120 s transport timeout, and the warden could not assess while it waited. Fixed in `559f976` (about 40 s, above).
- **No Glimmer verification in this run:** RTX 4090 stock was out across every CUDA version (RunPod capacity read at 16:25 UTC); `warm.sh on` got no worker on either endpoint in 9 min. Re-run when capacity returns to cover the verify/hold path live.
- **Earlier run (14:27 UTC, phone-101)** reached `critical` (6.0/m²), but the API was restarted for the final deploy while its Glimmer check was in flight, so that alert was lost.
- **Ops:** the API pod's 20 GB volume hit its quota during the run (`Disk quota exceeded`); four superseded crowd releases were removed (~800 MB). `live_run.sh` needs absolute paths (it changes into `/workspace/rulecam` before writing the feed log).
