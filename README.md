# Rulecam

[![ci](https://github.com/jeevithg090/Axi0N_Meta/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/jeevithg090/Axi0N_Meta/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](.python-version)

**Tell your cameras what worries you, in plain words and in your own language. Rulecam says
honestly what a camera can and can't check, proves each rule before it goes live, has a second
Meta model look at every alert before anyone is bothered, and keeps evidence that anyone can
check.**

*Describe it. Plan it. Prove it. Trust it.* Works with a phone or the CCTV you already have.
Built during the Meta Global AI Developer Hackathon (Oct 26 – Nov 4 2026).

![Same forklift rule on two clips. Left: Muse Glimmer confirms a worker inside the 4 m zone (0.99). Right: Glimmer rejects the seated driver the rule engine flagged (0.05).](docs/pitch/media/hero-same-rule.jpg)

*Same rule, same model, two clips. The rule engine flagged both. Muse Glimmer, Meta's
open-weight model, then running on our own GPU, looked at the frames, confirmed one and rejected
the other. Frames from recorded live runs ([T02 and T04 goldens](scenarios/README.md); the same
verdicts in the [1 Nov live campaign](reports/eval/2026-11-01-live-test-campaign.md)). Since
4 Nov the live site runs this check on Muse Spark 1.3 through the Meta API.*

## Try it now

Live site: **https://rulecam-live.vercel.app**. The site runs on Vercel. During the hackathon the
Rulecam API ran on the team's Mac behind a Cloudflare tunnel, with every model call going to the
Meta API. **Since the hackathon ended (4 Nov 2026) that API is switched off**, so the hosted
site's live pages have no backend. The replay and the verify page below still work, because
they need no API. To run the whole system yourself, see
[Run it on your own machine](#run-it-on-your-own-machine).

| What | Link | What you will see |
|---|---|---|
| The 3-minute video | [`docs/video/script.md`](docs/video/script.md) · [`captions.srt`](docs/video/captions.srt) | The three worlds below, live and recorded, each labelled |
| Replay, no login, no keys | `https://rulecam-live.vercel.app/demo?demo=1` | A recorded live run, step by step: request, plan, Rule CI, runtime, evidence. The header says **REPLAY**. It makes no model calls |
| Check the evidence yourself | `https://rulecam-live.vercel.app/verify/index.html?chain=/golden/warehouse_driver/chain.jsonl&root=6fe7daeb1f2543aef6a3d01d27f3a7793c4516520be41433ba6ee0871f87082e&n=35` | Your browser recomputes every SHA-256 of the 35-record evidence log and checks it against the day's Merkle root. Nothing is trusted from us |
| The three worlds | `https://rulecam-live.vercel.app/usecases/warehouse`, `/usecases/crowd`, `/usecases/supermarket` | Each world's story, step by step, read from what Rulecam recorded. The crowd page shows recorded runs only: crowd is not live now |
| Staff phone app, glasses mode | `https://rulecam-live.vercel.app/staff`, `https://rulecam-live.vercel.app/glasses` | Open them on a phone |
| Run it on your laptop | `./start_rulecam.sh` (macOS/Linux) or `./start_rulecam.ps1` (Windows), then `localhost:3000/demo?demo=1` | The API and the site on your machine; see [Run it on your own machine](#run-it-on-your-own-machine) |

![The Rulecam landing page as captured before 4 Nov: the recorded T02 run's verified alert, with the measured distance, Glimmer's verdict and the state of the GPUs we ran then.](docs/pitch/media/app-landing.jpg)

*The landing page, captured before 4 Nov: the recorded T02 run's verified alert, labelled as
recorded, and the state of the stack we ran then. Since 4 Nov the status band names the Meta API
models instead of GPU states.*

## Live now: Meta API only (since 4 Nov 2026)

On 4 Nov, at about 05:50 UTC, we moved every model call to the Meta API. We no longer run
RunPod or any GPUs of our own. Until the hackathon ended, the API and the site ran on the
team's Mac, public through a Cloudflare tunnel ([`docs/deploy.md`](docs/deploy.md), "Meta API
only"). That API is off now; run it yourself with `./start_rulecam.sh`.

- **Runs now:** Muse Spark 1.3 (planner, Ask, the planner's guardrail pass, retail checks, and
  the Tier 1 check of every alert), Muse Wonder 1.0 (Tier 2: dispute review, plan audit, Ask
  answer review), SAM 3.1 hosted by Meta, Muse Voice Transcribe, Muse Image, and Muse Spark 1.2
  where the code uses it (video with narration). Muse Code runs as the local `muse` CLI.
  MMS-TTS voices run on the CPU.
- **Ran before, not now:** Muse Glimmer 30B and Muse Spark Open on our own RunPod GPUs, and
  Llama Guard 4. The planner's own guardrails still decline banned requests (a fixed pattern
  check, then Muse Spark). Prompt Guard 2 is not loaded: the firewall shows "degraded", and
  candidates with text in the scene go to a person.
- **Results from 1–4 Nov** were measured on that earlier stack, with Glimmer on our own GPUs
  and Spark Open on 8 x H100. We keep them and say so wherever we quote them. What we measured
  on the Meta API alone is under [Results](#results).

## One engine, three worlds

One set of parts serves all three: **SAM 3.1** sees and counts, **Muse Spark** plans, reasons
and checks each alert, **Muse Wonder** reviews, and a **hash-chained log** keeps the proof.
Until 4 Nov, **Muse Glimmer** on our own GPUs did the alert check. Each world starts from a
sentence the owner says or types, in their own language.

| World | The owner says | What Rulecam does | Proof |
|---|---|---|---|
| **Warehouse** | "Mantener a las personas a al menos 4 metros de una carretilla elevadora en movimiento." | Plans the rule and says what it can't check. Rule CI attacks it. A second model checks each near miss. Spark writes the warning in Spanish, Meta's MMS-TTS speaks it, and Voice Transcribe listens back to check it was said right. It flags the OSHA refresher training a near miss triggers. A worker can dispute an alert, or report a near miss by voice | **Live now, Meta API only:** the Spanish rule planned in **146 s**, passed Rule CI (**16 of 16** edge cases, **6 of 6** mutants), was approved with two amber caveats ([Results](#results)) and runs on a looped stock clip, with Tier 1 verdicts from Muse Spark 1.3. **Earlier, with Glimmer on our own GPU:** Glimmer confirmed **6 of 6** near misses at **0.98–0.99**; the Spanish warning was heard back with word error **0.09–0.18**, all passed; with the camera already surveyed, a plan took **42–45 s** (**123 s** on a new camera) ([4 Nov](reports/eval/2026-11-04-warehouse-live.md)). The seated driver was rejected at **0.05** ([1 Nov](reports/eval/2026-11-01-live-test-campaign.md)) |
| **Crowd crush early warning** | In Hindi: warn us before the crowd at the main gate turns dangerous; if it fills, send the corridor to side door B | SAM 3.1 counts heads in each zone and turns them into people per m². Spark judges the level, names the crowd's phase and writes steward actions and calm announcements in Hindi and English. Before sending people anywhere, a route guard checks that place is not filling too. A second model checks the frames. A late check never holds back a warning | **Not live now:** the Jülich clips were only on the server we deleted. **Recorded 4 Nov, with Glimmer on our own GPU:** on real footage of the Jülich bottleneck experiment, counts were within **0–4%** of the published truth in every zone, up to 8–9 people per m². With Glimmer warm: confirmed at **0.95** in **6.2 s**, stewards told **26 s** after the reading. Listening back caught a Hindi announcement that came out as "crowd" instead of "gate", so the page did not play it ([report](reports/eval/2026-11-04-crowd-live.md), [chart](docs/pitch/media/crowd-density-vs-truth.png)) |
| **Supermarket** | "When was the checkout queue longest, how many tills were open, … do we need another till?" | Spark picks its own tools: SQL over SAM 3.1 tracks, counts, a model looking at frames, Spark watching the clip. It finds shelf gaps and spills, opens a task for staff, and closes it only when a photo proves the fix. It answers spoken questions in the asker's language | **Live now, Meta API only:** an Ask answer took **57.4 s** (Muse Spark 1.3, SAM 3.1, Muse Wonder review). **Recorded 4 Nov, with Glimmer on our own GPU:** on a busy frame, the tracker said **22** people. Counting the same frame three ways (SAM 3.1, Spark pointing, Glimmer) gave **10**, against about **12** counted by hand. A spill was seen and cleared in **1.9 min**, checked by photo (the spill image is a generated SAMPLE). A spoken Spanish question was heard word for word and answered in Spanish in **45 s** ([report](reports/eval/2026-11-04-supermarket-live.md)) |

Each number is the one we measured, including the weak ones. The limits are
[below](#honest-limits).

## The platform around them

Measured live on 4 Nov, before the move to the Meta API (Glimmer ran on our own GPUs then).

| Piece | What it does | Measured (live, 4 Nov) |
|---|---|---|
| **Connect any camera** | RTSP (H.264, H.265, with a password), HLS, MJPEG, public live streams, uploaded clips and ONVIF discovery. Spark names each camera and says which world it fits | From pressing Connect to a named, surveyed camera: **23–33 s** in every case. No real CCTV device was available: tested against streaming servers and a simulated ONVIF recorder ([report](reports/eval/2026-11-04-connectors.md)) |
| **Staff app** | One feed per role, never per name, in English, Spanish or Hindi. Acknowledge, On my way, Done with a photo, or False alarm. Every tap goes into the chain | A Glimmer-verified alert (0.95) reached Google's push service **8.9 s** after the camera chunk, with the app closed (tested in Chromium, not on a real phone) ([report](reports/eval/2026-11-04-staff-app.md)) |
| **Proof portal** | An inspector or insurer gets a share link to a proof pack. Their browser re-checks every record and every number | **395** records and **97** numbers re-checked in the browser in **0.3–0.4 s**. One edited number turned the page red ([report](reports/eval/2026-11-04-proof-portal.md)) |
| **Glasses mode** | Ask out loud what you are looking at. A model looks (Glimmer then, Muse Spark 1.3 now), SAM 3.1 counts, Spark answers, MMS-TTS speaks | First spoken sentence ready in a median **8.6 s**. Stand-in stills and a phone page: not yet run on real glasses ([report](reports/eval/2026-11-04-glasses.md)) |
| **Muse Code rule types** | For a rule the engine can't express, Muse Code writes the check. Rulecam tests it and the Z3 solver proves it | "Alert if a pallet is stacked above the yellow line": Z3 proved **4 of 4** properties, mutation testing caught **25 of 26** broken copies, the owner approved it on the live API, and it ran on a real clip ([report](reports/eval/2026-11-04-muse-code-live.md)) |
| **Chain Mode** | One rule pack for many stores, proved at every allowed setting, with a store-level scorecard (never per worker) | The pack's Rule CI is real; the store scorecard is **SIMULATED** and labelled so ([report](reports/eval/2026-11-04-chain-mode-demo.md)) |

## Why trust it

Other products already let you search or set camera alerts in plain words (Verkada, Hakimo,
Spot AI, Brivo, Conntour). Rulecam's difference is **proof**:

1. **It says what it can't see.** Before a rule goes live, the planner probes the camera and
   reports what it can and can't check. Asked to keep people away from a forklift on a kitchen
   camera, it said "I can't see the forklift reliably (lower bound 0.00)"
   ([T08](reports/eval/2026-11-01-live-test-campaign.md)).
2. **Rule CI proves each rule first.** Like tests for code: it attacks the rule with edge
   cases and broken copies of itself (mutants). Every forklift plan on 4 Nov passed **16 of 16**
   edge cases and caught **6 of 6** mutants.
3. **Every alert is checked, and checks are checked.** A second model looks at the frames
   before anyone is told, and it can say no: Muse Spark 1.3 now, Muse Glimmer on our own GPUs
   before 4 Nov. Muse Wonder 1.0 can give a second opinion (Muse Spark Open did before). Counts
   are made three ways when it matters. Spoken warnings are heard back before they play.
4. **Evidence anyone can verify.** Every event, verdict, action and staff tap goes into a
   hash-chained log with a daily Merkle root (one fingerprint for the whole day). Your browser
   can recompute it, and an inspector gets the same check through the proof portal.
5. **Workers have a voice.** Every rule is on a QR poster page. Anyone can dispute an alert or
   report a near miss by voice. On 4 Nov a worker's dispute was upheld, and it became a
   permanent Rule CI test (it also exposed a bug in how the evidence ruler was drawn, now fixed).
6. **Privacy.** No face recognition, ever. Alerts go to roles, never to names. The planner's
   guardrails refuse rules about who people are (Llama Guard 4 also screened them before 4 Nov).
   Faces are blurred in evidence by default. Since 4 Nov every model call, the alert checks
   included, goes to the Meta API; before that, Glimmer and Spark Open ran on open weights on our
   own GPUs. Our Meta API key is set to the Standard tier, never the Contributor tier, under
   which Meta may train on inputs ([`.env.example`](.env.example)). Every measured run used
   public footage or labelled SAMPLE images, never a real site's CCTV.
7. **Cameras you already own.** RTSP, HLS, MJPEG, ONVIF, a phone paired by QR code, or a public
   stream.
8. **Any language.** A Spanish rule, a Hindi instruction, spoken questions answered in the
   asker's language, and staff feeds in English, Spanish or Hindi.

## Meta models: what runs now, and what ran before

**Now** is the live system since 4 Nov 2026 (about 05:50 UTC): every call goes to the Meta API,
from the API on the team's Mac. **Before** is how the model ran in the recorded runs of 1–4 Nov.
Several 4 Nov runs used a laptop running the API against the live models rather than the
deployed pod; each report says where it ran.

| Model | Its job in Rulecam | Now | Before 4 Nov |
|---|---|---|---|
| **Muse Spark 1.3** | Plans and compiles rules; the planner's guardrail pass; runs the Ask agent's tool loop; retail checks (queues, shelf gaps, spills); judges crowd levels and plans routes; reviews worker disputes when no Tier 2 answers; web search where every kept item must cite a page the search returned. **Since 4 Nov also the Tier 1 check of every alert**, through the client Glimmer used | **Live**, Meta API | Meta API, everything but the Tier 1 check |
| **Muse Spark 1.2** | Watches a recorded clip as video, with narration | **Live** where the code uses it, Meta API | Meta API |
| **Muse Wonder 1.0** | Tier 2: second check on alerts, dispute review, plan audit, review of Ask answers, through the client Spark Open used | **Live**, Meta API | Not used |
| **SAM 3.1** | Surveys the scene, probes what the camera can detect, tracks people and objects, counts heads, confirms gaps and spills | **Live**, hosted by Meta | Hosted by Meta, and self-hosted on our RunPod serverless GPUs for runtime keyframes |
| **Muse Code** | Writes the code for new rule types; the Z3 solver then proves four properties or returns a counterexample | **Live** as the local `muse` CLI | From a laptop with the CLI; the API pod had no CLI, so there Spark 1.3 wrote the check, labelled as such |
| **Muse Voice Transcribe** | Spoken rules, questions, disputes and near-miss reports; hears every spoken warning back | **Live**, Meta API | Meta API |
| **Muse Image** | Test images when no real footage exists (the spill, the restocked shelf, the blocked exit), toolbox-talk drawings | **Live**, Meta API; every generated image is labelled SAMPLE. The Rule CI scenes step is still skipped | Meta API |
| **MMS-TTS** (Meta FAIR) | Speaks warnings and announcements in the listener's language | **Live**, on the CPU | CPU |
| **Muse Glimmer 30B** (open weights) | Checked every alert; counted for the three-way count; translated each rule back into plain words; looked for Ask and glasses mode | Not running. Its client now calls Muse Spark 1.3 | llama-server on our own serverless GPUs, with a standby endpoint in a second region and a hosted fallback |
| **Muse Spark Open** (open weights) | Second check on alerts (Tier 2), plan audit, review of Ask answers | Not running. Its client now calls Muse Wonder 1.0 | Self-hosted on our own 8 x H100 serverless endpoint: $43.12 an hour warm and about 18 minutes to start, so it was warmed for demos |
| **Llama Guard 4** | Screened each request before planning and refused rules about who people are | Not running. The planner's own guardrails still decline banned requests: a fixed pattern check, then Muse Spark | On our own serverless GPU (vLLM) |
| **Llama Prompt Guard 2** | Firewall for text found in the scene (a sign saying "ignore the rules") | Not loaded: the firewall shows "degraded", and candidates with scene text go to a person | On the API pod's CPU |
| **DINOv2** (Meta FAIR) | Joined an object's track pieces into one identity for Ask | Not running (it ran on our SAM GPU) | On our SAM GPU, prototype mode only |

The full map, with where each model runs, is in [Model map](#model-map).

## How it works

1. **Describe.** The owner types or says a concern, in any language. The planner's guardrails
   screen it. Muse Spark turns it into a plan: what to watch, on which camera, and what a camera
   cannot check.
2. **Plan.** SAM 3.1 surveys the scene and probes how often it can see what the rule needs.
   The planner measures metres on the floor, then writes the rule in a typed rule language
   (Rule IR) that a rule engine runs. A model reads the rule back in plain words.
3. **Prove.** Rule CI attacks the rule. A person approves the plan with every check on screen.
4. **Watch.** The camera sends 3-second video chunks. SAM 3.1 finds people and objects (hosted
   by Meta since 4 Nov; on our own GPU before), a tracker follows them, and the rule engine
   checks the rule on every keyframe.
5. **Check.** When the rule fires, a second model (Muse Spark 1.3 since 4 Nov; Muse Glimmer
   before) looks at a strip of frames and says yes, no or unsure. A safety-first policy decides
   what happens next.
6. **Act and record.** The alert goes to a role on the staff app, Telegram, WhatsApp or a
   webhook; a warning is spoken and heard back. Every step, and every staff tap, goes into the
   hash-chained log.

## Results

### On the Meta API alone (4 Nov, after the switch)

Measured live on 4 Nov after the move to the Meta API. The model proofs are from
`scripts/ops/model_proofs.py` (also in the message of commit `1d98493`); the rest are from the
live API that day. They are not yet written up in [`reports/eval/`](reports/eval/).

| Measured | Value |
|---|---|
| Model proofs, Tier 1 on a warehouse strip (Muse Spark 1.3) | 9.8 s, violation at p = 0.97, passed |
| Model proofs, Tier 2 (Muse Wonder 1.0) / plan audit / Ask answer review | 6.1 s / 19.9 s / 7.7 s, all passed |
| Warehouse: the Spanish 4 m forklift rule, request to plan | 146 s (survey 50.6, probe 59.8, gauge 6.0, ask 10.3, compile 0.2, audit 19.2, prove 0.1) |
| Warehouse: Rule CI on that plan | boundary 16 of 16, mutants 6 of 6 |
| Warehouse: approval | approved amber: forklift detection lower bound 0.797 against the 0.80 bar; calibration error 0.20 m against the 0.10 m limit |
| Warehouse: running live | on a looped Pexels clip, Tier 1 verdicts from `muse-spark-1.3` |
| Warehouse: verify chain, Tier 1 + Tier 2 | 11.3 s |
| Supermarket: one Ask answer (Muse Spark 1.3, SAM 3.1, Muse Wonder 1.0 review) | 57.4 s |
| Supermarket: queue check / hazard check on CAVIAR corridor clips | 18.6 s / 7.4 s. Spark reported that the corridor has no checkout tills |
| Crowd | not live now: the Jülich clips were only on the server we deleted |

### Earlier: 1–4 Nov, with Glimmer on our own GPUs

Headline numbers from the 4 Nov live runs, measured before the move to the Meta API, with Muse
Glimmer 30B on our own RunPod GPUs (each from the report it links):

| Measured | Value | Source |
|---|---|---|
| Glimmer on live forklift near misses (our GPU, runs 5–7) | 6 of 6 confirmed, 0.98–0.99 | [warehouse](reports/eval/2026-11-04-warehouse-live.md) |
| Spanish warning heard back by Voice Transcribe | word error 0.09, 0.15, 0.18: all passed | [warehouse](reports/eval/2026-11-04-warehouse-live.md) |
| Request to plan, camera already surveyed / new camera | 41.8 s and 45.1 s / 122.8 s | [warehouse](reports/eval/2026-11-04-warehouse-live.md) |
| Crowd counts against the Jülich truth, every zone and run | within 0–4% (mean error 0.6–1.6 people) | [crowd](reports/eval/2026-11-04-crowd-live.md) |
| Crowd reading to stewards told, Glimmer warm (run 6) | 26 s (Glimmer confirmed 0.95 in 6.2 s) | [crowd](reports/eval/2026-11-04-crowd-live.md) |
| Three-way count of the hottest zone, mean error | SAM 3.1 0.8, Glimmer 0.8, Spark 2.0 people | [crowd](reports/eval/2026-11-04-crowd-live.md) |
| Ask's count on a busy checkout frame, before / after the three-way count / by hand | 22 / 10 / about 12 | [supermarket](reports/eval/2026-11-04-supermarket-live.md) |
| Spill (generated SAMPLE image) seen to cleared, checked by photo | 1.9 min | [supermarket](reports/eval/2026-11-04-supermarket-live.md) |
| Camera connected to named and surveyed | 23–33 s | [connectors](reports/eval/2026-11-04-connectors.md) |
| Verified alert to Google's push service, app closed | 8.9 s from the camera chunk | [staff app](reports/eval/2026-11-04-staff-app.md) |
| Proof pack re-checked in the browser | 395 records, 97 numbers, 0.3–0.4 s | [proof portal](reports/eval/2026-11-04-proof-portal.md) |
| Glasses mode, first spoken sentence | median 8.6 s | [glasses](reports/eval/2026-11-04-glasses.md) |
| Muse Code rule type: Z3 proof, mutants caught | 4 of 4, 25 of 26 | [Muse Code](reports/eval/2026-11-04-muse-code-live.md) |

The table below is generated from the result files of the 1–3 Nov runs. Those runs also used
the earlier stack: SAM 3.1 and Glimmer on our own GPUs.

<!-- BEGIN RESULTS (generated by `uv run rulecam eval results`; do not edit) -->

Measured on the deployed stack and on public hand-labelled footage. Each row is read from the result file it cites by `uv run rulecam eval results`, with its run, date and the commit that recorded it; a test fails if a number here is not in that file. Weak results are shown as measured. The verifier is not yet scored against human labels (the blind labelling kit is in `eval/README.md`); more in [`eval/RESULTS.md`](eval/RESULTS.md).

| Measured | Value | Run | Date | Git SHA | Source |
|---|---|---|---|---|---|
| Distance measured, worker passing a moving forklift | 2.90 m | live test campaign, run 2: T02 | 2026-11-01 | `87d6cea` | [`2026-11-01-live-test-campaign.json`](reports/eval/2026-11-01-live-test-campaign.json) |
| Glimmer P(violation), worker inside the 4 m rule (verified alert) | 0.99 | live test campaign, run 2: T02 | 2026-11-01 | `87d6cea` | [`2026-11-01-live-test-campaign.json`](reports/eval/2026-11-01-live-test-campaign.json) |
| Event to verified decision | 19.0 s | live test campaign, run 2: T02 | 2026-11-01 | `87d6cea` | [`2026-11-01-live-test-campaign.json`](reports/eval/2026-11-01-live-test-campaign.json) |
| Glimmer P(violation), same rule asked in Spanish (warning queued in Spanish) | 0.98 | live test campaign, run 2: T03 | 2026-11-01 | `87d6cea` | [`2026-11-01-live-test-campaign.json`](reports/eval/2026-11-01-live-test-campaign.json) |
| Glimmer P(violation), seated driver flagged by the engine (rejected, no alert) | 0.05 | live test campaign, run 2: T04 | 2026-11-01 | `87d6cea` | [`2026-11-01-live-test-campaign.json`](reports/eval/2026-11-01-live-test-campaign.json) |
| Request to plan ready (planner, live models) | 133.6 s | live test campaign, run 2: T02 | 2026-11-01 | `87d6cea` | [`2026-11-01-live-test-campaign.json`](reports/eval/2026-11-01-live-test-campaign.json) |
| SAM 3.1 per 3 s chunk, p95 (self-hosted GPU) | 2.07 s | live test campaign, run 2: T02 | 2026-11-01 | `87d6cea` | [`2026-11-01-live-test-campaign.json`](reports/eval/2026-11-01-live-test-campaign.json) |
| Seconds with the exact people count (real CCTV, hand labels) | 22.7% | CAVIAR corridor, phone-68 | 2026-11-02 | `87d6cea` | [`2026-11-02-corridor-ground-truth.json`](reports/eval/2026-11-02-corridor-ground-truth.json) |
| Seconds with the count within one person | 53.4% | CAVIAR corridor, phone-68 | 2026-11-02 | `87d6cea` | [`2026-11-02-corridor-ground-truth.json`](reports/eval/2026-11-02-corridor-ground-truth.json) |
| Peak people at once, system vs labels | 10 vs 8 | CAVIAR corridor, phone-68 | 2026-11-02 | `87d6cea` | [`2026-11-02-corridor-ground-truth.json`](reports/eval/2026-11-02-corridor-ground-truth.json) |
| Person tracks and identities vs labelled visits | 416 tracks, 39 identities vs 80 visits | CAVIAR corridor, phone-68 | 2026-11-02 | `87d6cea` | [`2026-11-02-corridor-ground-truth.json`](reports/eval/2026-11-02-corridor-ground-truth.json) |
| Backfill of recorded chunks (tracked points in seconds) | 5301 points in 409 s | CAVIAR corridor, phone-68 | 2026-11-02 | `87d6cea` | [`2026-11-02-corridor-ground-truth.json`](reports/eval/2026-11-02-corridor-ground-truth.json) |
| Glimmer decisive before the strip renderer existed | 0 of 10 candidates | first recorded run (warehouse driver only) | 2026-10-31 | `79bd84a` | [`2026-10-31.json`](reports/eval/2026-10-31.json) |
| Person tracking, HOTA / IDF1 vs CAVIAR labels: ByteTrack, SAM detections at 0.5 and up | 0.239 / 0.240 | CAVIAR corridor tracks, morning backfill (ByteTrack) | 2026-11-02 | `1786ad2` | [`2026-11-02-corridor-tracking-before.json`](reports/eval/2026-11-02-corridor-tracking-before.json) |
| ID switches per minute, same run | 19.6 | CAVIAR corridor tracks, morning backfill (ByteTrack) | 2026-11-02 | `1786ad2` | [`2026-11-02-corridor-tracking-before.json`](reports/eval/2026-11-02-corridor-tracking-before.json) |
| Person tracking, HOTA / IDF1, same footage: ByteTrack with SAM detections down to 0.1 | 0.223 / 0.217 | CAVIAR corridor tracks, re-tracked with ByteTrack at the 0.1 floor (add-on 2.1) | 2026-11-02 | `1786ad2` | [`2026-11-02-corridor-tracking-bytetrack.json`](reports/eval/2026-11-02-corridor-tracking-bytetrack.json) |
| ID switches per minute, same run | 21.2 | CAVIAR corridor tracks, re-tracked with ByteTrack at the 0.1 floor (add-on 2.1) | 2026-11-02 | `1786ad2` | [`2026-11-02-corridor-tracking-bytetrack.json`](reports/eval/2026-11-02-corridor-tracking-bytetrack.json) |
| Our HOTA vs TrackEval's on the same segment (scorer cross-check) | 0.2603 vs 0.2603 | CAVIAR TwoEnterShop1cor, our scorer vs TrackEval | 2026-11-02 | `1786ad2` | [`2026-11-02-trackeval-crosscheck.json`](reports/eval/2026-11-02-trackeval-crosscheck.json) |
| Same strips replayed (8 raw frames at 960 px), verdicts matching the 1 Nov ones | 27 of 29 | Glimmer replay of the 1 Nov campaign strips | 2026-11-02 | `8bb65e3` | [`2026-11-02-verifier-replay-summary.json`](reports/eval/2026-11-02-verifier-replay-summary.json) |
| Decisive verdicts that flip with 6 face-blurred frames at 640 px instead | 6 of 29 | Glimmer replay of the 1 Nov campaign strips | 2026-11-02 | `8bb65e3` | [`2026-11-02-verifier-replay-summary.json`](reports/eval/2026-11-02-verifier-replay-summary.json) |
| Glimmer median per strip, 8 raw at 960 px vs 6 blurred at 640 px | 10898 ms vs 8505 ms | Glimmer replay of the 1 Nov campaign strips | 2026-11-02 | `8bb65e3` | [`2026-11-02-verifier-replay-summary.json`](reports/eval/2026-11-02-verifier-replay-summary.json) |
| Verdicts unchanged with a reasoning budget of 0 tokens (llama.cpp ignores the budget; reasoning stays short) | 20 of 20 | Glimmer reasoning-budget sweep | 2026-11-02 | `1786ad2` | [`2026-11-02-glimmer-budget-sweep.json`](reports/eval/2026-11-02-glimmer-budget-sweep.json) |
| Fights, "Alert me if people start fighting": found, missed, false alerts | 0 found, 4 missed, 0 false alerts | CAVIAR lobby clips (384x288, overhead), live rule runs | 2026-11-02 | `1786ad2` | [`2026-11-02-caviar-inria-events.json`](reports/eval/2026-11-02-caviar-inria-events.json) |
| Fight candidates on Fight_Chase, every one rejected by Glimmer as no visible fight | 8 candidates, 0 alerts | CAVIAR lobby clips (384x288, overhead), live rule runs | 2026-11-02 | `1786ad2` | [`2026-11-02-caviar-inria-events.json`](reports/eval/2026-11-02-caviar-inria-events.json) |
| Falls, "Tell me if anyone falls down": found, missed, false alerts | 0 found, 2 missed, 0 false alerts | CAVIAR lobby clips (384x288, overhead), live rule runs | 2026-11-02 | `1786ad2` | [`2026-11-02-caviar-inria-events.json`](reports/eval/2026-11-02-caviar-inria-events.json) |
| Left bags and boxes: found, missed, false alerts | 0 found, 3 missed, 0 false alerts | CAVIAR lobby clips (384x288, overhead), live rule runs | 2026-11-02 | `1786ad2` | [`2026-11-02-caviar-inria-events.json`](reports/eval/2026-11-02-caviar-inria-events.json) |
| Shop entries, "Let me know each time someone walks into one of the shops": found, missed, false alerts | 0 found, 19 missed, 0 false alerts | CAVIAR corridor clips, live rule runs | 2026-11-02 | `1786ad2` | [`2026-11-02-caviar-corridor-events.json`](reports/eval/2026-11-02-caviar-corridor-events.json) |
| One alert check on warm GPUs (SAM 3.1 on a 3 s chunk, then Glimmer on its strip), total | 28.7 s | Serverless load test (SAM chunk + Glimmer strip per check) | 2026-11-02 | `1786ad2` | [`2026-11-02-serverless-load.json`](reports/eval/2026-11-02-serverless-load.json) |
| 20 alert checks at once on warm GPUs, total p50 / p95 | 36.0 s / 51.9 s | Serverless load test (SAM chunk + Glimmer strip per check) | 2026-11-02 | `1786ad2` | [`2026-11-02-serverless-load.json`](reports/eval/2026-11-02-serverless-load.json) |
| GPU cost per alert check, 20 at once (RunPod serverless, billed worker time) | $0.0039 | Serverless load test (SAM chunk + Glimmer strip per check) | 2026-11-02 | `1786ad2` | [`2026-11-02-serverless-load.json`](reports/eval/2026-11-02-serverless-load.json) |
| Checks answered within 300 s after SAM scaled to zero (1 and 5 at once; Glimmer stayed warm) | 0 of 1, 4 of 5 | Serverless load test (SAM chunk + Glimmer strip per check) | 2026-11-02 | `1786ad2` | [`2026-11-02-serverless-load.json`](reports/eval/2026-11-02-serverless-load.json) |
| Ask's graded numbers within tolerance of the hand labels | 7 of 36 | Ask benchmark, CAVIAR corridor, 10 questions x 3 runs (`rulecam eval ask`) | 2026-11-02 | `1786ad2` | [`2026-11-02T230520Z-corridor.json`](reports/eval/ask/2026-11-02T230520Z-corridor.json) |
| Ask's median answer time | 41.8 s | Ask benchmark, CAVIAR corridor, 10 questions x 3 runs (`rulecam eval ask`) | 2026-11-02 | `1786ad2` | [`2026-11-02T230520Z-corridor.json`](reports/eval/ask/2026-11-02T230520Z-corridor.json) |
| "How many went into a shop?" (truth: 24), the three runs' answers | 14, 11, 32 | Ask benchmark, CAVIAR corridor, 10 questions x 3 runs (`rulecam eval ask`) | 2026-11-02 | `1786ad2` | [`2026-11-02T230520Z-corridor.json`](reports/eval/ask/2026-11-02T230520Z-corridor.json) |
| MOT17 HOTA by TrackEval, lowest (MOT17-13) and highest (MOT17-04) sequence; keyframes only, so not directly comparable with published trackers | 0.203 to 0.450 | MOT17 training set, 7 sequences, SAM 3.1 keyframes at 10 per second + ByteTrack (0.1 floor) | 2026-11-03 | `1786ad2` | [`2026-11-02-mot17.json`](reports/eval/2026-11-02-mot17.json) |

<!-- END RESULTS -->

An earlier E3 figure came from a simulated harness and was withdrawn on Oct 31 (see
[`eval/RESULTS.md`](eval/RESULTS.md)).

## Honest limits

- **The live site is one Mac.** The API and the site run on the team's Mac through a Cloudflare
  tunnel. If the Mac or the tunnel stops, the live pages stop; if the tunnel restarts, the
  address changes. The replay also runs offline (`cd ui && npm ci && npm run dev`).
- **Most results come from the earlier stack.** The 1–4 Nov results were measured with Glimmer
  on our own GPUs (and Spark Open on 8 x H100 where a run used it), before the move to the Meta
  API. On the Meta API alone we have the numbers [above](#on-the-meta-api-alone-4-nov-after-the-switch),
  not yet a full re-run.
- **Not running now:** Llama Guard 4 (the planner's own guardrails still decline banned
  requests) and Prompt Guard 2 (the firewall shows "degraded"; candidates with scene text go to
  a person). **Crowd is not live now**: the Jülich clips were only on the server we deleted, so
  its results are recorded results.
- **Many 4 Nov runs were on a laptop**, running the API against the live models, not on the
  deployed pod. Each report says where it ran.
- **GPU capacity decided the speed before 4 Nov.** When the serverless SAM pool was throttled, a
  camera chunk took about 123 s to become a candidate; warm, about 2 s. Spark Open needed about
  18 minutes to start.
- **Glimmer once held a true crowd alert.** In crowd run 5 it described a crush-level crowd but
  answered "not confirmed" (0.68). Now Spark decides to send or hold when the Tier 1 check
  disagrees; that path is unit-tested but has not come up live yet.
- **Counts from tracks are high on busy views**, and Ask's counts were weak on hand-labelled
  CCTV (7 of 36 within tolerance, 2 Nov). The three-way count fixes the busy-frame case it was
  tested on, not every case.
- **Some footage is generated.** The spill, the restocked shelf and the blocked fire exit are
  Muse Image edits, labelled SAMPLE. Chain Mode's scorecard is SIMULATED.
- **Not tested on real hardware:** a real CCTV camera or NVR, real glasses, a real phone for
  push notifications, a real speaker.
- **Low-resolution overhead footage beats some rules.** On CAVIAR clips (384x288) shop entries,
  fights, falls and left bags were all missed, with no false alerts.
- **Prototype mode is on in the hackathon deployment.** See
  [What we claim, and what we don't](#what-we-claim-and-what-we-dont).
- **Rulecam is not a safety system of record.** It helps people watch. It does not replace a
  guard, a lock-out or a supervisor.

---

## More detail

### The problem

- **Cameras are everywhere, but nobody is watching.** One retailer found that less than 1% of
  its recorded video is ever viewed
  ([Security Magazine](https://www.securitymagazine.com/articles/76977-so-why-isnt-shrink-shrinking-1)).
  A small site has cameras, but no safety team to watch them, write rules for them, or check
  that an alert is real.
- **Forklifts hurt people every day.** OSHA counts about **85 deaths and 34,900 serious
  injuries a year** from forklifts in the US
  ([OSHA, Powered Industrial Trucks notice](https://www.osha.gov/sites/default/files/enforcement/directives/reg1_fy2018_cpl-04-00-023e_powered-industrial-trucks.pdf)).
  One in four blue-collar workers saw a near miss in the past year and didn't report it
  ([TRADESAFE survey](https://trdsf.com/blogs/news/workplace-safety-silence-reporting-risks)).
- **Language matters.** In 2024 Hispanic or Latino workers died at work at 4.3 per 100,000
  full-time workers, against 3.3 for all workers
  ([BLS](https://www.bls.gov/news.release/cfoi.nr0.htm)). OSHA requires training in a language
  workers understand.
- **Empty shelves and queues cost sales.** Out-of-stocks cost about 7.4% of retail sales in
  2021, per NielsenIQ data
  ([report](https://a-closer-look.com/wp-content/uploads/2024/09/Report-Out-of-Sight-Out-of-Mind-The-Hidden-Cost-of-Empty-Shelves-and-Broken-Displays_FINAL-sm.pdf)).
- **Crowds get dangerous before anyone can see it from the ground.** In a crush, nobody does
  anything wrong. People are packed so tightly that they can no longer move by choice. Inquiries
  keep finding the same thing: the warning existed, and it did not become action
  ([our research, with sources](docs/pitch/research/crowd.md#what-repeats)). A camera looking
  down can count, and an alert can name who does what next.
- **Trust is the gap.** Plain-language camera alerts already exist. What owners, workers and
  insurers can't get is a way to check that a rule works and an alert is real
  ([market research](docs/pitch/research/)).

### Who it helps

- **A small warehouse** with forklifts and no safety officer.
- **A supermarket owner** who wants to know about queues, empty shelves and busy hours, across
  one store or many.
- **A venue, station or festival** that wants a warning before the crowd at a gate gets too
  dense.
- **The crew.** They see every rule on a QR poster page, can dispute an alert with one tap, and
  are never recognised by face.

### For judges: the 2-minute path

| Time | Open | What to look for |
|---|---|---|
| 0:00 | `https://rulecam-live.vercel.app/demo?demo=1` | The owner's plain-language request, the planner's steps, the compiled rule and its acceptance trace |
| 0:30 | the Rule CI section of the same page | The rule was attacked with boundary scenes and mutants before anyone could approve it, re-run offline in CI |
| 0:50 | **Verify it in your browser** | Your browser recomputes every SHA-256 of the evidence chain and its Merkle root |
| 1:20 | [Results](#results), then [`reports/eval/`](reports/eval/) | Live runs on real video and on hand-labelled CCTV, every number tied to its run, date and commit |
| 1:45 | [Model map](#model-map) | Which Meta model does which job, and where it runs |

> **Built during the hackathon.** Everything in this repository was written between Oct 26 and
> Nov 4 2026. [`WHAT-WE-BUILT.md`](WHAT-WE-BUILT.md) lists every module with the pull request
> that added it, and every reused library or model with its licence.

### How we use Meta models, beyond a basic call

- **Muse Spark is an agent with a fixed set of steps.** It turns a sentence into a plan, asks
  clarifying questions, grounds each rule in a standard (OSHA, FDA Food Code), and compiles it
  into Rule IR: survey, probe, calibrate, compile, check, repair.
- **Muse Spark answers questions about what the cameras saw.** The Ask agent runs a tool loop:
  SQL over SAM 3.1 tracks, SAM 3.1 counts on recorded frames, a model looking at frames with
  numbered boxes (Glimmer before 4 Nov), and Spark 1.2 watching the recorded clip as video.
- **Muse Spark searches the web, and we check every source.** For site context (weather,
  events, food recalls) an item is kept only if its source is a page the search really
  returned ([site tools report](reports/eval/2026-11-03-site-tools-live.md)).
- **Muse Spark and SAM 3.1 check each other.** On shelves, Spark proposes empty facings and SAM
  3.1 confirms no product package is inside each one. In crowds, SAM counts and Spark judges.
- **Three counts of the same frame.** SAM 3.1 boxes, Spark pointing at each person on a
  0–1000 grid, and a third count through the client Glimmer used, none seeing the others; Spark
  reconciles them and says why. On a busy checkout frame on 4 Nov, with Glimmer on our own GPU
  making the third count, this moved Ask's answer from 22 to 10 (about 12 by hand).
- **Speech is checked by listening.** Spark writes a warning or announcement, MMS-TTS speaks it,
  Voice Transcribe hears it back, and Spark judges whether the meaning survived. It caught a
  Hindi announcement that came out as "crowd" instead of "gate".
- **Spark plans routes and a guard checks them.** Before Spark sends a crowd anywhere, the
  route guard reads that place's camera; when side door B was already filling, Spark changed
  its plan to hold the inflow instead.
- **The Tier 1 check: Glimmer before, Muse Spark 1.3 now.** The Tier 1 client also translates each rule
  back into plain words, so the owner can check it says what they meant. Until 4 Nov it called
  Muse Glimmer, open weights, in llama-server on RunPod serverless GPUs behind a load balancer
  we built, with a standby in a second region. Since 4 Nov the same client calls Muse Spark 1.3
  on the Meta API, which speaks the same OpenAI chat format.
- **The Tier 2 check: Spark Open before, Muse Wonder 1.0 now.** Until 4 Nov Muse Spark Open, open weights on our own 8 x H100, did the
  Tier 2 check, the plan audit and the review of Ask's answers. Since 4 Nov the same client calls
  Muse Wonder 1.0 on the Meta API.
- **Banned requests are declined before planning.** The planner's guardrails (a fixed pattern
  check, then Muse Spark) refuse rules about who people are. Until 4 Nov Llama Guard 4 screened
  each request too.
- **Muse Code writes code, and a solver proves it.** For a rule type the engine does not have,
  Muse Code writes a check. Rulecam runs it in a sandbox, tests it on generated scenes and
  broken copies, and has the Z3 solver prove four properties for every valid scene up to a set
  number of objects (2 in the 4 Nov run): no alert without the target object, never crashes,
  moving the whole image changes nothing, and a declared move never turns an alert off. Anything
  it cannot prove exactly is marked unsupported, never proved. On 4 Nov Z3 found a real bug
  in one version (a vertical line divided by zero), and the repair round fixed it.

### What it does, step by step

1. **Describe it.** The owner types or says what worries them. Muse Spark plans the job: what
   to watch, which camera covers it, and what a camera *cannot* check. The feasibility screen
   says so before anything goes live.
2. **Plan it.** The planner looks at the scene with SAM 3.1, measures real distances, and
   writes the rule in Rule IR. It shows the plan, what changed since the last approved plan,
   and every check it passed.
3. **Prove it.** Rule CI attacks every rule with edge-case scenes and mutants before the owner
   can approve it. By default, a rule that fails Rule CI cannot go live. In prototype mode (on
   in the hackathon deployment) a failed check is shown as an amber caveat instead.
4. **Trust it.** Before anyone is alerted, a second model (Muse Spark 1.3 since 4 Nov; Muse
   Glimmer before) looks at a strip of frames from the event and says yes, no or unsure
   (Tier 1). Every event, verdict, action and acknowledgement goes into a hash-chained log with
   a daily Merkle root. Anyone can check it in the browser, with no server.

### Architecture

The live system since 4 Nov. Every model box is a Meta API call.

```mermaid
flowchart LR
  owner([Owner]) -- "plain-language request" --> planner["Planner<br/>Muse Spark 1.3"]
  planner -- "survey and probes" --> sam["SAM 3.1<br/>Meta API"]
  planner --> ci["Rule CI<br/>boundary scenes, mutants"]
  ci --> approve{"Owner approves<br/>the plan"}
  phone([Phone or camera]) -- "3 s H.264 chunks" --> api["FastAPI runtime<br/>(team's Mac)"]
  approve --> api
  api -- "keyframes" --> sam
  sam --> tracks[("Track store<br/>ByteTrack")]
  tracks --> engine["Rule engine<br/>typed Rule IR"]
  engine -- "candidate + evidence strip" --> tier1["Tier 1 check<br/>Muse Spark 1.3"]
  tier1 --> policy["Verification policy"]
  tier1 --> tier2["Tier 2 review<br/>Muse Wonder 1.0"]
  tier2 --> policy
  policy --> actions["Alerts and actions<br/>(dry-run by default)"]
  policy --> chain[("Hash-chained<br/>evidence log")]
  chain --> verify["Browser verify page<br/>Merkle root"]
  owner -- "questions" --> ask["Ask agent<br/>Muse Spark 1.3"]
  ask --> tracks
  ask --> sam
  ask -- "looks at frames" --> tier1
```

Before 4 Nov the runtime ran on a RunPod CPU pod, sent keyframes to our own SAM 3.1 on
serverless GPUs, and sent the Tier 1 check to Muse Glimmer and Tier 2 to Muse Spark Open, both
on our own GPUs. `/embed` on the SAM GPU gave Ask its identities (DINOv2 for objects, OSNet-AIN
for people, prototype mode only). The RunPod setup is kept as a record in
[`docs/deploy.md`](docs/deploy.md).

### Model map

Which model does which job, where it runs, and how far it is wired today. **Live** means the
deployed app calls it; since 4 Nov 2026 that is the API on the team's Mac, using only the Meta
API. **Before 4 Nov** means it ran in the recorded runs of 1–4 Nov and does not run now.
**Library** means the code is built and tested, but the live app does not call it yet.
**Planned** means it is not built.

| Meta model | Job | Runs where | Status |
|---|---|---|---|
| Muse Spark 1.3 (+1.2 for narrated video) | Planner, compiler, standards grounding, the planner's guardrail pass, Ask agent, retail checks, crowd warden; since 4 Nov the Tier 1 verifier too | Meta API | Live in the Planner (P2-01, P2-06) and in `POST /ask`, where Spark 1.2 can also watch a joined clip of the window it picks (`watch`, add-on 2.8). Since 4 Nov it is also the Tier 1 verifier: the Glimmer client points at the Meta API with `GLIMMER_MODEL=muse-spark-1.3` ([`docs/deploy.md`](docs/deploy.md)). If the Tier 1 call fails, unverified candidates go to the human queue. Crowd watch (`crowd/warden.py`) ran live on the API pod on 3 Nov. Site tools (run live from a laptop on 3 Nov, not yet on the deployed stack): `/context` uses its **web search** with the raw results returned, and keeps an item only if its source is a page the search returned; `/crowd` has it **watch the clip** of each crowded spell; `/shelf` uses its **image understanding** to find shelf gaps |
| Muse Wonder 1.0 | Tier 2 verifier, plan audit, Ask answer review, dispute review | Meta API | Live since 4 Nov: the Spark Open client points at the Meta API with `SPARK_OPEN_MODEL=muse-wonder-1.0`. Failing, Tier 2, plan audit and Ask review are skipped and logged; Tier 1 decides alone and anything unsure goes to a person. Muse Spark 1.3 reviews disputes when no Tier 2 answers |
| Muse Voice Transcribe | Spoken requests, questions, disputes and near-miss reports; hears every spoken warning back | Meta API | Live (warehouse, crowd, supermarket and glasses runs of 4 Nov) |
| SAM 3.1 | Survey, probes, tracking, coverage, masks, head counts | Meta API (hosted). Before 4 Nov also self-hosted on a RunPod serverless GPU (RTX 4090) | Live: hosted client (P1-02) for survey, probes, Rule CI and, since 4 Nov, the runtime's keyframes. Before 4 Nov the runtime sent keyframes to our self-hosted SAM 3.1 (`SAM_BASE_URL`, a RunPod serverless load balancer since 2 Nov) and fell back to hosted (P1-03); crop-zoom (add-on 3.3) re-ran it there on enlarged head and hand crops for worn items. Crowd watch counts heads per zone with it |
| Muse Code | Writes new rule-type checks (`extensions/`), proved with Z3 (add-on 7) | The local `muse` CLI (Meta API) | Live as the local `muse` CLI. The 4 Nov run used the CLI from a laptop: Z3 4/4, mutation 25/26, approved on the live API, run on a real clip. Where no CLI is installed, `POST /extensions` uses Spark 1.3 as the writer, labelled `spark-fallback` |
| Muse Image | Generated test images when no footage exists (spill, restocked shelf, blocked exit), toolbox-talk drawings, Z3 counterexample drawings; Rule CI scenes | Meta API | Live for test images, every one labelled SAMPLE. The Rule CI scenes step (P2-09, #71) is built but skipped |
| MMS-TTS (FAIR) | Spoken warnings and announcements | CPU | Live: Spanish forklift warnings, Hindi and English crowd announcements and glasses answers on 4 Nov, each heard back by Voice Transcribe where the report says so |
| Muse Glimmer 30B (open weights) | Tier 1 verifier, back-translation, recompute, Ask's eyes, crowd check, three-way counts, glasses mode | Before 4 Nov: llama-server on RunPod serverless GPUs in three US data centers, a standby endpoint in two EU data centers, and a hosted fallback (Routeway) as the last resort | Before 4 Nov: live as Tier 1 (P3-03). By default it saw 6 face-blurred keyframes at 640 px (P4-05); the deployed **prototype mode** sent 8 raw frames at 960 px. Not running now; its client calls Muse Spark 1.3 |
| Muse Spark Open 816B-A42B (open weights) | Tier 2 verifier, plan audit, Ask answer review, dispute review | Before 4 Nov: llama.cpp on our own RunPod serverless 8 x H100 endpoint (Baseten stayed an option) | Before 4 Nov: live, $43.12 an hour warm and about 18 minutes to start, so it was warmed for demos. Not running now; its client calls Muse Wonder 1.0 |
| Llama Prompt Guard 2 (86M) | Scene-text firewall | CPU, inside the API process | Not loaded now, so `/runtime/metrics` shows `firewall: degraded` and candidates with scene text go to a person. Before 4 Nov it ran on the API pod |
| Llama Guard 4 12B (FP8) | Screens each request before planning; refuses rules about who people are (S7 privacy, S10 hate) | Before 4 Nov: vLLM on our own RunPod serverless GPU | Not running now. The screen is skipped and the planner's own guardrails still decline banned requests: a fixed pattern check, then Muse Spark |
| DINOv2 (FAIR) | Joins an object's track fragments into one identity for Ask | Before 4 Nov: `/embed` on the SAM serverless GPU | Before 4 Nov: live in prototype mode only (`ask/identity.py`). Not running now |
| Sapiens (FAIR) | Pose and skeletons | GPU | Planned. Meanwhile RTMPose-l body7 (OpenMMLab, ONNX on the API's CPU, when `RULECAM_POSE_ONNX` is set) draws each person's pose into the evidence strips (add-on 3.4) |
| V-JEPA 2 (FAIR) | Surprise Lens world model | GPU | Planned (gated) |

Other models in the pipeline, picked on quality and speed. Before 4 Nov, **OSNet-AIN** person
re-ID vectors (BoxMOT, on the SAM GPU) fed **BoT-SORT**, which tracks people between SAM
keyframes, and Ask's person identities (prototype mode only, body crops, never faces); in the
hackathon deployment people stayed on ByteTrack (`RULECAM_PERSON_TRACKER=bytetrack`) because of
an OSNet fix the GPU volume did not yet carry. **MoGe-2** (Microsoft, on the SAM GPU) gave the
planner a metric ground calibration from one frame (add-on 3.5). Neither runs now: with no GPU
of our own, people and objects are tracked with ByteTrack, and the floor is calibrated from
reference objects ([`docs/deploy.md`](docs/deploy.md)).

### Documents

| File | Purpose |
|---|---|
| [`SUBMISSION.md`](SUBMISSION.md) | The hackathon submission text |
| [`docs/video/script.md`](docs/video/script.md) | The 3-minute video: shot list, voiceover, captions, fallbacks |
| [`docs/pitch/deck.md`](docs/pitch/deck.md) | The 10-slide finals deck with speaker notes |
| [`docs/pitch/launch.md`](docs/pitch/launch.md) | Launch posts and the 30-second vertical cut |
| [`docs/DEMO.md`](docs/DEMO.md) | The live demo script and the golden REPLAY |
| [`docs/demo-day.md`](docs/demo-day.md) | Demo-day runbook: what runs where, start-up checks, failures and fallbacks |
| [`docs/usecases/`](docs/usecases/) | Each world and platform piece in detail |
| [`docs/TASKS.md`](docs/TASKS.md) | Full team task book: rules, contracts, gates, every task |
| [`docs/master-plan.md`](docs/master-plan.md) | Product plan (v3): proof stack, use cases, evaluation |
| [`docs/v2-design.md`](docs/v2-design.md) | Engine internals: Rule IR, perception, Rule CI, verifier, privacy |
| [`docs/site-advisor.md`](docs/site-advisor.md) | Site Advisor: business advice you can check |
| [`src/rulecam/chain/README.md`](src/rulecam/chain/README.md) | Chain Mode: one rule pack for many stores |
| [`docs/deploy.md`](docs/deploy.md) | Deployment: Meta API only since 4 Nov; the earlier RunPod setup (API pod, Glimmer, SAM) kept as the record |
| [`WHAT-WE-BUILT.md`](WHAT-WE-BUILT.md) | What we wrote during the hackathon vs. what we reused, with licences |
| [`evidence/`](evidence/README.md) | Screenshots and verify-page captures behind our claims |
| [`eval/RESULTS.md`](eval/RESULTS.md) | Measured evaluation results |

### Run it on your own machine

You need [`uv`](https://docs.astral.sh/uv/) (it installs Python 3.12), Node 20+, `ffmpeg` and
`git`. On macOS: `brew install uv node ffmpeg`.

```bash
git clone https://github.com/jeevithg090/Axi0N_Meta.git
cd Axi0N_Meta
./start_rulecam.sh
```

On Windows, run `./start_rulecam.ps1` in PowerShell instead. The script checks for the tools
above, creates `.env` from `.env.example`, runs `uv sync` and `npm ci` the first time, loads
made-up SAMPLE data, starts the API on `http://localhost:8000` and the site on
`http://localhost:3000`, and lists the pages to open. Press Ctrl+C to stop both. Logs go to
`data/logs/local-{seed,api,ui}.log`. Use `./start_rulecam.sh --no-seed` to skip the sample data.

- `http://localhost:3000/demo?demo=1` replays a recorded live run. It needs no API keys and
  makes no model calls.
- `/monitor`, `/streams`, `/planner` and `/audit` show the SAMPLE data. Those records were
  written by hand (each one is labelled `[SAMPLE ...]`), so don't quote numbers from them.
- To run with real models, put a Meta API key in `.env.live` (see "Live" below).

Or with Docker: `docker compose up --build` starts the same two services (without the sample
data).

### Quick start (development)

```bash
make setup        # uv sync + pre-commit hooks
cp .env.example .env
make check        # lint + types + tests + coverage floors — must pass before every push
uv run rulecam version
```

Requires Python 3.12 (via `uv`), `ffmpeg`, and Node 20+ for the UI.

```bash
cd ui && npm ci && npm run dev          # the web UI on http://localhost:3000
uv run rulecam chain demo chains/kitchen-basics   # Chain Mode, no models, about 10 s
```

- **Without keys:** open `http://localhost:3000/demo?demo=1`. It replays a recorded live run
  (labelled REPLAY in the header) with no model calls. Nothing in it is hand-written.
- **Live:** put the Meta API key and the "Meta API only" settings from `.env.example` in
  `.env.live` (not `.env`: the tests read `.env`), then follow the "Meta API only" section of
  [`docs/deploy.md`](docs/deploy.md). The header badge reads LIVE.
- **Check an evidence log:** `uv run rulecam audit <YYYY-MM-DD> <site>` re-verifies a day's
  chain and re-derives every decision; or drop the chain file on the browser verify page.

### How we work

Code, pull requests and **CI all live on GitHub** (GitHub Actions, `.github/workflows/ci.yml`:
the `check`, `guardrails` and `ml-cpu` jobs). Every task ends in a pull request with all checks
green; see `docs/TASKS.md` §0 and §3.4, and `CLAUDE.md`.

### What we claim, and what we don't

- **We claim** what `eval/RESULTS.md`, `reports/eval/`, the golden runs and `evidence/` show,
  and nothing more. Every number in the UI and in the video comes from a run you can replay.
  The hand-made sample data used to build the cockpit screens (`scripts/seed_live_backend.py`)
  is not a run, and we never quote it as a result.
- **REPLAY is never shown as LIVE.** The header badge says which one you are watching, and it
  never switches silently.
- **The verifier can say no.** The Tier 1 and Tier 2 checks can reject a candidate; safety-critical
  rules fail towards alerting a human, never towards silence. In crowd watch, a verifier that
  does not answer in 20 s never holds back the warning.
- **Rulecam is not a safety system of record.** It helps people watch; it does not replace a
  guard, a lock-out or a supervisor. Interlocks (Kasa plugs) only ever stop equipment, and only
  a human can resume it.
- **No face recognition, ever.** In the default modes faces are blurred in evidence strips, the
  anonymous and strict privacy modes keep no person pixels at all, and nobody is re-identified.
- **Prototype mode is different, and the hackathon deployment runs it**
  (`RULECAM_PROTOTYPE_MODE=true`, `src/rulecam/prototype.py`): the planner declines only
  requests that single people out by race, ethnicity, skin colour, religion, nationality,
  immigration status or sexual orientation, and compiles every other concern; every compiled
  rule activates, with failed acceptance and Rule CI checks shown as amber caveats; the Tier 1
  model sees the raw, unblurred frames (since 4 Nov that model is Muse Spark 1.3 on the Meta
  API); the verifier's verdict is final (a rejection is suppressed whatever the rule's severity,
  so the "fail towards alerting a human" point above holds only outside prototype mode); and
  `POST /ask` **re-identifies people** across track fragments by appearance (OSNet-AIN re-ID
  vectors of body crops, plus a short Glimmer note on clothing) so an owner can ask about
  visitors and dwell time. The tracker can use the same vectors to keep a person's track between
  keyframes (BoT-SORT). The re-ID vectors came from our SAM GPU, so this part has not run since
  4 Nov. It never uses faces and never names anyone. Prototype mode is off by default and in CI.
- **Hosted models and your footage.** The Meta Model API key is configured for the Standard
  tier, never Contributor (`.env.example`). Since 4 Nov every model call goes to the Meta API,
  the alert checks included. Before that, Glimmer and Spark Open, which checked alerts, ran on
  open weights on our own GPUs, and a hosted Glimmer (Routeway) was only the last fallback when
  every one of our endpoints failed. Every measured run in this repository used public footage
  (Pexels and Mixkit stock video, CAVIAR, MOT17, the Jülich experiments, Wikimedia Commons
  photos, a public live webcam) or images generated by Muse Image and labelled SAMPLE, never a
  real site's CCTV.

### Licences

The code in this repository was written during the hackathon and is MIT-licensed. Third-party
components are used as dependencies under their own licences; see
[`WHAT-WE-BUILT.md`](WHAT-WE-BUILT.md). Llama Prompt Guard 2 (not loaded since 4 Nov) and Llama
Guard 4 (run until 4 Nov) are used under the Llama 4 Community License: **Built with Llama**.
**Built with Muse Spark Open**, which we self-hosted on our own GPUs until 4 Nov.
