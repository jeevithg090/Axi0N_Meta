# Rulecam

**Tell Rulecam what worries you. It works out what to watch, tells you what a camera can't check,
proves every rule before it goes live, checks every alert twice, and lets your crew check it.**

*Describe it. Plan it. Prove it. Trust it.*

Built for the Meta Global AI Developer Hackathon (build window Oct 26 – Nov 4 2026).

> **Built with Muse Spark Open.** · **Built with Llama** (Llama Prompt Guard 2).

## Status

Scaffold, shared contracts and CI are in place. Feature work follows the task books:

| File | Purpose |
|---|---|
| [`docs/TASKS.md`](docs/TASKS.md) | Full team task book: rules, contracts, gates, every task |
| [`docs/tasks/P1.md`](docs/tasks/P1.md) … [`P4.md`](docs/tasks/P4.md) | Per-person task books |
| [`docs/master-plan.md`](docs/master-plan.md) | Product plan (v3): proof stack, use cases, evaluation, video |
| [`docs/v2-design.md`](docs/v2-design.md) | Engine internals: Rule IR, perception, Rule CI, verifier, privacy |
| [`docs/jepa.md`](docs/jepa.md) | Surprise Lens (V-JEPA 2 world model) |
| [`docs/site-advisor.md`](docs/site-advisor.md) | Site Advisor: business advice you can check |

## Quick start

```bash
make setup        # uv sync + pre-commit hooks
cp .env.example .env
make check        # lint + types + tests + coverage floors — must pass before every push
uv run rulecam version
```

Requires Python 3.12 (via `uv`), `ffmpeg`, and Node 20+ once the UI lands.

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
| SAM 3.1 | Survey, probes, tracking, coverage, masks | Hosted + self-hosted | planned |
| Muse Glimmer 30B (open weights) | Tier 1 verifier, back-translation, recompute | Site laptop (llama-server) | client + Tier 1 verifier (P3-03) |
| Muse Spark Open (open weights) | Tier 2 verifier, dispute review, plan audit, devil's advocate | Baseten | client + Tier 2 (P3-04) |
| Muse Image | Rule CI scenes, lookalikes, posters | Hosted API | client (P2-01); Rule CI scenes and lookalikes (P2-09) |
| Sapiens (FAIR) | Pose and skeletons | GPU | planned; RTMPose (ONNX, CPU) is the pose path for now (P4-04) |
| MMS-TTS (FAIR) | Spoken warnings | CPU | planned |
| V-JEPA 2 (FAIR) | Surprise Lens world model | GPU | planned (gated) |
| Llama Prompt Guard 2 (86M) | Scene-text firewall | CPU (transformers, `ml` extra) | firewall (P3-05) |

## Results

Evaluation tables (E1–E11) will be published in `eval/RESULTS.md`.
