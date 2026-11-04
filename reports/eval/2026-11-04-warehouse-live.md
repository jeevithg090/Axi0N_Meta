# Warehouse forklift safety, live (4 Nov 2026, 00:17–01:40 UTC)

Every number below comes from a live run: the Rulecam API on branch `p1/uc-warehouse` running
on a laptop, against the live Meta models (Muse Spark 1.3, hosted SAM 3.1, Muse Voice
Transcribe, Muse Image on Meta's API; SAM 3.1 and Muse Glimmer 30B on our RunPod serverless
GPUs), fed real stock forklift video as paired phone cameras with `rulecam eval run`. No mocks,
no replay cache. The deployed API pod was not touched. Sources: the day's evidence chain, the
run records, the API log and the saved `/warehouse` results.

## In one table

| Step of the story | What happened live | Time |
|---|---|---|
| Spoken or typed request in Spanish | "Mantener a las personas a al menos 4 metros de una carretilla elevadora en movimiento." planned three times (runs 2, 4 aborted, 5) | 291–424 s request → plan (survey 120–239 s of it) |
| Plan with "can't check" items | Every forklift plan came back as a caveat in the planner's own words, e.g. "I can check this, with a caveat: camera 'phone-6' calibration error 0.15 m > 0.10 m" | – |
| Rule CI | boundary 16/16, mutations 6/6 on every forklift plan (6 plans) | 0.1–0.2 s |
| Live near miss | 9 candidates on 5 cameras: 3.28–4.00 m on the worker clip, 0.0 m on the driver clip | – |
| Glimmer verdict (RunPod GPU) | 4 of 4 confirmed `violation`, 0.98–0.99 | 12.5–21.7 s per verdict |
| Spanish warning | Spark wrote it, **Meta MMS-TTS** spoke it, Voice Transcribe heard it back: WER 0.09, 0.15, 0.18, all passed | 13.3–13.6 s |
| Bilingual warning | Spanish then English in one WAV (MMS-TTS both): Spanish heard back word for word (WER 0.00, passed); English "forklift" heard as "for Cliff" (WER 0.27, **flagged as failed**) | 16.5 s |
| Phone alert + ack | LAN alert to `safety_officer`; *On my way* tapped on `/usecases/warehouse`; ack chained | – |
| Evidence | chain intact (287 records), incident report and verify page links | – |
| OSHA refresher duty | clause found by Spark web search; on the 3.28 m near miss Spark read the forklift's painted unit **"K 35"** ("white marking on side panel, frames 2–3"), said the clause applies; marked done, `refresher_training_done` chained | 17.5–31.8 s |
| Toolbox talk | 2-minute Spanish talk from the records and 3 evidence frames, with a Muse Image drawing | 32.3–38.7 s |
| Worker dispute | worker page dispute on the 3.996 m alert: **upheld by Muse Spark 1.3** looking at 4 frames; `false_alarm` label and a permanent Rule CI regression stored. Spark read the strip's ruler, which was wrong: fixed (finding 3) | 28.3 s |
| Voice near-miss report | spoken Spanish report matched to the 3.28 m near miss (reported "about three metres"); proposed rule planned: Rule CI 16/16, 6/6 | 21.3 s report; 163 s plan |
| Shift report | 9 near misses, 9 alerts, 4 to a person, 1 ack, 1 dispute upheld, closest 0.0 m (the driver clip) | 7.2–8.3 s |

## Runs

| Run | Clip (Pexels) | Request | GPUs | Candidates → outcome |
|---|---|---|---|---|
| 1 (00:17) | 32243654 worker passes | EN 4 m | none: RTX 4090 pool empty | 2 candidates (3.45, 3.99 m); Glimmer timed out (no worker) → HIGH rule alerted first and queued both for a person |
| 2 (00:30) | 32243654 | ES 4 m | Glimmer on Routeway (free tier) | 2 candidates; 1 confirmed `violation` 0.92 (59.8 s), 1 timed out → queued |
| 3 (00:49) | 32243653 driver only | EN 4 m | Routeway Glimmer | 1 candidate on the seated driver (0.0 m); Glimmer timed out → alerted first, queued for a person. In T04 (2 Nov) Glimmer rejected this driver; here it never answered |
| 5 (01:07) | 32243654 | ES 4 m | RunPod SAM + Glimmer warm | 2 candidates (3.28, 3.996 m), both `violation` 0.99 (17.1 s, 12.5 s) |
| 6 (01:22) | 32243654 | EN 4 m | RunPod | 2 candidates (3.45, 3.99 m), `violation` 0.98 / 0.99 (21.7 s, 14.0 s); phone alerts al-1, al-2 |
| 7 (01:42) | 32243654 | EN 4 m, after the ruler fix | RunPod | 2 candidates (3.22, 3.99 m), `violation` 0.98 / 0.99; strip rulers 3.1–3.3 m and 3.9–4.2 m |

Run 4 was stopped during its survey when the RunPod GPUs came back, to re-run on them.

## Findings

