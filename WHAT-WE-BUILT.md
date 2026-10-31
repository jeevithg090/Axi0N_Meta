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
