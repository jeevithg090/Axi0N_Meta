# Rulecam

**Rulecam turns a phone or a CCTV camera into a safety helper you set up in plain words.
You say what worries you. Rulecam works out what the camera can and can't check, proves each
rule before it goes live, and has a second AI model look at every alert before anyone is
bothered.**

*Describe it. Plan it. Prove it. Trust it.*

Built for the Meta Global AI Developer Hackathon (build window Oct 26 – Nov 4 2026), track
**Build for Your World**.

> **Runs Muse Glimmer (Meta open weights) on our own GPU.** · **Built with Muse Spark Open** ·
> **Built with Llama** (Llama Prompt Guard 2). Muse Spark Open is wired in for plan audit, dispute
> review and Ask review, but it is not live yet: the hackathon deployment has no working Baseten
> key, so those calls are skipped and logged.

## The problem

- **Forklifts hurt people every day.** OSHA counts about **85 deaths and 34,900 serious
  injuries a year** from forklifts in the US
  ([OSHA, Powered Industrial Trucks notice](https://www.osha.gov/sites/default/files/enforcement/directives/reg1_fy2018_cpl-04-00-023e_powered-industrial-trucks.pdf)).
  The National Safety Council lists forklifts as the source of **84 work deaths in 2024**
  ([NSC Injury Facts](https://injuryfacts.nsc.org/work/safety-topics/forklifts/)).
- **Food makes people sick.** The CDC estimates that **48 million people** in the US get sick
  from food each year; **128,000** go to hospital and **3,000** die
  ([CDC, Facts About Food Poisoning](https://www.cdc.gov/food-safety/data-research/facts-stats/index.html)).
  Handwashing is one of the first rules in the FDA Food Code (§2-301.12).
- **Cameras are everywhere, but nobody is watching.** A small site already has cameras. It does
  not have a safety team to watch them, write rules for them, or check that an alert is real.
  Most AI camera products need an expert to set them up, and many want to know who you are.

## Who it helps

- **A small warehouse** with forklifts and no safety officer: "keep people 2 m from a moving
  forklift".
- **A restaurant kitchen** before a health inspection: "everyone washes their hands for 20
  seconds".
- **A shop, venue or festival** worried about crowding at a gate or a shelf running empty.
- **The crew.** They see every rule on a QR poster page, can dispute an alert with one tap, and
  are never recognised by face.

## For judges: the 2-minute path

- **Video: TODO — link added at submission.**
- **Try it, no login, no keys:** [rulecam.vercel.app/demo?demo=1](https://rulecam.vercel.app/demo?demo=1)
  replays a recorded live run with no model calls. The header says REPLAY. It works with every
  GPU switched off.

| Time | Open | What to look for |
|---|---|---|
| 0:00 | [`/demo?demo=1`](https://rulecam.vercel.app/demo?demo=1) | The owner's plain-language request, the planner's steps, the compiled rule and its acceptance trace |
| 0:30 | the Rule CI section of the same page | The rule was attacked with boundary scenes and mutants before anyone could approve it, re-run offline in CI |
| 0:50 | **Verify it in your browser** | Your browser recomputes every SHA-256 of the evidence chain and its Merkle root; nothing is trusted from us |
| 1:20 | [Results](#results) below, then [`reports/eval/`](reports/eval/) | Live runs on real video and on hand-labelled CCTV, every number tied to its run, date and commit |
| 1:45 | [Architecture](#architecture) | Which Meta model does which job, and where it runs |

Prototype mode is on in the hackathon deployment (see [What we claim](#what-we-claim-and-what-we-dont)).

> **Built during the hackathon.** Everything in this repository was written between Oct 26 and
> Nov 4 2026. [`WHAT-WE-BUILT.md`](WHAT-WE-BUILT.md) lists every module with the pull request
> that added it, and every reused library or model with its licence.

## What it does

1. **Describe it.** The owner types or says what worries them ("keep people 2 m from a moving
   forklift"). Muse Spark plans the job: what to watch, which camera covers it, and what a camera
   *cannot* check. The feasibility screen says so before anything goes live.
2. **Plan it.** The planner looks at the scene with SAM 3.1, measures real distances, and writes
   the rule in a typed rule language (Rule IR). It shows the plan, what changed since the last
   approved plan, and every check it passed.
3. **Prove it.** Rule CI attacks every rule with edge-case scenes and broken copies of the rule
   (mutants) before the owner can approve it. By default, a rule that fails Rule CI cannot go
   live. In prototype mode (on in the hackathon deployment) a failed check is shown as an amber
   caveat instead of blocking the rule.
4. **Trust it.** Before anyone is alerted, Muse Glimmer looks at a strip of frames from the event
   and says yes, no or unsure (Tier 1). It runs on our own serverless GPU. A second check by Muse
   Spark Open (Tier 2) and a Llama Prompt Guard 2 firewall for text in the scene are built, but
   not live in this deployment (see the [Model map](#model-map)). Every event, verdict, action
   and acknowledgement goes into a hash-chained log with a daily Merkle root. Anyone can check it
   in the browser, with no server.

## How we use Meta models, beyond a basic call

Taken from the [Model map](#model-map). **Live** means the deployed app calls it today.

- **Muse Spark plans and writes typed rules (Live).** It turns a plain sentence into a plan,
  asks clarifying questions, grounds each rule in a standard (OSHA, FDA Food Code), and compiles
  it into Rule IR that a rule engine runs. It is an agent with a fixed set of steps: survey,
  probe, calibrate, compile, check, repair.
- **Muse Spark answers questions about what the cameras saw (Live, prototype mode).** The Ask
  agent runs a tool loop: SQL over SAM 3.1 tracks, SAM 3.1 counts on recorded frames, Glimmer
  looking at frames, and Spark 1.2 watching the recorded clip as video.
- **Muse Spark searches the web, and we check every source.** For site context (weather,
  events, food recalls) an item is kept only if its source is a page the search really returned.
  Built and run live from a laptop on 3 Nov; not yet on the deployed stack.
- **Muse Spark watches video of crowded moments.** Each crowded spell is sent as a clip for
  surges, people down or blocked exits; the video check can only add reasons to escalate. Same
  status as the web search.
- **Muse Glimmer, open weights, on our own GPU (Live).** The Tier 1 verifier runs in
  llama-server on a RunPod serverless RTX 4090. It looks at evidence strips before anyone is
  alerted. The planner also has Glimmer translate each rule back into plain words, so the owner
  can check it says what they meant.
- **SAM 3.1 (Live).** Survey and detectability probes in the planner, keyframe tracking on our
  own serverless GPU (with the hosted API as fallback), and crop-zoom on heads and hands to find
  small worn items.
- **Muse Voice Transcribe (Live)** for spoken requests.
- **Built, not live yet:** Muse Spark Open as Tier 2 and plan auditor (no Baseten key), Muse
  Image scenes for Rule CI part 2 (switched off in the app), Llama Prompt Guard 2 firewall
  (waiting for gated-model access).
- **Coming in this build:** Muse Code writes new rule types (**in progress**, not merged).

## Documents

| File | Purpose |
|---|---|
| [`docs/TASKS.md`](docs/TASKS.md) | Full team task book: rules, contracts, gates, every task |
| [`docs/tasks/P1.md`](docs/tasks/P1.md) … [`P4.md`](docs/tasks/P4.md) | Per-person task books |
| [`docs/master-plan.md`](docs/master-plan.md) | Product plan (v3): proof stack, use cases, evaluation, video |
| [`docs/v2-design.md`](docs/v2-design.md) | Engine internals: Rule IR, perception, Rule CI, verifier, privacy |
| [`docs/jepa.md`](docs/jepa.md) | Surprise Lens (V-JEPA 2 world model) |
| [`docs/site-advisor.md`](docs/site-advisor.md) | Site Advisor: business advice you can check |
| [`docs/DEMO.md`](docs/DEMO.md) | The 3-minute live demo script and the golden REPLAY |
| [`docs/deploy.md`](docs/deploy.md) | Deployment: Vercel UI, RunPod API, Glimmer and SAM pods |
| [`WHAT-WE-BUILT.md`](WHAT-WE-BUILT.md) | What we wrote during the hackathon vs. what we reused, with licences |
| [`evidence/`](evidence/README.md) | Screenshots and verify-page captures behind our claims |
| [`eval/RESULTS.md`](eval/RESULTS.md) | Measured evaluation results |

## Quick start

```bash
make setup        # uv sync + pre-commit hooks
cp .env.example .env
make check        # lint + types + tests + coverage floors — must pass before every push
uv run rulecam version
```

Requires Python 3.12 (via `uv`), `ffmpeg`, and Node 20+ for the UI.

### Run it locally

```bash
cd ui && npm ci && npm run dev          # the web UI on http://localhost:3000
```

- **Without keys:** open `http://localhost:3000/demo?demo=1`. It replays a recorded live run
  (labelled REPLAY in the header) with no model calls. Nothing in it is hand-written.
- **Live:** copy `.env.example` to `.env`, fill in the Meta Model API key and the Glimmer and SAM
  endpoints, then follow [`docs/deploy.md`](docs/deploy.md). The header badge reads LIVE.
- **Check an evidence log:** `uv run rulecam audit <YYYY-MM-DD> <site>` re-verifies a day's chain and
  re-derives every decision; or drop the chain file on the browser verify page, which recomputes every SHA-256 and the Merkle root itself.

## How we work

Code, pull requests and **CI all live on GitHub** (GitHub Actions, `.github/workflows/ci.yml`: the
`check`, `guardrails` and `ml-cpu` jobs). Every task ends in a pull request with all
checks green; see `docs/TASKS.md` §0 and §3.4, and `CLAUDE.md`.

## Architecture

```mermaid
flowchart LR
  owner([Owner]) -- "plain-language request" --> planner["Planner<br/>Muse Spark"]
  planner -- "survey and probes" --> samh["SAM 3.1<br/>hosted API"]
  planner --> ci["Rule CI<br/>boundary scenes, mutants"]
  ci --> approve{"Owner approves<br/>the plan"}
  phone([Phone or camera]) -- "3 s H.264 chunks" --> api["FastAPI runtime<br/>(RunPod CPU pod)"]
  approve --> api
  api -- "keyframes" --> sam["SAM 3.1<br/>serverless GPU"]
  sam --> tracks[("Track store<br/>BoT-SORT people, ByteTrack objects")]
  tracks --> engine["Rule engine<br/>typed Rule IR"]
  engine -- "candidate + evidence strip" --> glimmer["Muse Glimmer<br/>Tier 1, serverless GPU"]
  glimmer --> policy["Verification policy"]
  policy --> actions["Alerts and actions<br/>(dry-run by default)"]
  policy --> chain[("Hash-chained<br/>evidence log")]
  chain --> verify["Browser verify page<br/>Merkle root"]
  owner -- "questions" --> ask["Ask agent<br/>Muse Spark"]
  ask --> tracks
  ask --> sam
  ask --> glimmer
  sam -- "/embed crops" --> ids["Identities: OSNet-AIN people,<br/>DINOv2 objects (prototype mode)"]
  ids --> ask
```

## Model map

Which model does which job, where it runs, and how far it is wired today. Three words are used:
**Live** means the deployed app calls it. **Library** means the code is built and tested, but the
live app does not call it yet. **Planned** means it is not built.

| Meta model | Job | Runs where | Status |
|---|---|---|---|
| Muse Spark 1.3 (+1.2 for narrated video) | Planner, compiler, standards grounding, Ask agent | Hosted API | Live in the Planner (P2-01, P2-06) and in `POST /ask`, where Spark 1.2 can also watch a joined clip of the window it picks (`watch`, add-on 2.8). Site tools (in the app, run live from a laptop on 3 Nov, not yet on the deployed stack): `/context` uses its **web search** with the raw results returned, and keeps an item only if its source is a page the search returned; `/crowd` has it **watch the clip** of each crowded spell; `/shelf` uses its **image understanding** to find shelf gaps |
| Muse Voice Transcribe | Spoken requests, disputes, voice round-trip check | Hosted API | Live for spoken requests (P2-02); voice disputes are library (P2-S2), not yet passed to the worker router |
| SAM 3.1 | Survey, probes, tracking, coverage, masks | Hosted API; self-hosted on a RunPod serverless GPU (RTX 4090) | Live: hosted client (P1-02) for survey, probes and Rule CI; the runtime sends keyframes to our self-hosted SAM 3.1 (`SAM_BASE_URL`, a RunPod serverless load balancer since 2 Nov; a pod or Modal also work) and falls back to hosted (P1-03). Crop-zoom (add-on 3.3) re-runs it on enlarged head and hand crops for worn items, at probe time and as Tier 0 before Glimmer. Site tools: its person tracks give crowd density in people per m² (`/crowd`), and it confirms Spark's shelf gaps hold no product package (`/shelf`) |
| Muse Glimmer 30B (open weights) | Tier 1 verifier, back-translation, recompute, Ask's eyes | llama-server on a RunPod serverless GPU (RTX 4090); a site laptop or pod also work | Live as Tier 1 (P3-03). By default it sees 6 face-blurred keyframes at 640 px (P4-05); the deployed **prototype mode** sends 8 raw frames at 960 px (`RULECAM_STRIP_FRAMES=8`, `RULECAM_STRIP_MAX_WIDTH=960`) |
| Muse Spark Open (open weights) | Tier 2 verifier, dispute review, plan audit, devil's advocate | Baseten | Wired for plan audit, dispute review (P2-07, P2-11) and the review of each Ask answer (add-on 2.7); Tier 2 in the runtime is library (P3-04). The hackathon deployment has no working Baseten key yet, so these calls are skipped and logged (`plan_audit_skipped`) |
| Muse Image | Rule CI scenes, lookalikes, boundary pairs | Hosted API | Library: Rule CI part 2 (P2-09, #71) is built but switched off in the app, which runs part 1 only |
| Sapiens (FAIR) | Pose and skeletons | GPU | Planned. Meanwhile RTMPose-l body7 (OpenMMLab, ONNX on the API pod's CPU) draws each person's pose into the evidence strips (add-on 3.4, `RULECAM_POSE_ONNX`) |
| MMS-TTS (FAIR) | Spoken warnings | CPU | Library: pre-render with round-trip check (P1-11). The runtime's actions are wired (dry-run by default) and record the `speak` action, e.g. "would play the warning (es)" on 1 Nov, but no speaker or pre-rendered phrase book is connected in `runtime/response.py`, so nothing is spoken yet |
| V-JEPA 2 (FAIR) | Surprise Lens world model | GPU | Planned (gated) |
| Llama Prompt Guard 2 (86M) | Scene-text firewall | CPU (transformers, `ml` extra) | Wired into the runtime's verification (P3-05, #105); it needs `HF_TOKEN` and the `ml` extra, and without them the runtime reports the firewall as degraded. In the hackathon deployment it is degraded until the token's account is granted the gated model |
| DINOv2 (FAIR) | Joins an object's track fragments into one identity for Ask | `/embed` on the SAM serverless GPU | Live in prototype mode only (`ask/identity.py`). People use OSNet-AIN instead (below), and DINOv2 again if the GPU has no re-ID model |

Other models in the pipeline, picked on quality and speed: **OSNet-AIN** person re-ID vectors
(BoxMOT, on the SAM GPU) feed **BoT-SORT**, which tracks people between SAM keyframes, and Ask's
person identities (add-ons 3.1 and 3.2, prototype mode only, body crops, never faces); objects
stay on ByteTrack. In the hackathon deployment people are on ByteTrack too
(`RULECAM_PERSON_TRACKER=bytetrack`) until the GPU volume carries the fix for OSNet under SAM's
bfloat16 autocast; Ask's identities use OSNet-AIN already. **MoGe-2** (Microsoft, on the SAM
GPU) gives the planner a metric ground calibration from one frame (add-on 3.5).

## Results

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

## What we claim, and what we don't

- **We claim** what `eval/RESULTS.md`, the golden runs and `evidence/` show, and nothing more.
  Every number in the UI and in the video comes from a run you can replay. The hand-made sample
  data used to build the cockpit screens (`scripts/seed_live_backend.py`) is not a run, and we
  never quote it as a result.
- **REPLAY is never shown as LIVE.** The header badge says which one you are watching, and it
  never switches silently.
- **The verifier can say no.** Glimmer and Spark Open can reject a candidate; safety-critical
  rules fail towards alerting a human, never towards silence.
- **Rulecam is not a safety system of record.** It helps people watch; it does not replace a
  guard, a lock-out or a supervisor. Interlocks (Kasa plugs) only ever stop equipment, and only a
  human can resume it.
- **No face recognition, ever.** In the default modes faces are blurred in evidence strips, the
  anonymous and strict privacy modes keep no person pixels at all, and nobody is re-identified.
- **Prototype mode is different, and the hackathon deployment runs it**
  (`RULECAM_PROTOTYPE_MODE=true`, `src/rulecam/prototype.py`): the planner declines only
  requests that single people out by race, ethnicity, skin colour, religion, nationality,
  immigration status or sexual orientation, and compiles every other concern; every compiled
  rule activates, with failed acceptance and Rule CI checks shown as amber caveats; Glimmer sees
  the raw, unblurred frames; the verifier's verdict is final (a rejection is suppressed whatever
  the rule's severity, so the "fail towards alerting a human" point above holds only outside
  prototype mode); and `POST /ask` **re-identifies people** across track
  fragments by appearance (OSNet-AIN re-ID vectors of body crops, plus a short Glimmer note on
  clothing) so an owner can ask about visitors and dwell time. The tracker uses the same vectors
  to keep a person's track between keyframes (BoT-SORT). It never uses faces and never names
  anyone.
  Prototype mode is off by default and in CI.

## Licences

The code in this repository was written during the hackathon. Third-party components are used as
dependencies under their own licences; see [`WHAT-WE-BUILT.md`](WHAT-WE-BUILT.md).
Llama Prompt Guard 2 is used under the Llama 4 Community License: **Built with Llama**.
