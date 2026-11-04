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
| WhatsApp voice note | dry-run only (no recipient configured, and no message is sent without the owner): "dry-run: would send the spoken warning (es+en) as a WhatsApp voice note to supervisor" chained; the bilingual note is 32.8 KB of Ogg/Opus (limit 512 KB) | – |
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

## Planning speed: before and after (02:20–02:46 UTC)

The lead's target: "Plan this rule" under about 90 s on stage, every check kept.

**Before.** The five forklift plans above took 291–424 s from request to plan. One of them
(`plan-d459ce9fa411`, 309.6 s), profiled from the API log:

| Step | Time | Where it went |
|---|---|---|
| survey | 224.9 s | Spark proposes 30 things (14.8 s); SAM 3.1 looks for each on 8 frames, 240 hosted calls 8 at a time (149 s); the MoGe-2 health check on the serverless GPU timed out twice (60 s) |
| probe | 56.5 s | Spark picks the concepts (5.5 s); 2 concepts x 19 frames, one SAM call after another |
| ask, compile | 7.5 s, 9.3 s | one Spark call each, one after the other |
| audit | 11.3 s | Glimmer back-translation (Spark Open answers 401 at once) |
| prove (Rule CI) | 0.1 s | 16 boundary traces and 6 mutants: nothing to win here |

**What changed** (commits 0f3192e, cbf5561, 0a96beb): all of a survey's SAM calls at once (up
to 32 in flight); each probe's frames at once, two probes at a time; a SAM call hung for 15 s
gets a twin; the MoGe health check runs beside the survey and is waited on for 5 s at most;
compile's Spark call starts with ask's; a camera's survey is reused for 30 minutes while its
scene hash is unchanged. The probe, the calibration check against people, the acceptance checks
and Rule CI run in full every time; Rule CI was green (16/16, 6/6) on all nine plans below.

**After.** Three benches, each: one phone camera fed the worker clip in a loop, the Spanish 4 m
rule planned three times in a row (`scripts/ops/plan_bench.py`; request to plan, as measured by the
client and by the saved run):

| Bench | Plan 1 (new camera) | Plan 2 | Plan 3 | What it showed |
|---|---|---|---|---|
| 1: parallel calls, reuse at 24 bits, no hedging | 160.2 s | 170.5 s (survey reused; one probe and one acceptance probe each waited ~40-60 s on a hung SAM call) | **70.4 s** (reused) | hung calls set the time |
| 2: hedging after 8 s | 139.5 s | 187.1 s | 76.2 s | the looping clip's scene hash moved 30-39 bits, so no reuse; 198 twins on a busy API only added load |
| 3: final (reuse within 48 bits, hedge after 15 s) | 122.8 s | **41.8 s** (reused) | **45.1 s** (reused) | 17 twins; survey 0.0 s, probe 14-16 s, ask 9-11 s, compile 1.7-1.9 s after ask, audit 17 s |

So on stage: plan once on the stage camera beforehand (any rule), and "Plan this rule" then
takes about 40-50 s on a warm GPU; a first plan on a new camera takes about 2 minutes (survey
53 s, probe 38 s on the final bench). The hosted SAM's latency still moves these numbers by
tens of seconds when the API is busy. The page shows each step's time as it finishes.

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
