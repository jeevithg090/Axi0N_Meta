# Team Standup Log & Gate Readiness Ledger (Task S-10, MP §14)

**Rotating Facilitator**: P1 (Jeevith), P2 (Nava), P3 (Raunak), P4 (Aris)
**Cadence**: Daily 15-minute syncs at 09:00 ET and 21:00 ET.

---

## 1. Integration Gates Summary

| Gate | Target Time | Criteria | Status | Verified In |
| :--- | :--- | :--- | :--- | :--- |
| **G0** | Fri Oct 30, 12:00 ET | Repo scaffold, CI, contracts v1.0.0, guardrail bans, branch protection. | **PASSED** | PR #1 (P3-01) |
| **G1** | Fri Oct 30, 23:00 ET | Vertical slice: Text request $\to$ Rule IR $\to$ SAM tracks $\to$ Glimmer verdict $\to$ Chained event. | **UNVERIFIED**: code merged, no recorded run ID | PR #24, #27 |
| **G2** | Sat Oct 31, 23:00 ET | UC1 Hero kitchen set end-to-end: Photo/PDF/Spanish voice intake $\to$ Survey/probes/feasibility $\to$ Plan with acceptance trace $\to$ Live handwash rule $\to$ Spanish MMS-TTS coaching $\to$ Evidence pack. | **PARTIAL**: intake, survey, probes and plan merged; the live rule, coaching and evidence pack are not wired (see corrections) | PR #24, #26, #27, #28 |
| **G3** | Sun Nov 1, 23:00 ET | • Rule CI complete (boundaries, mutations, lookalikes, prompt injection D1–D3)<br>• Site Advisor reasoning card with Glimmer recompute<br>• E3 held-out requests run ($\ge 80\%$ valid-or-honest rate). | **NOT MET**: Rule CI part 2 is off in the app; no advisor card (statistics missing); E3 was simulated | PR #30, #31, #47, #49, #63 |
| **Freeze** | Mon Nov 2, 12:00 ET | All must-work merged. No feature changes; only `fix-after-freeze` PRs permitted. | **AT RISK** | Pre-freeze audit |

### Corrections (Oct 31, task W-00)

A code audit on Oct 31 found that this ledger claimed more than `main` delivers. Gate statuses
above now record only what the code and recorded runs support; a gate is PASSED only with a run ID.

- **E3 was simulated.** `eval/runners/e3.py` never calls the Planner, so its 100.0% (60/60) result
  was true by construction, and the 20 requests were team-written personas rather than outsider
  requests. The figures, `eval/results/e3.json` and the 60 plans in `plans/heldout/` were
  withdrawn; see `eval/RESULTS.md`.
- **The live runtime does not act yet.** `runtime/factory.py` still wires `VerifyOnlyPolicy`,
  `NoActions` and `NoStrips`, so no coaching, interlock or alert fires and Tier 1 receives no
  frames. The handwash countdown kiosk and Spanish MMS-TTS coaching claimed for G2 are not
  functional; there is no `/station` page and `voice_out` is never called by the runtime.
- **Rule CI part 2** (lookalikes and injection signs, P2-09) is built but switched off in the app:
  `api/app.py` calls `prove_plan_hook(bus)` without a red team.
- **Site Advisor** runs on fakes only; its statistics (P4-08b) are not merged.
- **Standup 5** was written on Oct 31, before the Sunday meeting it describes.

---

## 2. Daily Standup Notes

### Standup 1 · Friday Oct 30, 09:00 ET (Kickoff)
- **Attendees**: P1, P2, P3, P4
- **Priorities**:
  - P1: Modal SAM 3.1 self-hosted loop and motion gate.
  - P2: P2-01 hosted Meta clients (Spark 1.3/1.2, Voice Transcribe, Muse Image), P2-02 multimodal intake, P2-03 rule compiler.
  - P3: Glimmer verifier local runner, Spark Open client, contract definitions.
  - P4: JEPA surprise lens setup on Modal, baseline synthetic clip generator.
- **Blockers / Resolutions**:
  - Blocker: Hugging Face gated access needed for Prompt Guard 2. Resolved by P3.

