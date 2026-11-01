# Rulecam

**Tell Rulecam what worries you. It works out what to watch, tells you what a camera can't check,
proves every rule before it goes live, checks every alert twice, and lets your crew check it.**

*Describe it. Plan it. Prove it. Trust it.*

Built for the Meta Global AI Developer Hackathon (build window Oct 26 – Nov 4 2026).

> **Built with Muse Spark Open.** · **Built with Llama** (Llama Prompt Guard 2).

## What it does

1. **Describe it.** The owner says or types what worries them ("keep people 2 m from a moving
   forklift"). Muse Spark plans the job: what to watch, which camera covers it, and what a camera
   *cannot* check (the feasibility screen says so before anything goes live).
2. **Plan it.** The planner surveys the scene with SAM 3.1, calibrates distances, compiles a typed
   Rule IR and shows the plan, the diff against the last approved plan, and the acceptance trace.
3. **Prove it.** Rule CI runs every rule against generated boundary scenes and mutants before the
   owner can approve it. A rule that fails Rule CI cannot go live.
4. **Trust it.** Every candidate alert is checked twice: Muse Glimmer (Tier 1, on the site laptop)
   and Muse Spark Open (Tier 2) for safety-critical or disputed cases, behind a Llama Prompt
   Guard 2 firewall so text in the scene can never instruct the verifier. Every event, verdict,
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

## Model map

Only models that actually run in the product are listed; this table is updated as features land.

| Meta model | Job | Runs where | Status |
|---|---|---|---|
| Muse Spark 1.3 (+1.2 for narrated video) | Planner, compiler, standards grounding | Hosted API | integrated (P2-01) |
| Muse Voice Transcribe | Spoken requests, disputes, voice round-trip check | Hosted API | integrated (P2-01) |
| SAM 3.1 | Survey, probes, tracking, coverage, masks | Hosted + self-hosted | integrated (P1-03, P1-04) |
| Muse Glimmer 30B (open weights) | Tier 1 verifier, back-translation, recompute | Site laptop (llama-server) | client + Tier 1 verifier (P3-03) |
| Muse Spark Open (open weights) | Tier 2 verifier, dispute review, plan audit, devil's advocate | Baseten | client + Tier 2 (P3-04) |
| Muse Image | Rule CI scenes, lookalikes, posters | Hosted API | client (P2-01); Rule CI scenes and lookalikes (P2-09) |
| Sapiens (FAIR) | Pose and skeletons | GPU | planned; RTMPose (ONNX, CPU) is the pose path for now (P4-04) |
| MMS-TTS (FAIR) | Spoken warnings, pre-rendered and checked by a Voice Transcribe round trip | CPU (`ml` extra) | integrated (P1-11) |
| V-JEPA 2 (FAIR) | Surprise Lens world model | GPU | planned (gated) |
| Llama Prompt Guard 2 (86M) | Scene-text firewall | CPU (transformers, `ml` extra) | firewall (P3-05) |

## Results

Measured results are in [`eval/RESULTS.md`](eval/RESULTS.md). A table appears there only after
it was measured; a target with no measured number has not been run yet.

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
- **No face recognition, ever.** Faces are blurred in evidence strips; the anonymous and strict
  privacy modes keep no person pixels at all.

## Licences

The code in this repository was written during the hackathon. Third-party components are used as
dependencies under their own licences; see [`WHAT-WE-BUILT.md`](WHAT-WE-BUILT.md).
Llama Prompt Guard 2 is used under the Llama 4 Community License: **Built with Llama**.
