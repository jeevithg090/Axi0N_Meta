# Rulecam Project Handoff & Session State

> **Last Updated**: 2026-11-01 06:58:00 (Local Time)  
> **Repository**: [https://github.com/jeevithg090/Axi0N_Meta](https://github.com/jeevithg090/Axi0N_Meta)  
> **Local Workspace**: `C:\Users\nav6i\.gemini\antigravity\scratch\Axi0N_Meta`  
> **User Persona**: Nava (`@nav6x`), Role **P2** (Planner, Rule CI & Advice Pipeline) in 4-person team.  
> **Teammates**: `@jeevithg090` (P1 / Team Lead), `@nav6x` (P2), `@R3unakk` (P3 / Reviewer for P2), `@aris` (P4).

---

## 1. Key Deadlines & Hackathon Timeline

- **Event**: Meta Global AI Developer Hackathon (Cerebral Valley / Devpost).
- **Feature Freeze**: **Monday Nov 2, 2026 at 12:00 ET** (~24 hours remaining).
- **Submission Deadline**: **Wednesday Nov 4, 2026 at 12:00 ET**.
- **Credit Claims Form**: Closes **Wednesday Nov 4, 2026 at 21:00 ET**.

---

## 2. P2 Status: 100% Completed & Merged into `main`

All Must-tier, Should-tier, Shared Duties, and Stretch goals assigned to P2 have been fully implemented, tested, CI-verified, and squash-merged into `main`:

### A. Must-Tier Tasks (P2-01 through P2-12)
- **P2-01**: Hosted Meta API clients (`Spark 1.3/1.2`, `Voice Transcribe`, `Muse Image`) with 150 RPM rate limiter and token bucket.
- **P2-02**: Multimodal Intake (photo, Spanish/English audio transcription with diarization, multi-page inspection report PDF).
- **P2-03**: Rule IR Compiler & Guardrail Declines (honest refusals for non-observable concepts like temperature and illness policy).
- **P2-04**: Camera Survey, SAM 3.1 Detectability Probes, and Wilson score Feasibility Gauge.
- **P2-05**: Interactive Clarifying Interview and Regulatory Standards Grounding (FDA Food Code, OSHA, ISO).
- **P2-06**: Fixed-Step Planner Pipeline (`survey` → `probe` → `gauge` → `interview` → `compile` → `acceptance`), Plan Linter, and 2-Round Repair Loop.
- **P2-07**: Spark Open Plan Audit and Glimmer Back-Translation (`English` ↔ `Spanish`).
- **P2-08**: Rule CI: Boundary Traces & AST Mutation Testing (`generate_mutations`, `generate_rule_traces`).
- **P2-09**: Rule CI: Muse Image Detectability Scenes, Lookalike Red-Team, Prompt Injection Sign Composites (D1–D3), and Poisson Alert Budget Forecasting.
- **P2-10**: Rule Cards & Declined Cards SVG/PNG Generation with Playwright renderer.
- **P2-11**: Worker Rules Transparency Page & Tap-to-Dispute Flow.
- **P2-12**: Site Advisor Reasoning Pipeline & Glimmer-Verified Advice Cards.

### B. Should-Tier Tasks
- **P2-S1**: Self-Repair Loop (automated plan recovery from acceptance check failures).
- **P2-S2** ([PR #61](https://github.com/jeevithg090/Axi0N_Meta/pull/61)): Worker Voice Dispute Flow with immediate zero-audio disposal, plus Two-Key Protected Privacy Settings Manager (`anonymous_mode`, `privacy_mode`, `retention_days`, `no_individual_metrics`).
- **P2-S3**: Kitchen Pass-Window Station Type Grounding.

### C. Shared Duties (Section 11)
- **S-01** ([PR #64](https://github.com/jeevithg090/Axi0N_Meta/pull/64)): Physical floor plans, props, and camera layout in [`docs/sets.md`](docs/sets.md).
- **S-04 / W-10** ([PR #63](https://github.com/jeevithg090/Axi0N_Meta/pull/63), [PR #121](https://github.com/jeevithg090/Axi0N_Meta/pull/121), [PR #126](https://github.com/jeevithg090/Axi0N_Meta/pull/126), [PR #127](https://github.com/jeevithg090/Axi0N_Meta/pull/127)): Real PlannerPipeline wired into E3 runner, positive outside-author validation, safe fallback defaults, and dataset ingested with verified outside authors across 5 domains in eval/heldout/requests.jsonl.
- **S-08** ([PR #67](https://github.com/jeevithg090/Axi0N_Meta/pull/67)): 3-stage social launch copy and reply protocol in [`docs/social.md`](docs/social.md).
- **S-10** ([PR #69](https://github.com/jeevithg090/Axi0N_Meta/pull/69), [PR #74](https://github.com/jeevithg090/Axi0N_Meta/pull/74)): Daily standup logs, gate readiness ledger (gate statuses corrected Oct 31), and pre-freeze checklist in [`docs/standups.md`](docs/standups.md).

### D. P2 Stretch Goal
- **Boundary-Pair Muse Image Edits** ([PR #71](https://github.com/jeevithg090/Axi0N_Meta/pull/71)): Chained turn-2 edits with `previous_response_id` preserving background confounders, planar homography ground metric projection (`project_pixel_to_ground`), and automated pass/fail suite runner (`src/rulecam/rule_ci/boundary_pairs.py`).

### E. Team Review Protocol
- **PR #72 Reviewed & Approved**: P2 reviewed and approved P1's PR #72 (*Reference object probes for metric rules*).
- **Issue #33 Approved**: P2 reviewed and approved P3's contract change (*`reasoning_ref` in `VerifierVerdict`*).

---

## 3. $150 Meta Model API Credit Claim Status

- **Status**: **SUBMITTED & CONFIRMED** on Nov 1, 2026.
- **Full Name**: Navadarshanth Shanmugasundaram
- **Email**: `nav6ind@gmail.com` (no dot, per Google Form credit claim requirement)
- **Team ID**: `1097704756498249`
- **Meta Billing URL**: [https://dev.meta.ai/billing?team_id=1097704756498249](https://dev.meta.ai/billing?team_id=1097704756498249)
- **ETA**: 1 to 2 business days (distributed in batches; expect Mon Nov 2 or Tue Nov 3).
- **Action Required on Arrival**:
  1. Check Billing page for the `$150.00 Free Credits` balance.
  2. Navigate to [dev.meta.ai/api-keys](https://dev.meta.ai/api-keys?team_id=1097704756498249) and click **Create Secret Key**.
  3. Create `.env` in repository root and set:
     ```bash
     RULECAM_MODE=live
     META_API_KEY=<your_new_key>
     META_API_BASE_URL=https://api.meta.ai/v1
     ```
  4. Run `.venv\Scripts\python.exe -m rulecam.replay.preflight` to run live smoke tests.

---

## 4. Current State of Repository & Teammates

- **Active Branch**: `main` (clean working tree, fully synced with `origin/main`).
- **Open PRs**: `0` (all merged).
- **Open Issues**:
  - `#5`: `[P1-02] Verify HostedSam31 wire-format assumptions against live SAM 3.1 API` (waiting on live `META_API_KEY`).
  - `#13`: `[P1-03] Deploy self-hosted SAM 3.1 on Modal` (P1 task, needs Hugging Face & Modal GPU access).
  - `#14`: `[P1-03] Route ModalSam31 through replay cache and unify model_used payload` (P1 task).
  - `#18`: `spec-conflict: P1-10 needs a 'cancelled' candidate status` (workaround merged in PR #19).
  - `#32`: `[P3-03] Measure Glimmer latency on 6-frame strip` (P3 task).
  - `#33`: `contract-change: add reasoning_ref to VerifierVerdict` (P3 task, approved by P2).
  - `#34`: `[P3-04] Live Spark Open call on Baseten: reasoning and screenshot` (P3 task).

---

## 5. How to Resume in a Fresh Session

When you open a new session, follow these steps:

1. **Verify Git Sync**:
   ```powershell
   cd C:\Users\nav6i\.gemini\antigravity\scratch\Axi0N_Meta
   git checkout main
   git pull origin main
   git status
   ```
2. **Check for Open PRs or Issues**:
   ```powershell
   gh pr list --state open
   gh issue list --state open
   ```
3. **Recommended Next High-Impact Tasks**:
   - **Task 1: Update `README.md` & `WHAT-WE-BUILT.md`**:
     Upgrade the public README from day-1 scaffold text to showcase the full, integrated system, model map, and real E3 results (once the runner calls the Planner on outsider requests) for the judges.
   - **Task 2: Golden Runs & Evidence (S-05, Lead: P3)**:
     Assist P3 with recording golden runs (`scenarios/*.expected.json`) and Glimmer verification traces if needed.
   - **Task 3: Video Shoot Coordination (S-07, Lead: P4)**:
     Review video recording progress against the physical setups in `docs/sets.md` and cue markers in `docs/social.md`.
   - **Task 4: Check Credits & Run Preflight**:
     Once Meta credits land, add `META_API_KEY` to `.env` and run:
     ```powershell
     .venv\Scripts\python.exe -m rulecam.replay.preflight
     ```
