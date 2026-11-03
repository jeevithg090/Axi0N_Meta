# Crowd watch, live run on Jülich bottleneck footage (3 Nov 2026)

Build `4ebcc9d` (PR #144) on the API pod, `RULECAM_CROWD_EVERY_S=5`, `RULECAM_CROWD_ASSESS_EVERY_S=10`.

## Setup

- Clip: `small-030_c_56_h0.mp4` (Jülich bottleneck experiment, overhead fisheye, 960×720, 64.8 s, people in orange caps).
- Fed in real time as a paired phone camera (`phone-102`, 22 chunks of ~3 s) with `/workspace/crowd/live_run.sh`.
- Watch: a Hindi instruction ("concert hall entrance seen from above; people push toward the narrow gate on the right; warn us before the crowd at the gate becomes dangerous, tell stewards what to do, announce calmly in Hindi and English"), role `stewards`, four operator floor points (4 × 4 m), zones `gate approach (2 x 2 m)` and `entry corridor`, concept `head`, grid 1.
- SAM: Meta hosted SAM 3.1 (fallback; the serverless RTX 4090 endpoint had no capacity, see below). Spark: Meta hosted. Glimmer: serverless endpoint, no worker could start.

## Readings (every 6 s)

| Time (UTC) | People in view | Gate approach people | Gate density (people/m²) | Gate pressure (/s²) | Gate speed (m/s) |
|---|---|---|---|---|---|
| 16:41:04 | 77 | 16 | 4.0 | 0.137 | 0.22 |
| 16:41:10 | 69 | 33 | 8.2 | 0.094 | 0.16 |
| 16:41:16 | 66 | 32 | 8.0 | 0.085 | 0.11 |
| 16:41:22 | 60 | 28 | 7.0 | 0.073 | 0.10 |
| 16:41:28 | 56 | 29 | 7.2 | 0.034 | 0.06 |
| 16:41:34 | 49 | 27 | 6.7 | 0.044 | 0.15 |
| 16:41:40 | 42 | 22 | 5.5 | 0.084 | 0.17 |
| 16:41:46 | 34 | 19 | 4.7 | 0.040 | 0.09 |
| 16:41:52 | 29 | 14 | 3.5 | 0.030 | 0.10 |
| 16:41:58 | 20 | 7 | 1.8 | 0.014 | 0.09 |
| 16:42:04 | 14 | 2 | 0.5 | 0.003 | 0.14 |

The count follows the run: the queue builds at the gate, then drains. A by-eye check of two frames earlier the same day matched the head count (about 33 caps seen vs 33 counted; about 55 vs 53–72 on denser frames with tiling). No ground-truth trajectories were on the pod, so densities are not scored. The 8 people/m² peak in a 2 × 2 m zone is probably high: heads stand about 1.7 m above the floor plane the homography maps, so on this fisheye view heads near the gate may project into the zone from outside it.

## What the warden did

1. 16:41:04 reading → Spark assessment at 16:41:20 (9.9 s): `warning`, alert. Why: gate approach 4.0 people/m², pressure 0.137 /s² above the 0.02 turbulence onset. Staff actions: hold entry inflow, open another route, send stewards to the gate front. Announcements in Hindi and English.
2. Glimmer check: `crowd_check_failed` at 16:43:20 (ReadTimeout after the client's 120 s timeout; no Glimmer worker).
3. Alert `crowd-9b1657daaaf0` sent at 16:43:39 without a check (by design: an unreachable verifier does not hold a safety warning): MMS-TTS WAVs for both languages, notifier id `al-1`, chained as candidate + actions (`chain_hash bd9fc13c…`).
4. Next assessment only at 16:43:46 on the 16:42:04 reading (`normal`, 6.8 s). The peak (8.2/m² at 16:41:10) was never assessed: one assessment runs at a time and the stuck check held the slot.

## Findings

- **Works end to end:** plain-language Hindi instruction → SAM count per zone → Spark level, actions and bilingual announcements → speech, notifier, evidence chain.
- **Alert latency 2 min 35 s** from reading to alert when Glimmer is down: the check waits out the 120 s transport timeout, and the warden cannot assess while it waits. Fix: give the crowd check a short timeout (or send first and verify after), and let assessments continue while a check runs.
- **No Glimmer verification in this run:** RTX 4090 stock was out across every CUDA version (RunPod capacity read at 16:25 UTC); `warm.sh on` got no worker on either endpoint in 9 min. Re-run when capacity returns to cover the verify/hold path live.
- **Earlier run (14:27 UTC, phone-101)** reached `critical` (6.0/m²), but the API was restarted for the final deploy while its Glimmer check was in flight, so that alert was lost.
- **Ops:** the API pod's 20 GB volume hit its quota during the run (`Disk quota exceeded`); four superseded crowd releases were removed (~800 MB). `live_run.sh` needs absolute paths (it changes into `/workspace/rulecam` before writing the feed log).
