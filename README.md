# Rulecam

**Tell Rulecam what worries you. It works out what to watch, tells you what a camera can't check,
proves every rule before it goes live, has a second model check every alert, and lets your crew
check it.**

*Describe it. Plan it. Prove it. Trust it.*

Built for the Meta Global AI Developer Hackathon (build window Oct 26 – Nov 4 2026).

> **Built with Muse Spark Open.** · **Built with Llama** (Llama Prompt Guard 2).

## For judges: the 2-minute path

- **Video:** _link added at submission_ (placeholder).
- **Try it, no login, no keys:** [rulecam.vercel.app/demo?demo=1](https://rulecam.vercel.app/demo?demo=1)
  replays a recorded live run with no model calls. The header says REPLAY; it works with every
  GPU switched off.

| Time | Open | What to look for |
|---|---|---|
| 0:00 | [`/demo?demo=1`](https://rulecam.vercel.app/demo?demo=1) | The owner's plain-language request, the planner's steps, the compiled rule and its acceptance trace |
| 0:30 | the Rule CI section of the same page | The rule was attacked with boundary scenes and mutants before anyone could approve it, re-run offline in CI |
| 0:50 | **Verify it in your browser** | Your browser recomputes every SHA-256 of the evidence chain and its Merkle root; nothing is trusted from us |
| 1:20 | [Results](#results) below, then [`reports/eval/`](reports/eval/) | Live runs on real video and on hand-labelled CCTV, every number tied to its run, date and commit |
| 1:45 | [Architecture](#architecture) | Which Meta model does which job, and where it runs |

Prototype mode is on in the hackathon deployment (see [What we claim](#what-we-claim-and-what-we-dont)).

## What it does

1. **Describe it.** The owner says or types what worries them ("keep people 2 m from a moving
   forklift"). Muse Spark plans the job: what to watch, which camera covers it, and what a camera
   *cannot* check (the feasibility screen says so before anything goes live).
2. **Plan it.** The planner surveys the scene with SAM 3.1, calibrates distances, compiles a typed
   Rule IR and shows the plan, the diff against the last approved plan, and the acceptance trace.
3. **Prove it.** Rule CI runs every rule against generated boundary scenes and mutants before the
   owner can approve it. A rule that fails Rule CI cannot go live.
4. **Trust it.** Every candidate alert is verified by Muse Glimmer (Tier 1, on the site laptop),
   and safety-critical or disputed cases get a second check by Muse Spark Open (Tier 2). Text in
   the scene is screened by a Llama Prompt Guard 2 firewall before it reaches the verifier. Every event, verdict,
   action and acknowledgement goes into a hash-chained, Merkle-rooted log that anyone can check in
   the browser with no server.

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
  sam --> tracks[("Track store<br/>ByteTrack, Parquet")]
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
  sam -- "/embed crops" --> dino["DINOv2 identities<br/>(prototype mode)"]
  dino --> ask
```

## Model map

Each status says exactly how far the model is wired today. **Live** means the deployed app calls it;
**library** means the code is built and tested but the live runtime does not call it yet.

| Meta model | Job | Runs where | Status |
|---|---|---|---|
| Muse Spark 1.3 (+1.2 for narrated video) | Planner, compiler, standards grounding | Hosted API | Live in the Planner (P2-01, P2-06) |
| Muse Voice Transcribe | Spoken requests, disputes, voice round-trip check | Hosted API | Live for spoken requests (P2-02); voice disputes are library (P2-S2), not yet passed to the worker router |
| SAM 3.1 | Survey, probes, tracking, coverage, masks | Hosted API; self-hosted on a RunPod serverless GPU (RTX 4090) | Live: hosted client (P1-02) for survey, probes and Rule CI; the runtime sends keyframes to our self-hosted SAM 3.1 (`SAM_BASE_URL`, a RunPod serverless load balancer since 2 Nov; a pod or Modal also work) and falls back to hosted (P1-03) |
| Muse Glimmer 30B (open weights) | Tier 1 verifier, back-translation, recompute, Ask's eyes | llama-server on a RunPod serverless GPU (RTX 4090); a site laptop or pod also work | Live as Tier 1 (P3-03). By default it sees 6 face-blurred keyframes at 640 px (P4-05); the deployed **prototype mode** sends 8 raw frames at 960 px (`RULECAM_STRIP_FRAMES=8`, `RULECAM_STRIP_MAX_WIDTH=960`) |
| Muse Spark Open (open weights) | Tier 2 verifier, dispute review, plan audit, devil's advocate | Baseten | Live for plan audit and dispute review (P2-07, P2-11); Tier 2 in the runtime is library (P3-04) |
| Muse Image | Rule CI scenes, lookalikes, boundary pairs | Hosted API | Library: Rule CI part 2 (P2-09, #71) is built but switched off in the app, which runs part 1 only |
| Sapiens (FAIR) | Pose and skeletons | GPU | Planned; RTMPose (ONNX, CPU) pose features and estimator are library code (P4-04), unverified on real weights and not yet called by the runtime |
| MMS-TTS (FAIR) | Spoken warnings | CPU | Library: pre-render with round-trip check (P1-11). The runtime's actions are wired (dry-run by default) and record the `speak` action, e.g. "would play the warning (es)" on 1 Nov, but no speaker or pre-rendered phrase book is connected in `runtime/response.py`, so nothing is spoken yet |
| V-JEPA 2 (FAIR) | Surprise Lens world model | GPU | Planned (gated) |
| Llama Prompt Guard 2 (86M) | Scene-text firewall | CPU (transformers, `ml` extra) | Wired into the runtime's verification (P3-05, #105); it needs `HF_TOKEN` and the `ml` extra, and without them the runtime reports the firewall as degraded |
| DINOv2 (FAIR) | Joins a person's track fragments into one identity for Ask (dwell time, visitors) | `/embed` on the SAM serverless GPU | Live in prototype mode only (`ask/identity.py`): body crops, never faces |

## Results

<!-- BEGIN RESULTS (generated by `uv run rulecam eval results`; do not edit) -->

Measured on the deployed stack and on public hand-labelled footage. Each row is read from the result file it cites by `uv run rulecam eval results`, with its run, date and the commit that recorded it; a test fails if a number here is not in that file. Rule accuracy on labelled clips, the verifier against human labels, tracking scores and serverless latency are not measured yet: see [`eval/RESULTS.md`](eval/RESULTS.md) and [`eval/README.md`](eval/README.md).

| Measured | Value | Run | Date | Git SHA | Source |
|---|---|---|---|---|---|
| Distance measured, worker passing a moving forklift | 2.90 m | live test campaign, run 2: T02 | 2026-11-01 | `f738f39` | [`2026-11-01-live-test-campaign.json`](reports/eval/2026-11-01-live-test-campaign.json) |
| Glimmer P(violation), worker inside the 4 m rule (verified alert) | 0.99 | live test campaign, run 2: T02 | 2026-11-01 | `f738f39` | [`2026-11-01-live-test-campaign.json`](reports/eval/2026-11-01-live-test-campaign.json) |
| Event to verified decision | 19.0 s | live test campaign, run 2: T02 | 2026-11-01 | `f738f39` | [`2026-11-01-live-test-campaign.json`](reports/eval/2026-11-01-live-test-campaign.json) |
| Glimmer P(violation), same rule asked in Spanish (warning queued in Spanish) | 0.98 | live test campaign, run 2: T03 | 2026-11-01 | `f738f39` | [`2026-11-01-live-test-campaign.json`](reports/eval/2026-11-01-live-test-campaign.json) |
| Glimmer P(violation), seated driver flagged by the engine (rejected, no alert) | 0.05 | live test campaign, run 2: T04 | 2026-11-01 | `f738f39` | [`2026-11-01-live-test-campaign.json`](reports/eval/2026-11-01-live-test-campaign.json) |
| Request to plan ready (planner, live models) | 133.6 s | live test campaign, run 2: T02 | 2026-11-01 | `f738f39` | [`2026-11-01-live-test-campaign.json`](reports/eval/2026-11-01-live-test-campaign.json) |
| SAM 3.1 per 3 s chunk, p95 (self-hosted GPU) | 2.07 s | live test campaign, run 2: T02 | 2026-11-01 | `f738f39` | [`2026-11-01-live-test-campaign.json`](reports/eval/2026-11-01-live-test-campaign.json) |
| Seconds with the exact people count (real CCTV, hand labels) | 22.7% | CAVIAR corridor, phone-68 | 2026-11-02 | `85a63bd` | [`2026-11-02-corridor-ground-truth.json`](reports/eval/2026-11-02-corridor-ground-truth.json) |
| Seconds with the count within one person | 53.4% | CAVIAR corridor, phone-68 | 2026-11-02 | `85a63bd` | [`2026-11-02-corridor-ground-truth.json`](reports/eval/2026-11-02-corridor-ground-truth.json) |
| Peak people at once, system vs labels | 10 vs 8 | CAVIAR corridor, phone-68 | 2026-11-02 | `85a63bd` | [`2026-11-02-corridor-ground-truth.json`](reports/eval/2026-11-02-corridor-ground-truth.json) |
| Person tracks and identities vs labelled visits | 416 tracks, 39 identities vs 80 visits | CAVIAR corridor, phone-68 | 2026-11-02 | `85a63bd` | [`2026-11-02-corridor-ground-truth.json`](reports/eval/2026-11-02-corridor-ground-truth.json) |
| Backfill of recorded chunks (tracked points in seconds) | 5301 points in 409 s | CAVIAR corridor, phone-68 | 2026-11-02 | `85a63bd` | [`2026-11-02-corridor-ground-truth.json`](reports/eval/2026-11-02-corridor-ground-truth.json) |
| Glimmer decisive before the strip renderer existed | 0 of 10 candidates | first recorded run (warehouse driver only) | 2026-10-31 | `79bd84a` | [`2026-10-31.json`](reports/eval/2026-10-31.json) |

<!-- END RESULTS -->

An earlier E3 figure came from a simulated harness and was withdrawn on Oct 31 (see
[`eval/RESULTS.md`](eval/RESULTS.md)).

## What we claim, and what we don't

- **We claim** what `eval/RESULTS.md`, the golden runs and `evidence/` show, and nothing more.
  Every number in the UI and in the video comes from a run you can replay.
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
  (`RULECAM_PROTOTYPE_MODE=true`, `src/rulecam/prototype.py`): Glimmer sees the raw, unblurred
  frames, the verifier's verdict is final, and `POST /ask` **re-identifies people** across track
  fragments by appearance (DINOv2 vectors of body crops, plus a short Glimmer note on clothing)
  so an owner can ask about visitors and dwell time. It never uses faces and never names anyone.
  Prototype mode is off by default and in CI.

## Licences

The code in this repository was written during the hackathon. Third-party components are used as
dependencies under their own licences; see [`WHAT-WE-BUILT.md`](WHAT-WE-BUILT.md).
Llama Prompt Guard 2 is used under the Llama 4 Community License: **Built with Llama**.
