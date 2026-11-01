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
| RTMPose (OpenMMLab) as ONNX, plus ONNX Runtime (`ml` extra) | CPU pose estimator (`pose/keypoints.py`, P4-04) | Apache-2.0 (code); weights are downloaded at run time, not bundled, so confirm their licence when fetching. ONNX Runtime is MIT |
| YuNet face detector (opencv_zoo) via OpenCV `FaceDetectorYN` | Blurring faces in evidence strips, never recognition (`runtime/strip_draw.py`, P4-05) | OpenCV is Apache-2.0; the ONNX weights are fetched by the owner (`RULECAM_FACE_MODEL`), not bundled, so confirm their licence when fetching |
| Roboflow `trackers` 2.6.1 | ByteTrack (with buffered IoU) joining SAM 3.1 image keyframes into tracks (`perception/keyframe_tracker.py`, PLAN-9.5 WS2b) | Apache-2.0 (LICENSE checked on GitHub at tag 2.6.1). Its `opencv-python` requirement is overridden in `pyproject.toml` (`[tool.uv]`): the repo's `opencv-python-headless` provides the same `cv2` |
| Roboflow `supervision` 0.30.0 | The `Detections` type `trackers` takes | MIT. Pulls PyAV (`av`, BSD-3-Clause; pinned to 19.0.1), defusedxml (PSF), pyDeprecate (Apache-2.0) |
| prometheus-client | `/metrics` for the runtime (`api/prometheus.py`) | Apache-2.0 |
| Next.js 15, React 19 | Web UI (`ui/`) | MIT |
| yaml (npm) | Rule IR YAML in the UI | ISC |
| TypeScript, ESLint, Prettier, Vitest, Testing Library, jsdom, openapi-typescript | UI development only | Apache-2.0 / MIT |
| Llama Prompt Guard 2 86M (`meta-llama/Llama-Prompt-Guard-2-86M`, gated) | Scene-text firewall (`verify/firewall.py`, P3-05) | Llama 4 Community License ("Built with Llama"); weights downloaded at run time, not bundled |

## Built during the hackathon

| Module | Description | PR |
|---|---|---|
| `src/rulecam/contracts/` | Shared typed contracts v1.0.0 (Rule IR, Site Plan, events, advisor, world model, Protocols) | #1 |
| `src/rulecam/bus.py`, `config.py`, `log.py`, `clock.py`, `cli.py` | Platform basics | #1 |
| `tests/fakes/`, `tests/guardrails/` | Test doubles and static guardrails | #1 |
| CI, Makefile, pre-commit, coverage floors | Engineering scaffold | #1 |
| `.github/workflows/ci.yml` | GitHub Actions CI (check, guardrails, ml-cpu); replaced the GitLab CI bridge on Oct 31 | #1 |
| `src/rulecam/capture/` + `rulecam capture` CLI | Webcam / RTSP / file → 3 s H.264 faststart chunks, published on the bus (P1-01) | #2 |
| `src/rulecam/pose/` | COCO-17 pose features (`is_down`, `hands_above_shoulder`, `ladder_three_point_lost`, `bent_back_lift`), the `PoseStates` adapter for the rule engine, the RTMPose ONNX estimator and keypoint interpolation (P4-04) | #77 |
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
