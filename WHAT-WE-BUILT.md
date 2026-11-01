# What we built vs. what we reused

Everything in this repository was written during the hackathon (Oct 26 – Nov 4 2026).
Reused components are dependencies, never copied code. Each PR that adds a dependency or model
adds a row here with its licence.

## Reused (dependencies)

| Component | Use | Licence |
|---|---|---|
| pydantic, pydantic-settings | Contracts and settings | MIT |
| FastAPI, uvicorn | API server | MIT / BSD-3-Clause |
| httpx | HTTP client | BSD-3-Clause |
| meta-sam-parser 0.1.0 (Meta Platforms) | Decodes SAM 3.1 mask payloads (`clients/meta_hosted/sam_masks.py`) | **SAM License** (Nov 19 2025; custom, not OSI): royalty-free use, modification and redistribution under the same agreement; field-of-use bans (trade-control targets, ITAR, military, nuclear, espionage, weapons); no reverse engineering; indemnity; Meta may terminate on breach. Accepted by the team on Oct 30 2026 (PR #8) |
| structlog | Structured logging | MIT / Apache-2.0 |
| DuckDB | Track and event store | MIT |
| pyarrow | Parquet | Apache-2.0 |
| NumPy, SciPy | Numerics | BSD-3-Clause |
| OpenCV (headless) | Video and geometry | Apache-2.0 |
| Shapely | Zone geometry | BSD-3-Clause |
| Jinja2 | Templates | BSD-3-Clause |
| Playwright for Python (`ui` extra) | Rule card PNGs via headless Chromium (`cards/render.py`, P2-10) | Apache-2.0 |
| PyYAML | YAML | MIT |
| qrcode | QR codes | BSD |
| Pillow | Rule CI injection-sign composites (`rule_ci/injection.py`) | MIT-CMU (HPND) |
| python-kasa | Smart plug / light | GPL-3.0 (used as an external library; review before any commercial release) |
| python-multipart | Uploads | Apache-2.0 |
| Typer | CLI | MIT |
| pytest, pytest-cov, pytest-asyncio, Hypothesis, mypy, ruff, pre-commit | Development only | MIT / Apache-2.0 / MPL-2.0 |
| ffmpeg (system) | Video encoding | LGPL/GPL (system binary, not bundled) |
| RTMPose (OpenMMLab) as ONNX, plus ONNX Runtime (`ml` extra) | CPU pose estimator (`pose/keypoints.py`, P4-04) | Code Apache-2.0; ONNX Runtime MIT. **Weights: no checkpoint is chosen yet, and none is bundled.** Apache-2.0 on the code does not cover the weights: the common RTMPose "body7" and "aic-coco" checkpoints were trained on datasets with their own terms (AI Challenger, Halpe, PoseTrack18). Before first use, pick a COCO-only checkpoint (or review the rest) and record its exact name, download URL, sha256 and licence in this row. `RtmposeOnnx.load` requires that sha256 and refuses any other file |
| YuNet face detector (opencv_zoo) via OpenCV `FaceDetectorYN` | Blurring faces in evidence strips, never recognition (`runtime/strip_draw.py`, P4-05) | OpenCV is Apache-2.0; the ONNX weights are fetched by the owner (`RULECAM_FACE_MODEL`), not bundled, so confirm their licence when fetching |
| Roboflow `trackers` 2.6.1 | ByteTrack (with buffered IoU) joining SAM 3.1 image keyframes into tracks (`perception/keyframe_tracker.py`, PLAN-9.5 WS2b) | Apache-2.0 (LICENSE checked on GitHub at tag 2.6.1). Its `opencv-python` requirement is overridden in `pyproject.toml` (`[tool.uv]`): the repo's `opencv-python-headless` provides the same `cv2` |
| Roboflow `supervision` 0.30.0 | The `Detections` type `trackers` takes | MIT. Pulls PyAV (`av`, BSD-3-Clause; pinned to 19.0.1), defusedxml (PSF), pyDeprecate (Apache-2.0) |
| prometheus-client | `/metrics` for the runtime (`api/prometheus.py`) | Apache-2.0 |
| Next.js 15, React 19 | Web UI (`ui/`) | MIT |
| yaml (npm) | Rule IR YAML in the UI | ISC |
| TypeScript, ESLint, Prettier, Vitest, Testing Library, jsdom, openapi-typescript | UI development only | Apache-2.0 / MIT |
| Llama Prompt Guard 2 86M (`meta-llama/Llama-Prompt-Guard-2-86M`, gated) | Scene-text firewall (`verify/firewall.py`, P3-05) | Llama 4 Community License ("Built with Llama"); weights downloaded at run time, not bundled |
| torch, transformers, onnxruntime (`ml` extra) | Prompt Guard 2 and model inference | BSD-3-Clause / Apache-2.0 / MIT |
| Modal client (`ml` extra) | Self-hosted SAM 3.1 runtime (`perception/sam_runtime.py`, P1-03) | Apache-2.0 |
| SAM 3.1 weights (self-hosted on Modal) | Runtime segmentation and tracking (P1-03) | **SAM License** (same terms as meta-sam-parser above) |
| MMS-TTS checkpoints (`facebook/mms-tts-<lang>`) | Spoken warnings (`voice_out/`, P1-11) | **CC-BY-NC-4.0 (non-commercial)**: demo and research use only; a commercial release needs a different TTS |
| Muse Glimmer 30B GGUF (`meta-models/Muse-Glimmer-30B-GGUF`) served by llama.cpp `llama-server` | Tier 1 verifier (`verify/tier1.py`, P3-03) | Model: per the Muse Glimmer model card (commercial terms to confirm). llama.cpp: MIT |
| Muse Spark Open (Baseten) | Plan audit, dispute review, Tier 2 (P3-04, P2-07) | Per its licence; "Built with Muse Spark Open" attribution in the README |
| matplotlib, ultralytics YOLOv8 (`eval` extra) | Evaluation charts and the YOLOv8 baseline only; never imported by runtime code | Matplotlib licence (PSF-based) / **AGPL-3.0** |

## Built during the hackathon

| Module | Description | PR |
|---|---|---|
| `src/rulecam/contracts/` | Shared typed contracts v1.0.0 (Rule IR, Site Plan, events, advisor, world model, Protocols) | #1 |
| `src/rulecam/bus.py`, `config.py`, `log.py`, `clock.py`, `cli.py` | Platform basics | #1 |
| `tests/fakes/`, `tests/guardrails/` | Test doubles and static guardrails | #1 |
| CI, Makefile, pre-commit, coverage floors | Engineering scaffold | #1 |
| `.github/workflows/ci.yml` | GitHub Actions CI (check, guardrails, ml-cpu); replaced the GitLab CI bridge on Oct 31 | #1 |
| `src/rulecam/capture/` + `rulecam capture` CLI | Webcam / RTSP / file → 3 s H.264 faststart chunks, published on the bus (P1-01) | #2 |
| `src/rulecam/perception/calibration.py` | Ground-plane calibration (clicks, reference objects) and metric buffer zones (P1-05) | #3 |
| `src/rulecam/perception/coverage.py` | Coverage watchdog: frozen, dark, blocked or moved cameras turn rules BLIND, with watched-time accounting (P1-08) | #6 |
| `src/rulecam/clients/meta_hosted/` (SAM) | Hosted SAM 3.1 client with rate limiting, replay and cost accounting (P1-02) | #8 |
| `src/rulecam/rules/` (engine core) | Timeline, spatial predicates and dwell (P1-06a) | #9 |
| `src/rulecam/rules/temporal.py` | Temporal operators: sequence, persist, absence, rate (P1-06b) | #10 |
| `src/rulecam/perception/tracking.py`, `src/rulecam/rules/store.py` | Cross-chunk tracking and the Parquet/DuckDB track store (P1-04) | #11 |
| `src/rulecam/capture/pair_phone/` | Pair-a-Phone: QR pairing, HTTPS phone camera, transcode, SAM placement coach (P1-09) | #12, #60 |
| `src/rulecam/advisor/process.py` | Site Advisor process log (OCEL-shaped) with quality gates (P1-12) | #15 |
| `src/rulecam/voice_out/` | MMS-TTS pre-render with a Voice Transcribe round-trip check (P1-11) | #4 |
| `src/rulecam/perception/sam_runtime.py`, `sam_worker.py`, `motion_gate.py`, `static_cache.py` | Self-hosted SAM 3.1 on Modal with motion gate, static cache and cost metrics (P1-03) | #16 |
| `src/rulecam/perception/sam_server.py`, `scripts/sam_pod/` | Self-hosted SAM 3.1 over HTTP on our own GPU pod, with fallback to hosted SAM (P1-03) | #79 |
| `src/rulecam/rules/near_miss.py`, `backtest.py` | Near-miss metrics, track-store backtester, decision re-derivation (P1-07) | #17 |
| `src/rulecam/perception/correction.py`, `correction_api.py` | Correction loop: negative-point backward re-track and re-evaluation (P1-10) | #19 |
| `src/rulecam/replay/` | Replay cache (record/replay), golden-run framework, preflight (P3-02) | #21 |
| `src/rulecam/evidence/chain.py` | Append-only hash-chained event log with browser-compatible canonical JSON (P3-07a) | #22 |
| `src/rulecam/verify/tier1.py`, Glimmer client | Glimmer local client and Tier 1 verifier (P3-03) | #23 |
| `src/rulecam/api/`, `src/rulecam/runtime/` | FastAPI app, runtime loop, WebSocket bus mirror (P3-08); plan approval activates the runtime; per-camera rule scope and SAM cost control | #24, #56, #66, #73 |
| `src/rulecam/clients/meta_hosted/` (Spark, Voice, Image) | Hosted Spark 1.3/1.2, Voice Transcribe and Muse Image clients via the replay cache (P2-01) | #25, #62 |
| `src/rulecam/planner/` (intake) | Multimodal intake: text, PDF/photo, diarized voice, narrated walk-through (P2-02) | #26 |
| `src/rulecam/verify/tier2.py`, Spark Open client | Spark Open Tier 2, dispute review, plan audit, devil's advocate (P3-04) | #27 |
| `src/rulecam/verify/policy.py`, `firewall.py` | Safety-asymmetric verification policy and Prompt Guard 2 scene-text firewall (P3-05) | #28 |
| `src/rulecam/act/` | Actions, escalation with acknowledgement timers, Kasa interlock with human resume, LAN alerts (P3-06) | #29 |
| `src/rulecam/planner/` (compiler, guardrails) | Rule IR compiler with repair; deterministic and model guardrail declines (P2-03) | #30 |
| `src/rulecam/evidence/merkle.py`, `packs.py`, `audit.py`, `verify_page/` | Merkle roots, browser verify page, evidence packs, audit CLI (P3-07b) | #31 |
| `ui/` (shell) | Shell, LIVE/REPLAY badge, relay HUD, live monitor, verifier panel (P3-09a) | #35 |
| `src/rulecam/planner/` (survey), `src/rulecam/stats/` | Camera survey, SAM detectability probes with Wilson bounds, feasibility gauge (P2-04) | #36, #51 |
| `ui/src/app/planner/`, `ui/src/app/plans/` | Planner, feasibility, plan and acceptance trace pages (P3-09b) | #37 |
| `src/rulecam/planner/` (interview, standards) | Clarifying questions with frame-tap answers; standards grounding with citation text match (P2-05) | #38 |
| `src/rulecam/planner/` (pipeline) | Fixed-step pipeline, persistence, plan linter, acceptance checks, bounded repair loop; acceptance and approval use each rule's camera scope (P2-06a/b/c) | #42, #43, #44, #65, #68, #78 |
| `src/rulecam/planner/` (audit) | Spark Open plan audit with stored reasoning; Glimmer back-translation (P2-07) | #45 |
| `src/rulecam/rule_ci/` | Rule CI: boundary-trace fuzzing, mutation testing, synthetic scenes, lookalikes, prompt-injection signs, alert forecast, boundary-pair image edits (P2-08, P2-09) | #46, #47, #71 |
| `src/rulecam/advisor/` | Site Advisor reasoning pipeline, safety veto, claim linter (P2-12); kitchen pass-window station type (P2-S3) | #48, #57 |
| `src/rulecam/cards/` | Rule cards and declined cards rendered from plan files (P2-10) | #49 |
| `src/rulecam/worker/`, `ui/src/app/worker/` | Worker rules page (QR), anonymous event cards, tap disputes (P2-11); voice disputes and two-key protected settings (P2-S2) | #50, #52, #61 |
| `src/rulecam/repair/` | Self-repair loop: clustering, fix proposals, Rule CI and backtest (P2-S1) | #53 |
| `src/rulecam/planner/` (site config) | Site config, scene calibration and zones from detections; reference-object probes for metric rules (P2-06) | #70, #72 |
| `eval/runners/e3.py`, `eval/heldout/` | **Simulated** E3 harness and a team-written request set (S-04). It does not call the Planner; relabelled and its outputs withdrawn on Oct 31 (W-00) | #63 |
| `docs/deploy.md` | Deployment guide: RunPod, Glimmer server, public UI, Pair-a-Phone behind the proxy | #54, #58, #59, #60 |
| `docs/sets.md`, `docs/social.md`, `docs/standups.md` | Physical sets (S-01), X posts plan (S-08), standups and gate ledger (S-10) | #64, #67, #69, #74 |
| `src/rulecam/advisor/{metrics,dock_stats,measurement}.py`, `advisor/stats/`, `advisor/whatif/` | Site Advisor metric catalogue, bottleneck detection, queueing what-ifs (Little, Erlang C, Allen-Cunneen), switchback plan and sign-flip read-out (P4-08b) | #80 |
| `src/rulecam/pose/` | COCO-17 pose features (`is_down`, `hands_above_shoulder`, `ladder_three_point_lost`, `bent_back_lift`), the `PoseStates` adapter for the rule engine, the RTMPose ONNX estimator and keypoint interpolation (P4-04) | #77 |
| `scripts/arrivals.py`, `eval/advisor/README.md` | Seeded truck-arrival script and seal protocol for Site Advisor sessions (P4-08a) | #76 |
| `src/rulecam/eval/manifests.py`, `eval/runners/e1.py`, `eval/report.py`, `rulecam eval label` | E1 evaluation harness: consent-gated clip manifests, 5 runs per arm, three ablations and two baselines, Wilson 95% intervals per rule / use case / stager, RESULTS.md section. **Not run:** needs the recordings (S-03) and signed consent forms (P4-06) | #123 |
| `eval/runners/{common,e4,e5,e7,e8,e9,e11}.py` | Evaluation runners E4 to E11: Rule CI aggregation with the D1/D2/D3 injection defences, Anonymous-Mode paired accuracy, cost and latency, UR Fall Detection (refused until its terms are recorded as accepted), chain re-derivation, Site Advisor sealed-cause and null-session scoring. **None run:** each writes a "not measured" result naming what is missing (P4-07) | #125 |
| `docs/consent.md` | Consent template and collection checklist for raw footage (S-02) | #75 |
| `HANDOFF.md`, `docs/SESSION_HANDOVER_P2.md` | Project handoff and P2 session state notes | #84 |
| `src/rulecam/runtime/strips.py`, `strip_draw.py` | Runtime evidence strips for Glimmer: keyframes across the episode (one in-memory ffmpeg decode per chunk), rule boxes, zones and a calibrated distance ruler, privacy modes (standard face blur via YuNet; anonymous/strict plate + skeletons, no person pixels) (P4-05) | #98 |
| `src/rulecam/perception/keyframes.py`, `keyframe_tracker.py`, `/segment_image` in `sam_server.py`, `Sam31ImageEngine` | Image-mode runtime: SAM 3.1 image keyframes (2–4/s per camera, self-hosted with hosted fallback) joined into cross-chunk tracks by ByteTrack; `RULECAM_RUNTIME_SAM_MODE` (P1-03/P1-04, PLAN-9.5 WS2b/WS2c) | this PR |
| `src/rulecam/replay/` + `rulecam golden` CLI | Replay cache with record/replay modes, golden runs and the preflight that replays them with no model calls (P3-02) | #21 |
| `src/rulecam/evidence/chain.py` | Append-only hash-chained event log, browser-compatible canonical JSON (P3-07a) | #22 |
| `src/rulecam/verify/tier1.py` + Glimmer client | Muse Glimmer local client and Tier 1 verifier with evidence logging (P3-03) | #23 |
| `src/rulecam/api/` (`app.py`, `ws.py`), runtime loop | FastAPI app, runtime orchestration loop, WebSocket bus mirror (P3-08) | #24 |
| `src/rulecam/verify/tier2.py` | Muse Spark Open (Baseten) Tier 2, dispute review, plan audit, devil's advocate (P3-04) | #27 |
| `src/rulecam/verify/policy.py`, `firewall.py`, `guard.py` | Safety-asymmetric verification policy and the Prompt Guard 2 scene-text firewall (P3-05) | #28 |
| `src/rulecam/act/` | Actions, escalation with acknowledgement timers, Kasa interlock with human-only resume, LAN alerts (P3-06) | #29 |
| `src/rulecam/evidence/merkle.py`, `packs.py`, `audit.py`, `verify_page/` + `rulecam audit` | Daily Merkle roots, the offline browser verify page, evidence packs, audit CLI (P3-07b) | #31 |
| `ui/` shell, `/monitor` | App shell, LIVE/REPLAY badge, relay HUD, live monitor and verifier panel (P3-09a) | #35 |
| `ui/` `/planner`, `/plans` | Planner, feasibility, plan diff and acceptance trace screens (P3-09b) | #37 |
| `src/rulecam/api/` plan activation, camera scope, auth | Plan approval activates the runtime; per-camera rule scope and SAM cost control; owner login and protected evidence routes; pipelined segmentation; stage timings (P3-08) | #66, #73, #90, #99, #103 |
| Runtime policy, actions and firewall wiring | Real policy, dry-run-first actions and the firewall in the live runtime; a failed Tier 1 goes to the human queue; verifier circuit breaker (P3-05/06) | #105, #109 |