---

### Standup 2 · Friday Oct 30, 21:00 ET (Gate G1 Review)
- **Attendees**: P1, P2, P3, P4
- **Gate G1 Check**:
  - Text input compiles cleanly to typed Rule IR.
  - Candidate event generated and verified by Glimmer stub.
  - Evidence chain record written with SHA-256 hash chaining.
  - **Result: Gate G1 officially passed.**
- **Action Items**:
  - P2 to proceed into P2-04 (camera survey, Wilson score bounds, feasibility gauge) and P2-05 (interview grounding).

---

### Standup 3 · Saturday Oct 31, 09:00 ET (UC1 Kitchen Integration)
- **Attendees**: P1, P2, P3, P4
- **Priorities**:
  - P2: Fixed-step Planner pipeline (P2-06), plan linter, acceptance checks, and 2-round repair loop.
  - P1: Pair-a-Phone QR pairing and live WebM/H.264 stream transcoding.
  - P3: Hash chain verify page (standalone browser `chain.js` crypto matching Python).
  - P4: JEPA go/no-go 1 evaluation.
- **Blockers / Resolutions**:
  - Issue #41: Spec conflict in P2-04 regarding probe frame dimensions. Resolved in PR #51.

---

### Standup 4 · Saturday Oct 31, 21:00 ET (Gate G2 Review & Cut-Line Check)
- **Attendees**: P1, P2, P3, P4
- **Gate G2 Check**:
  - Kitchen UC1 complete: Photo of inspection citation + 2026 Food Code PDF + Spanish voice request.
  - Thermometer honestly marked `not_observable`; sick cook declined with written illness policy alternative.
  - Handwash countdown kiosk with MMS-TTS Spanish voice coaching functional. *(Correction, Oct 31: not functional; see Corrections above.)*
  - **Result: Gate G2 officially passed. Green light given for Should-tier work.**
- **Action Items**:
  - P2 to tackle Rule CI (P2-08, P2-09) and Site Advisor (P2-12).

---

### Standup 5 · Sunday Nov 1, 09:00 ET (Gate G3 & Held-Out Set)

*Written on Oct 31, ahead of the meeting. The E3 result below was withdrawn the same day (see Corrections).*

- **Attendees**: P1, P2, P3, P4
- **Progress**:
  - P2 completed all Must-tier tasks (P2-01 through P2-12).
  - P2 completed Should-tier tasks P2-S1 (Self-repair loop) and P2-S3 (Kitchen pass-window station type).
  - P2 completed P2-S2 (Voice disputes & two-key protection).
  - S-04 held-out request dataset created (20 requests across 5 domains); ~~E3 evaluation achieved **100.0% valid-or-honest rate** (60/60 runs).~~ Withdrawn: the runner was a simulated harness and the requests were team-written personas.
  - S-01 sets and props specifications merged into `docs/sets.md`.
- **Blockers / Resolutions**:
  - Resolved Windows CRLF newline divergence in canonical chain hashing with `newline="\n"`.
  - Resolved circular import between `pipeline.py` and `kitchen.py` by extracting `Hypothesis`.
  - Approved contract-change issue #33 for `reasoning_ref` in `VerifierVerdict`.

---

## 3. Pre-Freeze Readiness Checklist (Target: Mon Nov 2, 12:00 ET)

- [x] All P2 Must-tier tasks (P2-01 through P2-12) merged into `main`.
- [x] All P2 Should-tier tasks (P2-S1, P2-S2, P2-S3) merged into `main`.
- [x] Boundary-pair Muse Image edits with `previous_response_id` (P2 Stretch, PR #71) merged into `main`.
- [ ] E3 run through the real Planner on outsider requests, with plans in `plans/heldout/` (the existing runner is a simulated harness; its plans were removed Oct 31).
- [x] Physical set and prop specifications in `docs/sets.md` (S-01).
- [x] Social launch and outreach pack in `docs/social.md` (S-08).
- [ ] Golden run recordings (`scenarios/*.expected.json`, S-05, P3 lead).
- [ ] Video shoot filming schedules confirmed (P4 lead).
