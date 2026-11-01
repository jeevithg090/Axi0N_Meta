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