1. **GPU capacity decided the first hour.** From 00:17 to 01:05 the RTX 4090 pool had no
   worker for either endpoint (six `/ping`s timed out at 200–300 s; RunPod reported 4090, L40S,
   L40, A6000 and L4 out of stock at CUDA 12.8). The system degraded as designed: SAM fell back
   to hosted SAM 3.1, Glimmer failures became `verifier_error`, the HIGH rule still alerted first
   and sent every unverified candidate to a person. Never a silent pass.
2. **Latency is the serverless SAM timeout, not the models.** After every approval the first
   runtime `/segment_image` calls to the serverless SAM waited 2 × 60 s before falling back to
   hosted SAM, so candidates reached the chain about **123 s** after the event (5 of 5 chunks
   timed, segment 122–124 s; once warm, 1.8 s per chunk). Glimmer itself took 12.5–21.7 s. The
   1 Nov campaign, on a dedicated GPU pod, had 1.2–5.7 s. Recommendation for the ops agent: fail
   over to hosted SAM after the first timeout per chunk instead of retrying.
3. **A worker dispute exposed a ruler bug, now fixed.** Spark upheld the dispute on the 3.996 m
   alert: "the annotation legend in the frames reports the yellow ruler distance as 5.1 m then
   4.9 m … all > 4 m … The IR measured value of 3.996 m is borderline by 4 mm". The strip's ruler
   measured from the person's foot to the middle of the forklift's bottom edge, while the rule
   measures to the nearest point of its footprint: about 1 m apart on a wide forklift. The ruler
   now ends at the footprint point the rule used (`runtime/strips.py`, e7a483d). Run 7 confirms
   it: for a 3.989 m near miss the strip's ruler reads 3.9–4.2 m across the keyframes (it read
   4.9–5.1 m for the same pass before), and Glimmer confirmed it at 0.99. The upheld dispute and
   its regression stay on the chain as they happened; the calibration's stated error on that run
   (±0.50 m) also covered the 4 mm margin.
4. **Spark's OSHA judgement varies at the margin.** On 3.99 m near misses it said "due" three
   times and once "not required: a marginal 4-m separation breach rather than an accident or
   imminent-collision near miss"; on the 3.28 m one it said due. The model decides; a person marks
   the training done.
5. **The worker page had no rules for planner-made forklift rules.** It listed only rules with a
   zone on the camera, and the planner's distance rules have none: `/worker/{plan}/{camera}`
   answered 404, so no worker could dispute. Fixed (`worker/page.py` uses the runtime's camera
   scope) and tested on the recorded T02 plan.
6. **Without Spark Open every dispute stayed pending.** Muse Spark 1.3 now reviews them with
   the frames when no Tier 2 exists (`worker/spark_review.py`); the upheld dispute above is the
   first one decided by a model live.
7. **Live `speak` needs a speaker.** With dry-run off and no speaker or phrase book configured,
   the rule's own "play the warning (es)" action fails (`ActionError`); the Spark-written,
   MMS-TTS-spoken WAV on the page is what the demo plays.
8. **The coverage watchdog flaps on stock clips.** Wired to chain BLIND/OK changes (lead's
   request), it read the moving stock camera as `moved` and wrote 222 records in an hour; it now
   ships off by default (`RULECAM_COVERAGE_LOG`) and starts from live chunks only.
9. **Voice report matching is careful.** With only the driver-clip candidate (0.0 m) in the
   window, Spark refused to match a "three metres" report ("separation 0.0 m versus about 3 m
   reported … not clearly the same incident"); with the worker-clip near misses present it matched
   the 3.28 m one.
10. **A proposed rule is planned honestly.** The worker's "4 m in the main aisle" became
    `distance_m < 4 ∧ moving` with Rule CI green, and the planner said it was "not probed" on any
    camera (no recent frames at that moment) and had no zone for "the main aisle".

## Goldens

T02 (`worker_passes_4m`, verified alert, Glimmer 0.99) and T04 (`driver_only_4m`, driver
rejected) were recorded live on 2 Nov on `p1/addon-integration` and never reached the final
integration branch; they are now on `p1/uc-warehouse` with their gates. `make golden-verify`:
5/5 steps reproduced for each; `make golden-gate`: T02 alerts 2 (`violation`, `violation`), T04
alerts 0 (`no_violation` × 3), `warehouse_driver` alerts 0.

## Setup

| Part | What ran |
|---|---|
| API | local, branch `p1/uc-warehouse`, prototype mode, actions dry-run in runs 1–2, live from run 3 (LAN alerts only: no plug, speaker, Telegram or WhatsApp configured) |
| Planner | Muse Spark 1.3 and hosted SAM 3.1 (Meta API) |
| Runtime | SAM 3.1 serverless (RunPod `imz4qu1vzwp4z6`) with hosted fallback, ByteTrack |
| Verifier | Glimmer 30B: RunPod `z000o8uch2wj4i` (runs 1, 5, 6), Routeway hosted free tier (runs 2–3) |
| Strips | 8 frames at 960 px, raw (prototype mode) |
| Speech | Meta MMS-TTS on CPU (`transformers`), macOS system voice before it was installed (labelled) |
| Feed | 3 s chunks at 10 fps, 20 s survey, clip once at 1x after approval |

Not measured: precision or recall (the clips are not hand-labelled), the deployed pod, the T02
alert latency on a dedicated GPU, a real speaker or plug.
