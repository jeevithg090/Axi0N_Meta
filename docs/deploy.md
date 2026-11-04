# Deployment: Vercel UI + RunPod API + RunPod Glimmer

A public deployment for showing the product. Visitors can watch; everything that changes state
(mode switch, planning, plan approval, answering the planner, pairing, corrections, runtime plan)
and the full evidence log are owner-only: from the API pod itself (loopback, over SSH) or from a
browser logged in with the owner passphrase ("Owner login" below).

**Visitors need `PUBLIC_DEMO=true`.** The live feed (`/ws`) and `GET /plans`, `GET /plans/{id}`
expose site content (events, reasoning, the owner's request text), so they are owner-only unless
the API pod sets `PUBLIC_DEMO=true`. Set it only on a demo site with staged, consenting data, never
on a real site. Without it, logged-out visitors get 403 on the monitor and plans pages; the owner
still sees everything after logging in. The evidence chain stays owner-only either way.
`scripts/deploy/runpod_up.sh --public-demo` sets it on the API pod; without the flag the pod's
env leaves it unset, so a value in `/workspace/rulecam/.env` still applies.

```
browser ──► https://rulecam.vercel.app  (Next.js, ui/)
              │  /rulecam-api/*  (Next rewrite)
              ▼
            https://<api-pod>-8000.proxy.runpod.net   (FastAPI, live mode)
              │  /ws  (read-only bus mirror; owner, or PUBLIC_UI_URL origin with PUBLIC_DEMO=true)
              ├─► Meta Model API (Spark, Voice, Image, SAM) via the replay cache
              ├─► https://<gpu-pod>-8080.proxy.runpod.net/v1  (llama-server + Muse Glimmer 30B)
              └─► https://<sam-pod>-8090.proxy.runpod.net     (sam_server + SAM 3.1, runtime only)
```

## One-command deploy

`scripts/deploy/runpod_up.sh` brings the whole stack up from prebuilt images, and is safe to run
again at any time (it only changes what differs). Everything below "Manual path" is the same
stack by hand, kept as the fallback.

**Images** (built by `.github/workflows/images.yml`; pull requests build them without pushing,
pushes to `main` push `sha-<commit>` and `latest`):

| Image | What is in it |
|---|---|
| `ghcr.io/jeevithg090/rulecam-api` | Python 3.12 slim (Debian trixie), the locked deps (`uv sync --frozen --no-dev`), ffmpeg 7 (no more 4.4 / `-fps_mode`), tini, sshd for owner actions. Runs uvicorn with `--timeout-graceful-shutdown 5 --forwarded-allow-ips 100.64.0.0/10` as the unprivileged `rulecam` user. |
| `ghcr.io/jeevithg090/rulecam-sam` | CUDA 12.8 base (Ubuntu 24.04), torch 2.10 cu128, `sam3` at `SAM3_COMMIT`, einops, pycocotools, psutil, pydantic, opencv, numpy < 2. Nothing is installed at boot, so no venv or uv-Python on the wiped container disk. |
| `ghcr.io/jeevithg090/rulecam-gpu` | The SAM image's venv plus `llama-server` from `ghcr.io/ggml-org/llama.cpp:server-cuda-b11669`, under a small supervisor: Glimmer and SAM on one 48 GB pod (`--one-gpu`, "One GPU per site" below). |

The packages start private on GHCR: make both public once (GitHub → Packages → the package →
Package settings → Change visibility), or create a RunPod registry credential for GHCR and pass
its id as `--registry <id>`.

**Before the first run** (once, on your machine):

1. `runpodctl config --apiKey …` (the script reads `~/.runpod/config.toml`; `RUNPOD_API_KEY`
   wins if set). The key is never printed; requests carry a `rulecam-deploy/1.0` User-Agent
   because Cloudflare blocks Python's default one.
2. Key files in `~/.runpod/` (mode 600, never in git or chat): `rulecam-glimmer.key` and
   `rulecam-sam.key` (required; e.g. `umask 077; openssl rand -hex 32 > ~/.runpod/rulecam-sam.key`),
   and optionally `rulecam-meta.key`, `rulecam-hf.token` (the account must have access to
   `facebook/sam3.1`), `rulecam-baseten.key`, `rulecam-session.secret` and
   `rulecam-ui-proxy.secret` (owner login, below). Each becomes a RunPod secret
   (`rulecam_glimmer_api_key`, …) that pods reference as `{{ RUNPOD_SECRET_<name> }}`, so no key
   value is in a pod's config, argv or the script's output. A key without a local file is left to
   the volume's file (`/workspace/rulecam/.meta_api_key` etc.), as on the manual pods.
   `--sync-secrets` re-uploads rotated files; `--inline-secrets` puts the values in pod env instead
   (redacted in the output) if your API key cannot manage secrets.

**Run:**

```bash
scripts/deploy/runpod_up.sh --dry-run      # reads the account, prints every write it would make
scripts/deploy/runpod_up.sh                # does it, waits for every /health, prints the URLs
scripts/deploy/runpod_up.sh --vercel       # also sets NEXT_PUBLIC_RULECAM_API and redeploys the UI
scripts/deploy/runpod_up.sh --public-demo  # demo site only: logged-out visitors can watch /ws and plans
scripts/deploy/runpod_status.sh            # state, $/h and health of each pod (exit 1 if not all ok)
scripts/deploy/runpod_down.sh              # stop all pods (volumes kept; --only sam to stop one)
```

What `up` does, in order (pods are found by name: `rulecam-api`, `rulecam-glimmer`,
`rulecam-sam`, `rulecam-gpu`; `--prefix` changes it):

1. RunPod secrets for the local key files (created when missing).
2. The API's network volume: the one the existing API pod mounts, else `rulecam-api`, else a new
   20 GB volume in a data center that has the 8 vCPU flavor in stock **and** offers network
   volumes (CPU pods can only persist to a network volume; `--data-center` sets the preference).
3. Glimmer (llama.cpp `server-cuda-b11669` with the documented args, `LLAMA_API_KEY` from the
   secret, `LLAMA_CACHE` on a 40 GB pod volume) and SAM (the SAM image, `SAM_API_KEY` and
   `HF_TOKEN` from secrets, port `8090/http` only). GPU stock-outs: the script reads the live
   stock catalog (`/v2/catalog/gpus`, CUDA ≥ 12.8) and tries each preferred GPU type in each data
   center that has it, most stock first, until one places (`--glimmer-gpus`, `--sam-gpus`; 48 GB
   cards first, then L4 / A5000 / 4090 for SAM). GPU pods use pod volumes, so they are not tied to
   the API volume's data center. `--one-gpu` replaces both with one 48 GB pod, `rulecam-gpu`
   ("One GPU per site" below).
4. The API CPU pod (8 vCPU, `8000/http, 22/tcp`, SSH keys from the account) in the volume's data
   center, with `RULECAM_MODE=live`, `RULECAM_RUNTIME=true`, `GLIMMER_BASE_URL` / `SAM_BASE_URL`
   pointing at the GPU pods' proxy URLs, `RULECAM_GPU_USD_PER_HOUR` set to their combined price
   (as RunPod reports it; `--gpu-usd-per-hour` overrides it), `RULECAM_IDLE_STOP_GPU_PODS` set
   to their names, and the key references. Anything else still comes
   from `/workspace/rulecam/.env` on the volume.
5. Existing pods are reused: a stopped pod is started; a pod whose image, env, ports or start
   command differ is patched (a manual pod's `bash -c "(boot.sh &) ; exec /start.sh"` is cleared so
   the image's entrypoint runs). A stopped GPU pod whose GPU was taken cannot start: the error
   says so (retry later, or `runpod_down.sh --only sam --terminate --yes` and `up` again, which
   drops that pod's model cache).
6. Unless `--no-wait`, it waits for each pod to run and each `/health` to answer 200 (the first
   SAM start downloads the checkpoint; Glimmer downloads 17 GB).

New code reaches the pods through the images: after the `images` workflow pushed a commit, run
`runpod_up.sh --image-tag sha-<commit>` (or keep `latest` and restart the pods). Restarts are
safe: both entrypoints keep their state on the volume, so a fresh container (a pod restart wipes
the container disk) comes up the same way; CI checks this by running the API image twice on one
volume (`tests/deploy/test_image_restart.py`).

Image-based API pod: the entrypoint (`deploy/api-entrypoint.sh`) reads the key files in
`/workspace/rulecam` like `start.sh` (environment first, then file; also `.session_secret` and
`.ui_proxy_secret`, so no `start.sh` edit is needed for owner login), defaults
`PUBLIC_TUNNEL_URL` to `https://$RUNPOD_POD_ID-8000.proxy.runpod.net`, starts sshd when RunPod
passes `PUBLIC_KEY` (for one-time owner setup such as `rulecam owner set-passphrase`, and
loopback owner actions), then runs uvicorn as `rulecam`. On a volume that ignores ownership
(MooseFS also ignores chmod) it logs a warning and stays root, like `setup.sh`.

`--api-source archive` keeps the documented `runpod/base` image instead: it sets the start command
to `bash -c "(/workspace/rulecam/deploy/api-boot.sh &) ; exec /start.sh"`, uploads `git archive
HEAD` over SSH (`--ssh-key`, default `~/.ssh/runpod_ed25519`) and starts `deploy/api-boot.sh`,
which keeps uv's Python, cache and the venv on the volume and runs the same entrypoint.

## One GPU per site

Two GPU pods per site (Glimmer on an A6000 at about $0.59/h, SAM at about $0.59/h) cost $1.18/h,
so one camera costs $1.18 per camera-hour. The idle target is $0.15. The runtime only uses SAM
**image** mode (no video session state, about 4 to 6 GB), so SAM fits next to Glimmer on one
48 GB card. `scripts/deploy/runpod_up.sh --one-gpu` runs both on a single pod, `rulecam-gpu`:
Glimmer on `8080/http` and SAM on `8090/http`, both behind the RunPod HTTPS proxy.

**Image** `ghcr.io/jeevithg090/rulecam-gpu` (`deploy/gpu.Dockerfile`, built by the `images`
workflow when its inputs change):

- the SAM venv from `deploy/sam.Dockerfile`, pin for pin: CUDA 12.8 base (Ubuntu 24.04), torch
  2.10 cu128, `sam3` at `SAM3_COMMIT`, einops, pycocotools, psutil, pydantic, opencv, numpy < 2;
- `llama-server` and its ggml backends, copied from `ghcr.io/ggml-org/llama.cpp:server-cuda-b11669`
  (Muse Glimmer needs ≥ b10353). That image keeps everything in `/app`: `/app/lib/*` (the shared
  libraries, including the CUDA backend) plus `/app/llama-server`. We copy all of `/app` to
  `/opt/llama` so the backends stay next to the binary. It links the CUDA runtime, cuBLAS and NCCL.
  The torch wheels already ship them (`libcudart.so.12`, `libcublas.so.12`, `libcublasLt.so.12`,
  `libnccl.so.2`), so
  `/opt/llama/cuda` links to those and the image carries no second copy (the CUDA `runtime`
  image would add about 3 GB). The build fails if `ldd` finds any library of the binary or its
  backends unresolved. `libcuda.so.1` comes from the host driver at run time; at build time it
  is checked against the image's compat copy. The runtime packages are the ggml-org image's:
  `libgomp1`, `curl`, `ffmpeg`;
- `deploy/gpu-entrypoint.sh` and `deploy/gpu_supervisor.py` (stdlib Python, run with the venv).

**Processes.** `tini` → `gpu-entrypoint.sh serve`. As root, it reads the secrets and
`/workspace/sam.env`, creates the caches and drops to the `sam` user (uid 10001). Then
`gpu_supervisor.py` starts both services at once:

| Service | Command | Health | Cache on the pod volume |
|---|---|---|---|
| glimmer | `/opt/llama/llama-server` with the Glimmer pod's documented args (`-c $GLIMMER_CTX -np $GLIMMER_PARALLEL`, defaults 32768 and 2) | `:8080/health` | `LLAMA_CACHE=/workspace/llama-cache` (GGUF + mmproj, about 18 GB) |
| sam | `sam-entrypoint.sh _serve` (fills the checkpoint cache once, then `sam_server` offline) | `:8090/health` | `HF_HOME=/workspace/hf-cache` (about 3.5 GB) |

Restart rules:

- **Exit.** A service that exits is restarted. The wait starts at 2 s and doubles up to 60 s; it
  goes back to 2 s after a run that became healthy or lasted 2 minutes.
- **Hang.** A service whose `/health` stops answering for 3 minutes, after it had been healthy,
  gets SIGTERM, then SIGKILL 20 s later, and is then restarted. A server that has never been
  healthy is left to load, because the first boot downloads about 21 GB.
- **Stop.** SIGTERM to the container stops both servers.
- **Health.** `gpu-entrypoint.sh healthcheck`, the image's `HEALTHCHECK`, needs both `/health`
  endpoints. `runpod_status.sh` shows the pod `ok` only when both answer.
- **Memory log.** Once both servers are healthy, the supervisor logs one
  `gpu memory (name, total, used, free)` line from `nvidia-smi`.
- **Plan.** `gpu-entrypoint.sh plan` prints both commands and contains no secrets.

**Secrets.** Each comes from the environment first (RunPod secrets) and otherwise from a mode-600
file on the volume. No secret is ever logged or put in argv. The supervisor takes all three out
of its own environment and gives each child only its own:

| Variable | File fallback | Goes to |
|---|---|---|
| `LLAMA_API_KEY` (RunPod secret `rulecam_glimmer_api_key`) | `/workspace/.llama_api_key` | llama-server only |
| `SAM_API_KEY` (`rulecam_sam_api_key`) | `/workspace/.sam_api_key` | the SAM server only |
| `HF_TOKEN` (`rulecam_hf_token`) | `/workspace/.hf_token` | the SAM checkpoint download only. It is dropped once SAM has been healthy, and the SAM entrypoint unsets it before the server starts |

**GPU memory budget (48 GB card; an RTX A6000 has 49,140 MiB).** These are estimates from the
model cards. Muse Glimmer is a 30B dense model with 52 layers, 3 sliding-window layers to 1
full-attention layer, sold as fitting in 24 to 32 GB at 4 bit. SAM 3.1 has about 0.85B
parameters. The supervisor's `gpu memory` log line gives the real numbers on the first boot:
record them here.

| Item | Budget |
|---|---|
| Glimmer weights, Q4_K_M, all layers on the GPU (`-ngl 99`) | about 17 GB |
| mmproj (perception encoder) | about 1 to 2 GB |
| KV cache, `-c 32768` over `-np 2` (16k tokens per slot); only 13 full-attention layers keep the whole context | about 2 GB |
| llama.cpp compute buffers (vision graph included) | about 2 to 3 GB |
| **Glimmer, allocated once at load** | **about 24 GB (budget 26)** |
| SAM 3.1 weights | about 3.5 GB |
| SAM image call (every keyframe and concept of a chunk) | +1 to 2.5 GB |
| **SAM, capped by `SAM_MEMORY_GUARD_GB=8`** (set in the image; was half the device) | **≤ 8 GB** |
| CUDA contexts (two processes) | about 1 GB |
| **Headroom** | **about 13 GB** |

- llama.cpp allocates weights, KV cache and compute buffers once, at load. A SAM spike can
  therefore only fail SAM's own call: the runtime falls back to hosted SAM for 60 s, and Glimmer
  is not touched. The guard frees SAM's cache after every call, so a Glimmer restart finds its
  memory free again.
- The context stays at 32768. The KV cache is small because most layers use a sliding window,
  and a 6-frame strip with the prompt uses a few thousand tokens per slot. If the memory log
  shows less than about 6 GB free, put `GLIMMER_CTX=16384` in `/workspace/sam.env`; it saves
  about 1 GB.
- **Video `/segment` on this pod is offline only** (`scripts/sam_pod/compare.py`, `soak.py`). A
  video session grew from 4 to 22 GB on an L4. Run those with the runtime stopped, or on a
  separate SAM pod (`runpod_up.sh` without `--one-gpu`).

**Deploy (operator).**

1. Merge to `main`. The `images` workflow pushes `ghcr.io/jeevithg090/rulecam-gpu:sha-<commit>`
   and `:latest`. Make the package public once (GitHub → Packages → rulecam-gpu → Package
   settings → Change visibility), or pass `--registry <RunPod registry credential id>`.
2. Key files: the existing `~/.runpod/rulecam-glimmer.key` (it becomes `LLAMA_API_KEY`),
   `rulecam-sam.key` and `rulecam-hf.token`. The token's account needs access to
   `facebook/sam3.1`.
3. `scripts/deploy/runpod_up.sh --one-gpu --image-tag sha-<commit> --dry-run`. Check the planned
   `rulecam-gpu` pod (ports `8080/http, 8090/http`) and the API env: `GLIMMER_BASE_URL` (…-8080
   …/v1), `SAM_BASE_URL` (…-8090…), `RULECAM_GPU_USD_PER_HOUR` (`<gpu pod $/h>` until the pod
   exists) and `RULECAM_IDLE_STOP_GPU_PODS=rulecam-gpu`.
4. `scripts/deploy/runpod_up.sh --one-gpu --image-tag sha-<commit>`. It tries RTX A6000 → A40 →
   L40S → RTX 6000 Ada (`--one-gpu-gpus`) in each data center with stock, then patches the API
   pod's env, including `RULECAM_GPU_USD_PER_HOUR` set to the placed pod's price
   (`--gpu-usd-per-hour` overrides it). Then it waits for every `/health`; the first boot
   downloads about 21 GB. It warns that `rulecam-glimmer` and `rulecam-sam` still run.
5. `scripts/deploy/runpod_status.sh`: `rulecam-gpu` must be `ok`, which needs both servers. Read
   the `gpu memory` line in the pod log and fill in the budget table above.
6. Stop the old pods: `scripts/deploy/runpod_down.sh --only glimmer,sam` (their volumes stay).
   Once you are satisfied, run `--only glimmer,sam --terminate --yes`.
7. Rollback: run `scripts/deploy/runpod_up.sh` without `--one-gpu`. It starts the two pods again,
   rewires the API and warns that `rulecam-gpu` still runs.

**Cost per camera-hour.** The GPU share is the pod's price divided by the cameras it serves. API
CPU pod: about $0.06/h, not model spend.

| Layout | GPU $/h | 1 camera | 4 cameras | 6 cameras | 8 cameras |
|---|---|---|---|---|---|
| Two pods (Glimmer + SAM) | 1.18 | 1.18 | 0.295 | 0.197 | 0.148 |
| One GPU (A6000) | 0.59 | 0.59 | 0.148 | **0.098** | 0.074 |

The deployment assumption next to the PLAN-9.5 cost target is **one 48 GB pod per site, shared
by 4 to 8 cameras**. The estimate behind it:

- An image-mode chunk (2 concepts, 2 keyframes/s, 3 s) takes about 1 s of GPU ("Runtime
  perception modes": one L4 carries 2 to 3 cameras that segment every chunk).
- Idle sampling segments one chunk in 3, and the motion gate skips still chunks.
- An A6000 is about 1.5 to 2 times an L4.
- Glimmer only runs on candidates, 2 at a time.

This is unmeasured. The scorecard's cost rows divide by `--cameras-per-gpu N`, or by
`cameras_per_gpu` in the site config (`RULECAM_SITE_CONFIG` or `--site-config`;
`docs/site.example.yaml` uses 6). The target then reads "(one GPU pod per N cameras)", and the
measured column shows `GPU $X/h ÷ N cameras = $Y`. The Cost area passes only with the
`gpu_cameras` check in `eval/checks.yaml`: a soak showing one pod carried at least N cameras at
the real-time targets. Example:

```bash
uv run rulecam eval score reports/eval/runs/<run> --gpu-usd-per-hour 0.59 --cameras-per-gpu 6
```

The live cost meter (`/runtime/metrics` → `cost`) does not assume anything: it shares
`RULECAM_GPU_USD_PER_HOUR` among the cameras that are active. On a site with fewer cameras
than the assumption it shows the real, higher cost per camera, and it books time with no
active camera as `gpu_idle_usd`.

## Monitoring

`GET /metrics` on the API serves Prometheus text: read-only aggregates, safe to leave public (no
traces, event ids, verdict reasons, URLs or keys; counters are per camera at most, never per
person or track). Families: `rulecam_runtime_lag_seconds`, `_keyframe_mode`, `_in_flight_chunks`,
`_segment_latency_seconds{quantile}`, `_chunks_total{outcome}`, `_billed_frames_total`,
`_billed_frames_per_minute{camera}`, `_hosted_sam_usd_per_hour`, `_budget_*`;
`rulecam_perception_sam_calls_total{backend}`, `_sam_fallbacks_total`, `_gpu_seconds_total`,
`_usd_per_camera_hour{basis}`; `rulecam_cost_budget_alarm`, `_budget_usd_per_camera_hour`,
`_usd_total`, `_usd_per_camera_hour`, `_usd_per_hour{camera}`, `_gpu_idle_stopped` (WS3);
`rulecam_verdicts_total{tier,verdict}`;
`rulecam_dependency_up{service="glimmer"|"sam"}` and `rulecam_dependency_state{service,state}`
(cached 10 s), serverless `rulecam_dependency_workers{service,status}` and
`_workers_limit{service,limit}`, and the SAM pod's `rulecam_sam_gpu_memory_bytes{kind}` /
`rulecam_sam_memory_guard_trips_total`. `GET /health` serves the same state under `gpus`.

**GPU state.** A pod URL is probed on its open `/health` (`warm` when it answers, else
`unhealthy`). A serverless URL (`https://<id>.api.runpod.ai`) is never polled: its edge answers
401 without the RunPod key, and with it every probe would start a worker. The API reads the
endpoint from RunPod's REST API instead (`GET /v2/serverless/{id}` and `/workers`, which wake
nothing), with the key in `RULECAM_RUNPOD_API_KEY_FILE` when set, else `SAM_EDGE_KEY` /
`GLIMMER_EDGE_KEY` (`runtime/serverless.py`). States: `warm` (a worker runs, billed),
`cold-ready` (scaled to zero, the next request starts one), `starting` (RunPod is preparing a
worker), `unhealthy` (`workers.max` is 0, or every worker is unhealthy) and `unknown` (no
answer from the REST API, e.g. a key restricted to the endpoints only: put a read-capable key in
`RULECAM_RUNPOD_API_KEY_FILE`). The first three are up, so a GPU scaled to zero raises no alert,
the verifier's recovery probe passes and the UI can show "GPU waking". With both GPUs serverless
and their state known, the cost meter charges `RULECAM_GPU_WORKER_USD_PER_HOUR` (1.34) per
running worker instead of `RULECAM_GPU_USD_PER_HOUR`.

`deploy/monitoring/` is a minimal Prometheus + Grafana (`docker compose -f
deploy/monitoring/docker-compose.yml up -d` on your own machine, after copying
`targets/rulecam.example.json` to `targets/rulecam.json` with the API pod's proxy host). Grafana
is AGPL-3.0: run it for yourself, don't host it for others. Alert rules (`alerts.yml`):

| Alert | Rule | Severity |
|---|---|---|
| RulecamApiDown | `/metrics` unreachable for 1 min | page |
| RulecamLagHigh | `rulecam_runtime_lag_seconds > 10` for 1 min | page |
| RulecamDependencyDown | `rulecam_dependency_up == 0` for 2 min (Glimmer or SAM `unhealthy` or `unknown`; scaled to zero is up) | page |
| RulecamKeyframeMode | keyframe mode for 1 min | warning |
| RulecamSamFallback | any self-hosted SAM outage in 10 min | warning |
| RulecamHostedSpendHigh | hosted SAM above $1.20/h for 5 min | warning |
| RulecamBudgetAlarm | a camera over its dollar budget for 2 min | warning |
| RulecamSamMemoryGuard | more than 2 guard trips in 15 min | warning |

Without Prometheus, `runpod_status.sh` (exit 1 unless every pod runs and is healthy) works from
cron.

## Manual path (fallback)

The sections below are the stack by hand, as first deployed. Use them when the images or the
script cannot be used.

## Vercel (UI)

- Project `rulecam`, **Root Directory `ui`**, connected to the GitHub repo: every push to `main`
  deploys production, and every PR gets a preview.
- `NEXT_PUBLIC_RULECAM_API` = the API pod URL. It is shipped to browsers, so never put a secret
  in any `NEXT_PUBLIC_*` variable. Model keys belong on the API pod only.
- Optional, server-only: `RULECAM_PROXY_SECRET` (see "Per-client rate limits" below). The UI's
  middleware reads it on Vercel's servers; it never reaches a browser.

## API pod (RunPod CPU)

- Image `runpod/base:1.4.0-ubuntu2204` (ships `uv`, Python 3.12, ffmpeg), 20 GB network volume at
  `/workspace`, ports `8000/http, 22/tcp`.
- Code in `/workspace/rulecam` (a `git archive` of `main`), deps with `uv sync --no-dev`.
- `/workspace/rulecam/.env` holds non-secret settings: `RULECAM_MODE=live`, data and evidence dirs
  on `/workspace`, `META_API_BASE_URL`, `GLIMMER_*`, `PUBLIC_TUNNEL_URL` (this pod's proxy URL),
  `PUBLIC_UI_URL=https://rulecam.vercel.app`, and `PUBLIC_DEMO=true` (demo site only; see above).
- Secrets are files with mode 600, read by `start.sh` into the environment: `.meta_api_key`,
  `.glimmer_api_key` (add `.baseten_api_key` / `HF_TOKEN` the same way when needed). The owner
  writes them; they are never in `.env`, argv, chat or git.
- `start.sh` runs `uvicorn rulecam.api.app:app --host 0.0.0.0 --port 8000
  --timeout-graceful-shutdown 5`. Without the timeout, a restart hangs while public `/ws` viewers
  stay connected, and the new process cannot take the port. It also passes
  `--forwarded-allow-ips 100.64.0.0/10` (RunPod's proxy network) so phones on the HTTPS proxy are
  seen as `https` (Pair-a-Phone refuses plain HTTP). Never use `"*"`. Owner routes stay safe with
  this setting: they also need a loopback Host, which the public proxy never sends (tested with
  spoofed `X-Forwarded-For` / `X-Forwarded-Host`: 403).

### Owner login

The owner logs in at `https://rulecam.vercel.app/login` with a passphrase. There is no account,
email or third-party service: the API pod holds the passphrase's scrypt hash and a signing secret.

How it works (`src/rulecam/api/auth.py`):

- `POST /auth/login {passphrase}` checks the passphrase against `OWNER_PASSPHRASE_HASH`
  (stdlib `hashlib.scrypt`, N = 2^17, r = 8, p = 1, constant-time compare), only over HTTPS, and
  at most 5 attempts a minute per client and 20 a minute in total (429 with `Retry-After`).
- It sets one cookie: `__Host-rulecam_owner`, `HttpOnly; Secure; SameSite=Strict; Path=/`, no
  `Domain`, `Max-Age` = `SESSION_TTL_S` (default 12 h). The browser calls the API through the
  Vercel rewrite (`/rulecam-api/*` on `rulecam.vercel.app`), so the cookie belongs to
  `rulecam.vercel.app`, is sent on every `/rulecam-api/*` call and is never sent to the RunPod
  URL directly. The value is a payload signed with HMAC-SHA256 under a key derived from
  `SESSION_SECRET` and the passphrase hash; changing either signs every session out.
- Owner routes accept loopback (as before) or a valid session. A write (POST/PUT/PATCH/DELETE)
  on a session also needs `Origin: https://rulecam.vercel.app` (the `PUBLIC_UI_URL` origin) and
  the `X-CSRF-Token` header equal to the token inside the signed cookie (the UI gets it from
  `GET /auth/me` and keeps it in memory). Otherwise 403.
- `POST /auth/logout` revokes the session (in memory, until the API restarts; the cookie's
  expiry still applies) and clears the cookie. `GET /auth/me` says who is asking.
- `/ws` still lets the public UI's origin watch; the browser opens it on the RunPod URL directly,
  where the cookie is not sent, so it is unchanged by login.

Set it up (once, on the API pod over SSH; the passphrase is typed into `getpass`, never echoed,
written to a file, put in argv or logged):

```bash
ssh -i ~/.ssh/runpod_ed25519 -p <port> root@<ip>
cd /workspace/rulecam
uv run rulecam owner set-passphrase          # prompts twice; prints one line
# append the printed OWNER_PASSPHRASE_HASH=scrypt:... line to /workspace/rulecam/.env
umask 077; openssl rand -hex 32 > /workspace/rulecam/.session_secret
grep -q '^PUBLIC_UI_URL=https://rulecam.vercel.app$' .env || echo 'PUBLIC_UI_URL missing'
```

In `start.sh`, export the secret like the other key files, before `uvicorn`:

```bash
export SESSION_SECRET="$(cat /workspace/rulecam/.session_secret)"
```

Then update the code and restart the API (see "Update the API"). Optional: `SESSION_TTL_S` in
`.env` (300 to 604800 s). The API logs `owner_login_disabled reason=...` when the hash is
malformed or the secret is shorter than 32 characters; login then answers 503 and only loopback
is the owner. To change the passphrase, run `set-passphrase` again and replace the line; to sign
everyone out, write a new `.session_secret`; restart after either.

### Per-client rate limits

Behind Vercel and the RunPod proxy, uvicorn sees a proxy's address, so every browser shares one
login limit and one worker-dispute limit. With a shared secret the UI forwards the browser's
address and the API trusts it only next to that secret (`ClientAddress` in `api/auth.py`,
`ui/src/middleware.ts`; a browser's own copies of the two headers are always dropped):

```bash
# on the API pod
umask 077; openssl rand -hex 32 > /workspace/rulecam/.ui_proxy_secret
# in start.sh:  export UI_PROXY_SECRET="$(cat /workspace/rulecam/.ui_proxy_secret)"
# from your machine: the same value as a server-only Vercel variable, then redeploy
ssh -i ~/.ssh/runpod_ed25519 -p <port> root@<ip> 'cat /workspace/rulecam/.ui_proxy_secret' \
  | vercel env add RULECAM_PROXY_SECRET production
```

Without it everything works; the limits are just shared. Never make it a `NEXT_PUBLIC_*`
variable.

### Site config (planner geometry)

The planner reads the owner's site config from the YAML file named by `RULECAM_SITE_CONFIG`
(put it in `.env`, e.g. `RULECAM_SITE_CONFIG=/workspace/rulecam/site.yaml`). Start from
[`docs/site.example.yaml`](site.example.yaml): site name, privacy mode, retention, alert roles,
crew languages and, per camera, an optional manual calibration (four or more floor pixels with
their positions in metres; five or more give a real error estimate, four always fit exactly)
and optional named polygon zones. It is validated at start-up: an invalid file disables the
planner (`planner_disabled` in the log, `POST /plans/requests` answers 503); a missing file
gives today's defaults (`site_config_missing` in the log). Restart the API after editing it.

What the file does not give, the planner takes from the scene: a pallet (48x40 in) or a 1 m
floor square that SAM finds lying on the floor calibrates its camera (the best fit per camera;
the step detail says `cam1 calibrated from pallet_48x40in (0.03 m)`), and places a rule needs
(sink, prep counter, door) become zones around their detections. The owner's calibration and
zones always win. A rule that needs metres on a camera with neither stays amber and the plan
asks the owner to mark a known distance; no scale is ever guessed. Approving the plan hands the
calibration (`PlanStore.calibrations`) and the rules' zones to the runtime.

Pair a phone (owner action: logged in as the owner, or on the pod):

```bash
ssh -i ~/.ssh/runpod_ed25519 -p <port> root@<ip> 'curl -s -X POST localhost:8000/pair/token' | python3 -c 'import json,sys; print(json.load(sys.stdin)["url"])'
```

Open the printed URL on the phone (or scan the QR in `qr_png_base64`) before it expires.
- Auto-start: the pod's `dockerStartCmd` is
  `bash -c "(/workspace/rulecam/boot.sh &) ; exec /start.sh"`, and `boot.sh` runs `start.sh`
  (log: `/workspace/api.log`). Set it with the RunPod REST API (`PATCH /v1/pods/<id>`); `runpodctl`
  has no flag for it.
- The SSH port changes on every restart: `runpodctl pod get <id>` shows the current one.

Update the API: `make ship-api` (below). By hand: `git archive origin/main | ssh … 'tar -x -C
/workspace/rulecam'`, then `uv sync --no-dev` and restart (`pkill -f "[u]vicorn rulecam.api.app"`
then `./start.sh` via `setsid nohup`, or restart the pod).

### Ship the API

`GET /health` reports `revision`, the git SHA the API runs (`RULECAM_REVISION`, else the
`REVISION` file at the release root; `null` on a hand-made checkout).

```bash
export RULECAM_API_SSH_HOST=<ip> RULECAM_API_SSH_PORT=<port>   # runpodctl pod get 7nv0ifjo2tmk00
make ship-api                 # the committed HEAD; `make ship-api REF=<sha>` for another commit
```

`scripts/deploy/ship_api.sh` uploads `git archive` into `/workspace/releases/<sha12>` (key
`RULECAM_API_SSH_KEY`, default `~/.runpod/ssh/runpodctl-ssh-key`; SSH keep-alives and retries,
since the pod's SSH drops now and then), then starts `scripts/deploy/ship_api_remote.sh` on the
pod with `setsid nohup`, so a dropped session cannot stop it halfway. That script:

1. The first time, moves the hand-made `/workspace/rulecam` to `/workspace/rulecam-shared` and
   makes `/workspace/rulecam` a symlink to it (the first rollback target).
2. Links everything untracked from `rulecam-shared` into the release: `.env`, `.sam_edge_key`,
   `.glimmer_edge_key`, `.glimmer_api_key`, `start.sh`, `boot.sh`, and `plans/`, `evidence/`,
   `data/`, `reports/`, which the API writes into. Keys are never copied.
3. Builds the release's own `.venv` (`uv sync --frozen --no-dev`). Until here the live API is
   untouched; a failure ends with status `failed`.
4. Switches the `/workspace/rulecam` symlink, runs `/workspace/restart_api.sh` detached and polls
   `127.0.0.1:8000/health` until it reports the new revision (`HEALTH_WAIT_S`, 300 s). Otherwise it
   switches back, restarts again and ends with `rolled-back`.
5. Keeps the 3 newest releases (`KEEP`) plus the live and previous ones.

The result is in `/workspace/releases/ship-<sha12>.status` (`ok`, `rolled-back`, `failed`) and
the log in `ship-<sha12>.log`; `make ship-api` prints its tail. Roll back by hand: `ln -sfn
/workspace/releases/<old sha12> /workspace/rulecam.next && mv -Tf /workspace/rulecam.next
/workspace/rulecam`, then `setsid nohup /workspace/restart_api.sh > /dev/null 2>&1 < /dev/null &`.

### Ship the GPU code

```bash
export RUNPOD_S3_ACCESS_KEY_ID=user_... RUNPOD_S3_SECRET_ACCESS_KEY=rps_...  # RunPod S3 API key
make ship-gpu                 # DRY_RUN=1 prints the writes
```

`scripts/deploy/ship_gpu.sh` copies `src`, `scripts/sam_pod` and `scripts/glimmer_pod`
(`SHIP_GPU_PATHS`) of the committed HEAD onto network volume `11ymk65bni` (US-IL-1) through
RunPod's S3-compatible API (`https://s3api-us-il-1.runpod.io`, bucket = volume ID) at
`rulecam/<path>`, which serverless workers see as `/runpod-volume/rulecam/...`. It deletes
files the commit no longer has and writes `rulecam/REVISION`. No staging pod is needed. It uses
the `aws` CLI, or `uvx --from awscli aws` when that is missing. Running workers keep their
code; the next cold start loads the new one (`scripts/ops/warm.sh off` and wait out the 300 s
idle timeout to force it). New Python dependencies still need the staging pod (`setup.sh
--stage`).

### Operator exclusion (forklift drivers)

A person riding a vehicle (forklift, pallet jack, cart, truck, ...) inside its box and moving with
it is that vehicle's operator, not a pedestrian: person-vehicle distance and buffer rules ignore
the ridden vehicle for them (`src/rulecam/rules/operators.py`). It is on for every rule. To count
riders as pedestrians for a rule, list its id in `RULECAM_OPERATOR_EXCLUSION_OFF` (comma-separated,
e.g. `RULECAM_OPERATOR_EXCLUSION_OFF=forklift-proximity`) and restart the API; each candidate's
evidence record keeps the flag it was decided with.

A person first seen already on board (the tracker's first keyframes of a seated driver) is held
as *operator pending* for up to `RULECAM_OPERATOR_WARMUP_S` (default and maximum 1.5 s, 0 turns
it off) before being judged operator or pedestrian. The trade-off: a pedestrian first seen inside
a moving vehicle's box alerts at most 1.5 s later, on HIGH (act-first) rules too, well inside the
10 s alert-latency target; pedestrians seen approaching from outside alert at once.

### Actions, escalation and the interlock

The runtime decides with P3-05's `VerificationPolicy` and acts with P3-06's `ActionExecutor`
(`src/rulecam/runtime/response.py`). `GET /runtime/metrics` shows how they are configured under
`response`, and the API logs the same once at start-up (`response_built`):

| Setting | Effect |
|---|---|
| `RULECAM_ACTIONS_DRY_RUN` (default `true`) | Every action the decision allows is chained as `dry-run: would cut power`, `dry-run: would notify dock_lead (needs review)`, ... and none runs: no power cut, light, speaker, LAN alert or escalation chain. Set `false` only once live tests show clean alerts. |
| `BASETEN_API_KEY` | Tier 2 (Spark Open) reviews what Tier 1 did not confirm (rules with `tier2: spark-open-on-disagree`) and every worker dispute. Without it (`response.tier2 = absent`, logged `tier2_absent`) Tier 1 decides alone: a confirmed violation alerts, a confident rejection is suppressed, anything unsure goes to the human queue ("Needs review" notifications), and disputes stay `pending` for a person. Nothing passes silently. |
| `HF_TOKEN` + the `ml` extra | The scene-text firewall: Llama Prompt Guard 2 (86M, CPU) loads at start-up and screens the text both tiers report; an injection attempt blurs the sign, re-verifies and chains `tamper_attempt`. The account must have accepted the Llama 4 licence on `meta-llama/Llama-Prompt-Guard-2-86M`. Without either, `response.firewall = degraded` with the reason (logged `firewall_degraded`), and a candidate whose verdicts report scene text is sent to a person instead of being suppressed. The API image installs no `ml` extra, so add it (`uv sync --extra ml`) to turn the firewall on. |
| `KASA_PLUG_HOST` | The interlock plug (`/interlock/plug/resume {role}` restores power, owner-only and logged). It is connected at start-up, so the first HIGH cut is one round trip; a plug that does not answer is listed in `response.interlock_unreachable` and retried on first use. |
| `KASA_LIGHT_HOST` | The red strobe (`visual_signal`). |

HIGH rules act first: the interlock is cut and roles are notified before Glimmer runs, and a
verifier veto then sends a "Needs review" notification (never a silent drop). The alerts PWA
routes (`GET /alerts`, `POST /alerts/{id}/respond`, `POST /ack`) are owner-only; escalation timers
tick once a second.

**Hard-negative memory.** An upheld worker dispute and an owner correction (`POST /correction`)
each append a lookalike regression to `$RULECAM_REGRESSIONS_DIR/<rule_id>.jsonl` (default
`$RULECAM_DATA_DIR/rule_ci/regressions`, on the volume), which every later lookalike red-team run
of that rule tests again. Copy the files into `src/rulecam/rule_ci/regressions/` in a PR to make
them part of the package's own Rule CI.

## Runtime SAM cost

Hosted SAM 3.1 takes **one concept per request** (a text prompt is a single noun phrase; see
`clients/meta_hosted/sam_wire.py` and issue #5) and bills **at least 50 frames per request**
(`usage.video_frames_processed`, verified live). Measured on the pod before this control: one
forklift-proximity rule (person, forklift) on two cameras made one request per concept per 3 s
chunk per camera, about 4,000 billed frames a minute; each request took 2 to 10 s, the loop fell
30 s behind and dropped to keyframe mode. The runtime now spends SAM only where and when it can
matter:

1. **Camera scope.** Approving a plan gives each rule the cameras where the planner found it
   `observable` or `caveat` (the feasibility evidence), plus its zone and calibration cameras
   (`ActivePlan.rule_cameras`). A camera with no active rule is never segmented
   (`chunks_skipped_no_rule`). A rule with no camera evidence runs everywhere, as before, and
   the log says `active_rule_unscoped`. Plans approved before this change are unscoped until
   approved again.
2. **One perception step per chunk per camera.** All the concepts the camera's rules need are
   tracked together, each SAM prompt once (never the concept key and its prompt twice), in
   parallel (`RULECAM_RUNTIME_CONCEPT_WORKERS`), so a chunk costs the slowest request's latency,
   not the sum. The API cannot take two concepts in one request; batching one concept over
   several chunks would amortise the 50-frame minimum but add a chunk of latency to HIGH rules,
   so it is not done.
3. **Motion gate and static cache before any request.** On a still chunk the dynamic concepts of
   non-HIGH rules are not segmented (one keyframe every 30 s still runs;
   `chunks_skipped_still`). Static concepts (sink, door, rack) are segmented once per plan and
   camera and served from the cache; a `moved` camera refills it.
4. **Idle sampling.** While nothing happens on a camera, one chunk in N is segmented
   (`chunks_skipped_idle`): `RULECAM_IDLE_STRIDE_LOW`/`_MEDIUM` (default 3, about 9 s) and
   `RULECAM_IDLE_STRIDE_HIGH` (default 2, at most 2), the strictest severity on the camera wins.
   "Something happens" means a candidate opened, a dynamic concept was tracked, or, on a camera
   with a HIGH rule, the motion gate saw motion. Then every chunk is segmented until
   `RULECAM_IDLE_HOLDOFF_S` (30 s) after the last activity.
5. **Budget (dollars).** Each camera has an hourly dollar budget,
   `RULECAM_BUDGET_USD_PER_CAMERA_HOUR` (default 12, the former 1,000 hosted frames/min; 0 turns
   it off). Its spend rate is the hosted SAM bill of the last minute (billed video frames at
   $0.20/1,000 and single images at $2.50/1,000, `clients/meta_hosted/sam_cost.py`, counting only
   calls hosted SAM really served, fallbacks included), scaled to an hour, plus its share of the
   GPU pods (`RULECAM_GPU_USD_PER_HOUR`, split over the active cameras). At or above the budget
   the alarm goes up (`cost_budget_exceeded`; `/runtime/metrics` → `cost.alarm`; a red
   **BUDGET** badge in the UI header) and, if hosted spend is part of the rate, the camera
   degrades to one chunk in `RULECAM_SAM_BUDGET_STRIDE` (3) instead of falling behind. GPU time
   alone only raises the alarm: segmenting less would not lower it. It recovers by itself when the
   minute drains (`cost_budget_recovered`). `RULECAM_SAM_BUDGET_FRAMES_PER_MIN` (default 0, off)
   remains as an optional extra cap in billed frames.
6. **Pipelined chunks.** Hosted SAM takes 6 to 17 s per request and a camera delivers a chunk
   every 3 s, so a loop that segments one chunk at a time cannot keep up. Measured on `main`
   096a438 (one HIGH forklift rule, one camera, idle stride 2): lag 123 to 142 s, the loop stuck
   in keyframe mode, 3 chunks segmented in 3 minutes, the 8 vCPU API pod mostly idle. The loop
   now keeps up to `RULECAM_RUNTIME_PIPELINE_DEPTH` (default 4) chunks per camera in flight.
   Everything that decides *whether* to segment (lag, idle stride, motion gate, budget) runs when
   a chunk is submitted, in arrival order; only the SAM concept pass runs concurrently; a
   per-camera reorder buffer then hands results, strictly in chunk order, to tracking, the track
   store, rule evaluation, candidates, verification, actions and the evidence chain. The budget
   counts each in-flight chunk's expected bill (one request per dynamic concept) until its real
   bill replaces it, so depth never overspends it. A failed chunk is counted in `chunks_failed`
   and is a coverage gap; the chunks behind it carry on. When a camera's pipeline is full the
   next chunk waits for the oldest one, its lag grows and keyframe mode thins the chunks, as
   before; requests never queue past the depth. `1` is the old one-chunk-at-a-time loop.

   **Throughput.** A camera keeps up while `depth / latency >= 1 / interval`, where the interval
   is 3 s on a hot camera and 6 s at idle stride 2. Depth 4 sustains 4 / 6 s = 0.67 chunks/s at
   6 s latency (needs 0.33) and 4 / 12 s = 0.33 chunks/s at 12 s, so it keeps up with every chunk
   up to 12 s per request and with idle stride 2 up to 24 s. At 17 s it manages 0.24 chunks/s on
   a hot camera; lag then grows by about 1.3 s per chunk until keyframe mode (one in 3, 0.11
   chunks/s needed) takes over. The cost is concurrency, not frames: at depth 4 with two concepts
   a camera has up to 8 hosted requests open at once. With depth above 1 the tracker hands
   identities off by box IoU instead of box re-prompts: a re-prompt needs the previous chunk's
   tracks, which would put one more SAM round trip per chunk back on the ordered path. That also
   removes the re-prompt requests from the bill.
7. **Stage timings and the stall fix (P3-08).** Every segmented chunk is timed stage by stage
   (`runtime/timings.py`): `queued`, `admit`, `sam_queue`, `upload`, `sam_request`, `sam` (per
   concept), `segment`, `reorder_wait`, `stitch`, `rules`, `candidates`, `strip`, `chain`,
   `verify_queue`, `verify`, `decide` and `end_to_end` (chunk ready to in-order stage done). Each
   chunk's timeline is logged (`runtime_chunk_timing`, plus `runtime_verify_timing` per
   candidate) and `/runtime/metrics` serves p50/p95 per stage in `stages`. Measured on `main`
   after #92 (depth 4, hosted SAM): requests took 5 to 24 s but their replies were logged up to
   two minutes later, five SAM threads shared one core, and segment p50 was 127 to 150 s. The
   cause: `meta-sam-parser` decodes mask payloads in pure Python, about 1.5 us of CPU per mask
   pixel under the GIL (2 to 6 s per 30-frame reply of a person or forklift on the pod; 8
   threads decode no faster than 1). Every runtime thread, the HTTP reads of other requests and
   the event loop queued behind it. The runtime only needs a foot point, so its hosted SAM view
   returns boxes only and the foot point is the box's bottom centre
   (`RULECAM_RUNTIME_SAM_MASKS=true` brings masks back; probes and calibration always keep
   them). Also: Tier 1 verification now runs on its own pool after the candidate is chained, so
   a 10 s verdict never holds up later chunks or other cameras (act-first actions still run in
   order, before verification); admission, the SAM pass, the in-order stage and verification
   each have their own bounded thread pool instead of asyncio's shared default executor; and at
   most `RULECAM_SAM_MAX_CONCURRENCY` (default 8) runtime SAM calls are open at once across
   cameras, the rest wait in `sam_queue`.

**Safety trade-off.** A chunk that is not segmented is a coverage gap, never evidence of
absence: a rule's window is cut to start after the gap, and the rule is judged on the cut window
only if that still covers the history it needs (none for proximity/zone predicates; the seconds
of a `persist`, `absence` or `dwell`; a minute for `rate`; the whole window for `sequence`).
Otherwise it counts in `windows_blind`. For HIGH rules: on a still camera an event can only start
with motion, and motion switches that camera to every chunk at once, whatever the idle stride or
budget, so idle sampling adds no detection latency to a HIGH event that starts with movement. What
it can delay, by at most one chunk (3 s), is an event that starts without motion the gate can
see (for example a person standing still who is reached by a forklift too small to change 0.1% of
a 160 px frame). The budget never thins a camera with a HIGH rule while it is active. Set
`RULECAM_IDLE_STRIDE_HIGH=1` to segment every chunk on such cameras regardless of cost.

**Expected spend** for the measured case (one HIGH forklift rule, warehouse + kitchen camera,
30-frame 3 s chunks, 50 frames billed per request): before, 2 cameras x 20 chunks x 2 concepts x
50 = **4,000 billed frames/min**. After: kitchen **0**; warehouse **1,000/min** while still or
idle (every 2nd chunk) and **2,000/min** while something moves (every chunk; with
`RULECAM_RUNTIME_PIPELINE_DEPTH=1`, tracker box re-prompts for objects already in view add up to
one request per concept per chunk). The same
rule at medium severity: about 200/min on a still camera (gate keyframes only), 670/min while
idle with motion, and at most the 1,000/min budget while active.

`GET /runtime/metrics` (owner) shows it: `sam_calls`, `billed_frames`, `billed_frames_per_min`
per camera, `chunks_skipped_no_rule`, `chunks_skipped_idle`, `chunks_skipped_still`,
`chunks_skipped_budget`, `budget_exceeded`, `budget_degraded`, and for the pipeline `in_flight`,
`pipeline_depth`, `segment_latency_p50_s` and `segment_latency_p95_s` (last 200 segmentations),
next to the existing lag and keyframe fields (`lag_s` is the most-behind camera's).

**Cost per camera-hour.** `/runtime/metrics` → `cost` (also in the UI header and `GET /metrics` as
`rulecam_cost_*`): the budget, `alarm` and `over_budget`, everything spent so far (`usd`: hosted
SAM, GPU shares and `gpu_idle_usd`, the GPU time while no camera was active), the camera time it
covered, and `usd_per_camera_hour` overall and per camera with each camera's current
`usd_per_hour`. Set `RULECAM_GPU_USD_PER_HOUR` to the GPU pods' combined price
(`scripts/deploy/runpod_status.sh` prints each pod's $/h), or the GPU part is not counted.

**Idle GPU stop (optional, off by default).** `RULECAM_IDLE_STOP_GPU_AFTER_S=1800` stops the GPU
pods (`RULECAM_IDLE_STOP_GPU_PODS`, default `rulecam-glimmer,rulecam-sam`, names or ids) through
the RunPod REST API after 30 minutes in which no camera had a rule of the active plan and a
recent chunk; once per idle period, retried every 5 minutes if RunPod refuses. A candidate still
waiting for Tier 1 (`verify_pending`) also counts as activity, so Glimmer is never stopped under a
verification in flight, nor while any camera of the active plan is sending chunks. It needs a
RunPod API key in a file on the volume:

```bash
# on the API pod (the key is never printed, logged or put in an error)
umask 077; cat > /workspace/rulecam/.runpod_api_key   # paste, then Ctrl-D
echo 'RULECAM_RUNPOD_API_KEY_FILE=/workspace/rulecam/.runpod_api_key' >> /workspace/rulecam/.env
echo 'RULECAM_IDLE_STOP_GPU_AFTER_S=1800' >> /workspace/rulecam/.env
```

Its state is in `cost.idle_stop` (`idle_s`, `stopped`, `last_error`). Nothing starts the pods
again automatically: run `scripts/deploy/runpod_up.sh` before the next session. While they are
stopped the runtime falls back to hosted SAM (and its bill, which the dollar budget then limits)
and the planner skips its Glimmer audit. A candidate is never dropped meanwhile: see "When Tier 1
fails" below.

## When Tier 1 fails (verifier down)

A Tier 1 verification that gives no verdict (the Glimmer pod is stopped or unreachable, a timeout,
a 5xx, a reply that is not the schema twice, an unreadable strip) is **never silent**
(`verify.guard`, P3-05):

1. **Bounded retry.** A transient failure (no answer, 429/5xx, timeout) is retried
   `RULECAM_VERIFY_RETRIES` times (default 1) with backoff, and not at all once the first attempt
   took 10 s or more. The Glimmer client's own retry (2 attempts, 60 s timeout) runs inside each.
2. **An explicit decision.** The candidate gets a `verdict` record with verdict `unsure` and a
   reason starting `verifier_error: ` (no contract change), then a decision record: **human
   queue**, alert on, reason `verifier unavailable — <error>`, whatever the severity. HIGH rules
   already acted first; a person now reviews them. LOW and MEDIUM are never suppressed on a failed
   verification. The human-queue notification is titled `Needs review: verifier unavailable — …`
   (the rule's notify/escalate roles, or `site_lead` when the rule has neither). With Tier 2
   configured and a rule that allows it (`spark-open-on-disagree`), Spark Open reviews the
   candidate first: if it confirms, the alert goes out (`auto`); anything else still goes to a
   person.
3. **Circuit breaker.** After `RULECAM_VERIFY_BREAKER_FAILURES` (3) transient failures in a row the
   circuit opens: candidates go straight to the human queue (no call, no timeout) for
   `RULECAM_VERIFY_BREAKER_COOLDOWN_S` (30 s, doubling on each failed trial up to 5 minutes). Then
   one candidate is let through as a trial; while open, the API also probes llama-server's
   `/health` every 10 s and a healthy answer lets the next candidate through. A successful verdict
   closes the circuit.

`/runtime/metrics` shows `verifications_failed` (candidates decided without a Tier 1 verdict),
`verifier_down` and `verifier` (`state`, `consecutive_failures`, `failures`, `fast_failures`,
`opens`, `last_error`, `retry_in_s`); Prometheus has `rulecam_runtime_verifier_down` and
`rulecam_runtime_verifications_failed`. The UI header shows a red **VERIFIER DOWN** badge while the
circuit is not closed, and the monitor's verifier panel marks each such event "Needs review:
verifier unavailable — …".

## Runtime perception modes (image keyframes + tracker)

`RULECAM_RUNTIME_SAM_MODE` picks how the continuous runtime perceives (PLAN-9.5 WS2b/WS2c):

- **`image` (default):** each 3 s chunk is segmented on its *keyframes* only, `RULECAM_KEYFRAME_FPS`
  per second (default 2, allowed 2 to 10; per camera `RULECAM_KEYFRAME_FPS_CAMERAS=dock=4,kitchen=3`).
  The keyframes are decoded once, in memory (JPEG, never written to disk), and SAM 3.1 **image** mode
  finds every concept on each. An open-source multi-object tracker, Roboflow `trackers` ByteTrack
  (Apache-2.0) with buffered IoU, joins them into tracks whose ids survive chunk borders; the rules
  engine interpolates between keyframes, so `moving`, speeds and operator exclusion work unchanged.
  Static concepts come from the static cache (one keyframe per plan and camera), and the motion
  gate, idle sampling and budget apply as before.
- **`video`:** the previous path: SAM 3.1 video per chunk and concept, IoU hand-off between chunks.

Probes, surveys, Rule CI and evidence re-checks always use hosted SAM 3.1 **video**, in both modes.

Image-mode fallback chain, each step counted at `GET /metrics/perception`: our GPU pod's
`POST /segment_image` (one call per chunk with every keyframe and concept; `SAM_BASE_URL`) →
hosted SAM 3.1 image requests (one per keyframe and concept, at most `RULECAM_SAM_MAX_CONCURRENCY`
open at once) while the pod fails (`sam_fallbacks`, `sam_fallback_to_hosted` in the log, retried
after 60 s). On Modal (no image endpoint) image mode is hosted only.

**Cost per active camera-hour** (3 s chunks at 10 fps, one rule with two concepts such as person and
forklift, every chunk segmented; idle sampling and the motion gate divide all rows alike):

| Runtime | Billed per chunk | $ per camera-hour |
|---|---|---|
| hosted video | 2 requests x 50 frames (30-frame chunks bill the 50 minimum) = $0.02 | **$24** |
| hosted image, 2 keyframes/s | 2 concepts x 6 images x $0.0025 = $0.03 | **$36** (1.5x) |
| hosted image, 4 keyframes/s | 2 x 15 images = $0.075 | $90 |
| self-hosted image (GPU pod) | nothing per call | the pod's hourly price, shared by every camera |

Hosted images are *not* cheaper than hosted video for 3 s chunks: an image costs as much as 12.5
video frames, and 6 keyframes already exceed the 50-frame minimum that pays for the whole chunk
(break-even is about 4 images per chunk). Image mode saves money only on our own GPU, where an image
needs no video session state (the 20 GB OOM was video sessions) and costs nothing per call; hosted
images are the fallback that keeps alerts flowing while the pod is down. The loop's budget counts
hosted images as 12.5 frames each (`billed_frames`), so a hosted-only image camera reaches the
default 1,000 frames/min budget with one concept and degrades as the budget section above says; a
self-hosted batch bills 0. The dollar budget and cost lines (`cost` above) see the same split:
each hosted image is recorded as one billed image (`PerceptionMetrics.record_image_call`), each
self-hosted batch as GPU time only. For a hosted-only site where cost matters more than latency, set
`RULECAM_RUNTIME_SAM_MODE=video`.

**Latency.** Video mode spends one hosted request per concept per chunk, about 10 s (p95 16 s). Image
mode spends one self-hosted call per chunk: about 0.5 MB of JPEGs over the RunPod proxy plus one
image-encoder pass per keyframe and one detector pass per concept on it. The video engine measured
about 3.5 s per 30-frame session, roughly 0.1 s per frame for encoder, detector and tracker, so a
2-concept chunk at 2 keyframes/s should take about 1 s of GPU and one L4 should carry 2 to 3 cameras;
measure it live (`stages.sam` / `sam_request` in `/runtime/metrics`, `image` in the SAM pod's
`/health`). Hosted images are many small requests (12 per chunk here) that run 8 at a time.

## Evidence strips (what Glimmer sees)

Every candidate gets a strip before Tier 1 (`runtime/strips.py`, P4-05): up to 6 keyframes
spread over the episode (at least its last 2 s), decoded from the chunks on disk with one ffmpeg
call per chunk straight into memory (no raw frame is written), at most 640 px wide. Each frame
shows the rule's objects as boxes (the subject thick and red), the rule's polygon zones and, when
the camera has a calibration in the approved plan, a ruler from the subject's foot to the nearest
other object with a one-metre tick. Every word Rulecam draws is in a black **annotation band**
appended below the frame (headed "RULECAM ANNOTATION (not part of the scene)"): the caption
(frame, offset, rule, measured value) and a colour legend (concepts, the ground distance, the
tick, zones). Glimmer is told the band is ours and not scene text, and `annotations.json` beside
the JPEGs lists the exact strings, which the verifiers remove from `scene_text` before the
Prompt Guard firewall, so our own labels never read as a tamper attempt. The JPEGs go to
`$RULECAM_DATA_DIR/strips/<camera>/<event>/`.

The plan's privacy mode applies: `standard` keeps the real frame with faces blurred;
`anonymous` and `strict` keep no person pixel (a clean plate, the rule's non-person objects
minus every person box, people drawn as skeletons or stick figures; `strict` is greyscale with
identical figures). Faces are only located to be blurred. The face detector is OpenCV's YuNet:
put `face_detection_yunet_2023mar.onnx` from opencv_zoo (check its licence when fetching) on the
volume and set `RULECAM_FACE_MODEL` to its path. Without it the log says `face_detector_missing`
at start-up and only the head regions of tracked people are blurred. A strip that cannot be
rendered is logged (`strip_render_failed`, `strip_chunk_failed`) and the candidate is verified
without frames.

## Glimmer pod (RunPod GPU)

- Image `ghcr.io/ggml-org/llama.cpp:server-cuda-b11669` (Muse Glimmer needs build ≥ b10353), RTX
  A6000 48 GB, 40 GB volume at `/workspace` (model cache), port `8080/http`.
- Docker args: `--hf-repo meta-models/Muse-Glimmer-30B-GGUF --hf-file
  Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf --mmproj-url
  https://huggingface.co/meta-models/Muse-Glimmer-30B-GGUF/resolve/main/mmproj-Muse-Glimmer-30B-Q4_K_M.gguf
  -a muse-glimmer-30b -ngl 99 -c 32768 -np 2 --host 0.0.0.0 --port 8080 --jinja --temp 1.0
  --top-p 0.95 --top-k 64`.
- Env: `LLAMA_API_KEY` (llama-server's name for `--api-key`; `LLAMA_ARG_API_KEY` is **not** read)
  and `LLAMA_CACHE=/workspace/llama-cache`. Without the key the GPU is open to anyone with the URL.
  The API sends the same key as `GLIMMER_API_KEY`.

### Serverless Glimmer (RunPod load balancer)

Glimmer can also run as a RunPod serverless **load-balancing** endpoint, beside serverless SAM
("Serverless SAM" below) and on the same network volume. It fits a 24 GB RTX 4090: the GGUF
header gives 52 layers, 2 KV heads of 128 and a 2,048-token sliding window on 39 of them, so the
KV cache of 64k context is about 1 GB next to 16.7 GB of weights and the 1.4 GB vision
projector.

- **Image**: the stock `ghcr.io/ggml-org/llama.cpp:server-cuda-b11669`, entrypoint
  `bash /runpod-volume/rulecam/scripts/glimmer_pod/serverless.sh`, port `8080/http`, env
  `PORT=8080`, `PORT_HEALTH=8080`, `GLIMMER_API_KEY` (`{{ RUNPOD_SECRET_rulecam_glimmer_api_key }}`),
  `GLIMMER_PARALLEL=4`, `GLIMMER_CTX=65536` (16k per slot, as on the pods).
- **Front**: `serverless.sh` starts llama-server on `127.0.0.1:8081` without `--api-key` and
  runs `rulecam.clients.open_weights.llama_edge` on `PORT` with the standalone Python from the
  volume. The proxy answers the load balancer's `GET /ping` (204 while llama-server loads, 200
  when its `/health` is 200, 503 if it dies after being ready), checks the Glimmer key
  (`X-Rulecam-Key`, or a Bearer token) and forwards everything else unchanged.
- **Volume**: `scripts/glimmer_pod/stage.sh` (any pod mounting the volume at `/runpod-volume`, no
  GPU) downloads the public GGUF and projector into `/runpod-volume/glimmer` (18 GB, about two
  minutes) and a standalone Python if SAM's staging has not.
- **Endpoint**: GPU pool `ADA_24`, `minCudaVersion` 12.8, workers min 0 / max 3, idle timeout
  300 s, scaler `REQUEST_COUNT` 4, FlashBoot on.
- **API pod**: `GLIMMER_BASE_URL=https://<endpoint-id>.api.runpod.ai/v1`, no
  `GLIMMER_EXTRA_BASE_URLS` (the endpoint scales out itself), and a RunPod API key in
  `/workspace/rulecam/.glimmer_edge_key` (mode 600) that `start.sh` exports as
  `GLIMMER_EDGE_KEY`; the client then sends the Glimmer key in `X-Rulecam-Key`.

Measured on 2 Nov 2026 from the API pod (US-IL-1), against an L40S pod:

| | Serverless (RTX 4090) | Pod (L40S) |
|---|---|---|
| Cold start (the first request waits) | 144 s the first time (2 min of it loading 18 GB from the volume); 36 s on a later start | none while running |
| Text answer, 64 tokens | 1.8 to 2.2 s | 1.9 to 2.1 s |
| One store frame (975 prompt tokens) | 4.0 to 6.0 s | 3.7 to 4.4 s |
| Generation | 42 to 50 tokens/s | 40 tokens/s |
| Live Tier 1 checks, warm, 4 slots | 19.6 and 24.3 s | 21 to 25 s (two pods) |

With 2 slots per worker the checks queued behind the caption memory, which keeps one request
open while cameras stream, and took 31 to 76 s; 4 slots fixed that. Four simultaneous 6-frame
requests (4,000 tokens each) finish in 9, 9, 19 and 19 s with 2 slots and 16, 16, 16 and 24 s
with 4: image prompts are compute-bound, so slots help mixed traffic, not raw throughput. Once,
right after the worker limit went from 0 to 3, a request sent during the cold start waited at
the load balancer until the client's timeout while another worker served other requests; the
client's 60 s timeout and retry cover that. Cost: $1.34/h per running worker, like SAM.

## Self-hosted SAM 3.1

Why: with hosted SAM on the continuous runtime, each call took **6 to 17 s** live and billed
**at least 50 frames**, so the loop could not keep up with 3 s chunks (**lag 142 s**, keyframe
mode). The router already intends self-hosted SAM 3.1 for the continuous runtime
(`perception/router.py`); this runs it on our own GPU pod instead of Modal.

What runs where:

- GPU pod: `rulecam.perception.sam_server`, a stdlib HTTP server around `Sam31Engine`
  (`facebook/sam3.1`, `sam3` at the commit pinned in `sam_worker.SAM3_COMMIT`). `GET /health` is
  open; `POST /segment` (video, offline uses) and `POST /segment_image` (the runtime's keyframes,
  `{"request": <SamImageRequest JSON>, "images_b64": [...]}`) need
  `Authorization: Bearer <SAM_API_KEY>`. Image mode runs the detector of the already loaded SAM 3.1
  model through `sam3`'s `Sam3Processor` (`Sam31ImageEngine`): no second checkpoint, no session
  state, memory released and guarded after every call. `SAM_IMAGE=0` turns it off (404),
  `SAM_IMAGE_THRESH` (0.1) is the lowest confidence it keeps; each request cuts higher at its
  `min_confidence` (0.5 unless the runtime tracker asks for its low-confidence floor,
  `RULECAM_TRACKER_LOW_CONFIDENCE`, 0.1, which only ByteTrack's second pass uses). Objects of a
  request's `embed_concepts` (people, when the API pod runs BoT-SORT) also get a 512-d OSNet-AIN
  person re-ID vector (`reid_worker`): BoxMOT 26.0.0 is installed in the GPU venv without its
  numpy >= 2 pin, and the weights `osnet_ain_x1_0_msmt17.pt` live at
  `$(dirname $HF_HOME)/reid/` (`SAM_REID_WEIGHTS` overrides; `SAM_REID=0` turns re-ID off;
  without BoxMOT or the file the server logs `reid_unavailable` and serves boxes only).
  `scripts/sam_pod/setup.sh` (also `--stage` for the serverless volume) and
  `deploy/sam-entrypoint.sh` fetch the weights once. On the API pod, people are tracked by
  BoT-SORT (`RULECAM_PERSON_TRACKER=botsort`, the default) only in prototype mode and only with
  `uv sync --extra reid`; otherwise ByteTrack, as before. `/embed` serves the same model for
  `"model": "person"`: Ask's identity memory (prototype mode only) joins people's track fragments
  with it and keeps DINOv2 for objects (`RULECAM_ASK_PERSON_REID`, on by default; a pod without
  the model makes people fall back to DINOv2). `SAM_IMAGE_BATCH` (0, off) encodes up to that
  many keyframes of a call in one backbone pass (`set_image_batch`) and grounds each concept per
  image (`FindStage.img_ids`); `/health` reports it as `image.batch`. Turn it on only after a GPU
  check that its boxes match the default and that it is faster. Image calls wait in their own queue,
  `SAM_IMAGE_MAX_WAITING` (8), for the same GPU. `/health` reports `image` (loaded, waiting,
  calls, guard trips). `POST /segment_crops` (add-on 3.3, same key) takes one frame and the head
  or hand boxes to enlarge, `{"request": {"concepts", "crops", "include_confidence"},
  "image_b64": ...}`, and runs the same loaded detector on each crop (`crop_worker.CropEngine`,
  no new weights) with its own threshold, `SAM_CROP_THRESH` (0.3); `/health` reports `crops`.
  `POST /geometry` (add-on 3.5, same key) runs MoGe-2 (`geometry_worker.MogeEngine`,
  `Ruicheng/moge-2-vitl-normal`, loaded on first use from `MOGE_MODEL`) on one frame,
  `{"request": {"grid": 96}, "image_b64": ...}`, and answers a metric point map on a grid plus
  the camera intrinsics; the planner turns it and SAM's floor mask into a ground calibration
  (`RULECAM_CALIBRATION_MODEL`, `auto` by default). It is served only when the `moge` package is
  installed (`scripts/sam_pod/install_moge.sh`, which also caches the 1.3 GB weights); `/health`
  reports `geometry`.
  The body is
  `{"request": <SamRequest JSON>, "video_b64": ...}` (32 MB limit, `SAM_MAX_BODY_BYTES`), the
  answer the `SamResponse` JSON. One call runs on the GPU at a time; up to `SAM_MAX_WAITING` (2)
  wait, the rest get 503 at once. The model loads once, before the port opens.
- API pod: `HttpSamWorker` (`perception/sam_runtime.py`) posts each runtime chunk there (60 s
  timeout, one retry on 503 or a dropped connection). Runtime perception is self-hosted when
  `SAM_BASE_URL` and `SAM_API_KEY` are set; they win over the Modal tokens, and without either
  runtime perception stays hosted. Planner probes, surveys and Rule CI always stay on hosted SAM.
- Fallback: any self-hosted failure (pod down, wrong key, queue full, engine error, malformed
  answer) sends runtime calls to hosted SAM for 60 s, then self-hosted is tried again. Each outage
  logs `sam_fallback_to_hosted` (and `sam_self_hosted_recovered` when it is back) and counts in
  `sam_fallbacks` at `GET /metrics/perception`; the calls it served show as `sam_calls_hosted`.
  `perception_built` in the API log says `self_hosted_worker=http` when the pod is in use.

**Gated checkpoint.** `facebook/sam3.1` is gated on Hugging Face: the account behind `HF_TOKEN`
must have requested and been granted access on the model page first, or the download fails with
401/403. The token is only needed once, to fill the cache on the volume; the server then runs
with `HF_HUB_OFFLINE=1` and never sees it.

GPU pod setup:

1. Create the pod: a RunPod PyTorch CUDA image with SSH (e.g.
   `runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04`; the venv brings its own
   Python 3.12 and `torch==2.10.0` cu128 wheels, so the host driver must support CUDA 12.8),
   RTX A6000 48 GB (an L4/A10G-class 24 GB GPU is enough), 40 GB volume at `/workspace` (venv
   about 10 GB, checkpoint cache), ports **`8090/http, 22/tcp`**. Do not expose 8090 as a TCP
   port: the server speaks plain HTTP and the RunPod HTTPS proxy is what keeps the key encrypted.
2. Copy the code: `git archive origin/main | ssh … 'mkdir -p /workspace/rulecam && tar -x -C
   /workspace/rulecam'`.
3. Write the two secret files on the pod yourself (mode 600, never in argv, chat or git):
   `/workspace/.hf_token` (the Hugging Face token) and `/workspace/.sam_api_key` (a fresh random
   key, e.g. `umask 077; openssl rand -hex 32 > /workspace/.sam_api_key`).
4. Run `/workspace/rulecam/scripts/sam_pod/setup.sh` once over SSH. It is idempotent: it creates
   `/workspace/sam-venv` (uv, Python 3.12, torch cu128, `sam3` at the pinned commit, opencv,
   numpy < 2, pydantic), checks that `sam3` imports and CUDA is visible, downloads
   `config.json` and `sam3.1_multiplex.pt` into `/workspace/hf-cache`, then starts the server with
   `setsid nohup` on port 8090 (log `/workspace/sam.log`) and waits for `/health`. Re-running it
   skips every finished step; `setup.sh --restart` restarts the server after a code update.
5. Auto-start: set the pod's `dockerStartCmd` (RunPod REST API, `PATCH /v1/pods/<id>`) to
   `bash -c "(/workspace/rulecam/scripts/sam_pod/boot.sh &) ; exec /start.sh"`. `boot.sh` runs
   `setup.sh` in the background (log `/workspace/sam-setup.log`) while `/start.sh` brings up SSH.
6. Check: `curl https://<sam-pod>-8090.proxy.runpod.net/health` gives `{"status": "ok", …}`, and
   `POST /segment` without the key gives 401.

GPU memory, speed and accuracy knobs (GPU pod environment, read once at load):

- After every session (also a failed one) the engine runs `gc.collect()`,
  `torch.clear_autocast_cache()` and `torch.cuda.empty_cache()`, asks propagation not to keep
  per-frame masks (`evict_cached_frame_outputs`), and logs `sam_session_memory allocated_mb=…
  reserved_mb=… peak_mb=…`. Upstream `close_session` only empties the CUDA cache above 80 % device
  use and, by default, keeps every propagated frame's video-resolution masks on the GPU until the
  session closes; on an L4 the pod grew from 4 GB to 20–22 GB in a few calls and then failed every
  call with `OutOfMemoryError`. `scripts/sam_pod/soak.py` (stdlib only, run with the sam venv on
  the GPU pod) posts one chunk N times and prints `/health` memory after each call.
- `SAM_MEMORY_GUARD_GB` (default: half the device): when allocated memory is still above it after a
  session, the engine logs `sam_memory_guard`, closes any session the predictor still holds and
  frees the cache again. `GET /health` shows `memory` (allocated, reserved, peak, free and total
  bytes; `sessions`, `guard_trips`, `last_session`, `model_config`). Allocated memory should come
  back to the same level after every call; if `guard_trips` keeps rising, restart with
  `setup.sh --restart` and report the log.
- No frame downscaling: SAM 3.1 resizes every frame to 1008 × 1008 before the backbone, so smaller
  input frames save no GPU time. `SAM_COMPILE=1` builds the model with `torch.compile` and warms it
  up (upstream quotes about 2×; slower start, untested on our pod).
- Detection settings (defaults are upstream's): `SAM_DET_THRESH` (0.4), `SAM_NEW_DET_THRESH`
  (0.65, the score a detection needs to start a new object), `SAM_HOTSTART_DELAY` (15 frames),
  `SAM_HOTSTART_UNMATCH` (8) and `SAM_HOTSTART_DUP` (8). Set them in the
  environment of `setup.sh --restart` or as `KEY=value` lines in `/workspace/sam.env` (read on
  every start, so they survive a reboot). Hotstart drops an object that goes
  unmatched for 8 of its first 15 frames, which is most of a 3 s chunk at 3–8 fps.
- Hosted parity check, on the API pod: `RULECAM_MODE=live uv run python
  scripts/sam_pod/compare.py --concept forklift --concept person <chunk.mp4 …>` prints per chunk
  and concept the detections, frames with detections, latency and frame agreement for hosted and
  self-hosted SAM, plus the self-hosted `/health` memory before and after (hosted calls are
  billed).

API pod: add `SAM_BASE_URL=https://<sam-pod>-8090.proxy.runpod.net` to `.env`, write the same key
to `/workspace/rulecam/.sam_api_key` (mode 600) and have `start.sh` export it as `SAM_API_KEY` like
the other key files, then restart the API. The key never goes in `.env`, `NEXT_PUBLIC_*`, argv or
logs.

**Security.** The server refuses to start without `SAM_API_KEY`, compares the Bearer key in
constant time, and answers 401 before reading a body. Only `/health` is open, and it reveals the
model version, whether the GPU is busy, GPU memory counters and the detection settings, nothing
else. Keep it behind the RunPod HTTPS proxy;
no shortcut that trusts every forwarder (the `--forwarded-allow-ips="*"` kind) and no open port
without the key. Rotate the key by rewriting both 600 files and restarting both sides.

**Cost.** An RTX A6000 pod is about $0.59/h (like the Glimmer pod) whether it serves one camera or
several, plus about $2.80/month for a 40 GB volume. Hosted SAM bills `max(50, frames)` per request
at $0.20 per 1,000 frames: the 1,000 to 2,000 billed frames/min of one HIGH rule on one active
camera ("Runtime SAM cost" above) is $12 to $24 per camera-hour. `GET /metrics/perception` shows
the self-hosted calls' GPU-seconds (priced at `DEFAULT_GPU_USD_PER_HOUR`) next to the
hosted-equivalent cost. Stop the pod when nobody is viewing (`runpodctl pod stop <id>`); the venv
and checkpoint stay on the volume, so the next boot only loads the model. The runtime falls back
to hosted SAM while it is stopped, so stop it only together with the runtime, or the hosted bill
returns.

### Serverless SAM (RunPod load balancer)

The same `sam_server` can run as a RunPod serverless **load-balancing** endpoint instead of a pod:
workers start on demand, scale to zero when idle, and a worker on a bad host is replaced by
RunPod instead of hanging at "awaiting container". Nothing is built or pushed: the endpoint runs
a stock public image (`runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04`) and everything
else lives on a network volume, which workers see at `/runpod-volume`.

- **Volume** (once): a network volume (25 GB is enough: venv 8.7 GB, checkpoints 3.4 GB, Python
  0.3 GB) in a data center with RTX 4090 serverless stock. Stage it from any pod that mounts it
  at `/runpod-volume`; no GPU is needed:

  ```bash
  RULECAM_DIR=/runpod-volume/rulecam SAM_VENV=/runpod-volume/sam-venv \
    HF_HOME=/runpod-volume/hf-cache UV_PYTHON_INSTALL_DIR=/runpod-volume/uv-python \
    HF_TOKEN_FILE=/root/.hf_token scripts/sam_pod/setup.sh --stage
  ```

  after copying the code to `/runpod-volume/rulecam` (`git archive HEAD src scripts`). Keep the
  Hugging Face token on the staging pod's own disk, not on the volume; workers never need it.
  A code update is the same copy from a pod mounting the volume; new workers pick it up.
- **Secret**: the SAM key as a RunPod secret (`rulecam_sam_api_key`), referenced in the endpoint
  env as `{{ RUNPOD_SECRET_rulecam_sam_api_key }}`.
- **Endpoint**: type `LOAD_BALANCER`, the image above with entrypoint
  `bash /runpod-volume/rulecam/scripts/sam_pod/serverless.sh`, port `8090/http`, env
  `PORT=8090`, `PORT_HEALTH=8090`, `SAM_API_KEY`, GPU pool `ADA_24`, `minCudaVersion` 12.8, the
  volume mounted, workers min 0 / max 2, idle timeout 300 s, scaler `REQUEST_COUNT` 6, FlashBoot
  on. `GET /ping` answers 204 while SAM loads and 200 once it serves.
- **API pod**: `SAM_BASE_URL=https://<endpoint-id>.api.runpod.ai` in `.env`, and a RunPod API key
  in `/workspace/rulecam/.sam_edge_key` (mode 600) that `start.sh` exports as `SAM_EDGE_KEY`.
  RunPod's edge checks that key in `Authorization`, so the client then sends the SAM key in
  `X-Rulecam-Key`, and the worker still refuses requests without it. Prefer a RunPod API key
  restricted to this endpoint.

Measured on 2 Nov 2026 from the API pod in the same data center (US-IL-1), one RTX 4090 each:

| | Serverless | Pod |
|---|---|---|
| Cold start (the first request waits) | 49 s (container start 2 s, model load 41 s) | none while running |
| One keyframe, two concepts (median, warm) | 0.81 s | 0.62 s |
| Six keyframes (one chunk) | 1.9 to 2.0 s | 1.6 s |
| `/embed`, 8 crops (warm) | 0.15 to 0.26 s | 0.26 s |

Cost: $1.34/h per running worker (4090 serverless pool) against $0.89/h for a 4090 pod, plus
about $0.06/day for the 25 GB volume. Workers bill from model load until the idle timeout after
the last request, so a camera running all day costs more than a pod; nights and quiet hours cost
nothing. During a cold start, requests wait at the load balancer (up to 2 minutes) instead of
failing: in a live checkout run that started 20 s after the worker had scaled to zero, the first
four chunks waited 23 to 63 s, none fell back to hosted SAM, and the next chunk took 2.0 s
(6 keyframes, 3 concepts). A longer idle timeout, or one warm worker (`workers.min` 1, billed
around the clock), avoids that wait at the start of a demo.

**Warm-up and keep-alive** (both endpoints, from any machine with `RUNPOD_API_KEY` set):
`scripts/ops/warm.sh on` sets `workers.min` 1 on SAM and Glimmer and waits until both `/ping`
answer 200; `off` sets it back to 0 (do it right after the demo: a warm worker bills $1.34/h);
`status` prints min, max, idle timeout and workers by status. RunPod lowers `workers.max` on an
endpoint without requests (to 2 after 3 days, to 0 after 7, until raised by hand), so
`warm.sh keepalive` sends one authenticated `/ping` to each and prints a loud warning when
`workers.max` is under 2 (SAM) or 3 (Glimmer). On the API pod, `scripts/ops/keepalive_loop.sh`
runs it once a day for 16 days (key from `/workspace/rulecam/.runpod_api_key`):
`setsid nohup bash /workspace/rulecam/scripts/ops/keepalive_loop.sh >> /workspace/keepalive.log
2>&1 < /dev/null &`.

### More GPU data centers and types (US-NC-1, US-TX-3)

On the night of 3 to 4 Nov US-IL-1 had no RTX 4090 left for serverless workers: SAM and Glimmer
could not start, SAM calls waited 120 s and fell back to hosted SAM. Now:

- **Data centers.** SAM runs in US-NC-1 and US-TX-3 (volumes `4q7tspgi9t`, `ysopmf0fjp`);
  Glimmer in US-IL-1, US-NC-1 and US-TX-3 (`11ymk65bni` too). A worker mounts the volume of the
  data center it lands in. SAM left US-IL-1 because that volume still has the code from before
  the re-ID autocast fix and no pod or worker could be placed there to update it.
- **GPU types.** Both endpoints take the pools ADA_24 (RTX 4090, $1.34/h), AMPERE_24 (A5000 /
  3090 / L4, $0.75/h), AMPERE_48 (A6000 / A40, $1.22/h) and ADA_48_PRO (L40S / L40 / 6000 Ada,
  $1.75/h): the 17 GB Glimmer GGUF and SAM 3.1 fit all of them. RTX 5090 is left out (Blackwell
  needs newer CUDA builds than the images have).
- **Staging.** The new volumes were staged from a 4090 pod mounting each at `/runpod-volume`:
  the code of `p1/final-integration` (faf6039) untarred into `/runpod-volume/rulecam`, then
  `scripts/sam_pod/setup.sh --stage`, `scripts/sam_pod/install_moge.sh` and
  `scripts/glimmer_pod/stage.sh` (about 6 minutes each). They have no `sam.env`, so SAM runs with
  the code defaults. Volumes do not sync: ship code to every one.
- **Standby Glimmer** `kkj76vah84y5uj` (`rulecam-glimmer-standby`): the same llama.cpp build
  with no volume (`scripts/glimmer_pod/anywhere.sh` downloads the weights on a cold start, about
  6 minutes), so it can run in any data center with stock. The API tries it when the primary
  gives no usable answer (`GLIMMER_STANDBY_BASE_URL=https://kkj76vah84y5uj.api.runpod.ai/v1`,
  same keys), then the hosted fallback. Keep it warm for demos (`demo_warm.sh` does).
- **Shorter wait before hosted SAM.** `SAM_HTTP_TIMEOUT_S` (default 60) and `SAM_HTTP_ATTEMPTS`
  (default 2) bound one self-hosted SAM call; with `25` and `1` the runtime falls back to hosted
  SAM after 25 s instead of 2 minutes.
- Account limit: max workers across all endpoints is 10 (SAM 2, Glimmer 3, standby 2, Llama
  Guard 2, Spark Open 1).

### Serverless Muse Spark Open (Tier 2, plan audit, Ask review)

Muse Spark Open is Meta's 816B-parameter mixture-of-experts model (about 42B active). Baseten,
where the code first pointed, never gave us a key, so it runs on our own RunPod endpoint
`hljmhlgjyaacyv` (`rulecam-spark-open`): llama.cpp `full-cuda-b12004` (the first build with the
`muse-spark` architecture is b11681), the 4-bit GGUF `ggml-org/Muse-Spark-Open-816B-A42B-GGUF`
(Q4_K_S, 470 GB in 22 parts), one worker with 8 x H100 80 GB in EUR-NO-2 (the only data center
with 8-GPU stock that night), and a 520 GB high-performance network volume `uhe04aj7bm`.

- The start script is `scripts/spark_open_pod/serverless.sh`. The endpoint has no code volume,
  so the script and `llama_edge` reach the worker as env vars (`scripts/spark_open_pod/
  endpoint_env.sh` prints them). The first start downloads the weights onto the volume
  (resumable); later cold starts only load them, about 10 minutes.
- Same key scheme as Glimmer: the RunPod key in `Authorization`, the model key (the RunPod
  secret `rulecam_glimmer_api_key`) in `X-Rulecam-Key`.
- API settings: `SPARK_OPEN_BASE_URL=https://hljmhlgjyaacyv.api.runpod.ai/v1`,
  `SPARK_OPEN_API_KEY` (the Glimmer key) and `SPARK_OPEN_EDGE_KEY` (the RunPod key). They win
  over `BASETEN_*`, which stays as an option. `SPARK_OPEN_MODEL` is `muse-spark-open`.
- Cost: $5.39/h per H100, $43.12/h per running worker, plus the volume. Keep `workers.min` 0 and
  warm it only for demos (`scripts/ops/demo_warm.sh`).
- A hosted alternative: Routeway serves `muse-spark-open` ($1.25 / $4.25 per million tokens in /
  out) through the same client (`SPARK_OPEN_BASE_URL=https://api.routeway.ai/v1`, its key as
  `SPARK_OPEN_API_KEY`, no edge key), once the Routeway account has credit.

### Serverless Llama Guard 4 (plan-time rule screen)

`rulecam.clients.open_weights.llama_guard.screen_rule_text` sends an owner's rule request to
Llama Guard 4 12B and refuses it when the model flags S7 Privacy or S10 Hate (rules about who
people are). Endpoint `vzi5ij5gclndjc` (`rulecam-llama-guard`): vLLM `v0.32.0` serving
`RedHatAI/Llama-Guard-4-12B-FP8-dynamic` (15 GB, downloaded to the container disk on a cold
start, so no volume and any data center), pools ADA_24, AMPERE_24, AMPERE_48, ADA_48_PRO,
$0.75 to $1.75/h per running worker, about 3 minutes cold. Start script:
`scripts/llama_guard_pod/serverless.sh`. Settings: `LLAMA_GUARD_BASE_URL=https://vzi5ij5gclndjc.api.runpod.ai/v1`,
`LLAMA_GUARD_API_KEY` (the Glimmer key), `LLAMA_GUARD_EDGE_KEY` (the RunPod key).

### Hosted Glimmer fallback

With `GLIMMER_FALLBACK_BASE_URL` set, a Glimmer call that gets no usable answer from our GPU (no
HTTP answer, 404, 429 or 5xx) is sent to a hosted OpenAI-compatible Glimmer instead
(`clients.transport.FailoverTransport`), with its own key, model name and the JSON schema in a
system message. The API logs `model_failover`. For Routeway: `GLIMMER_FALLBACK_BASE_URL=https://api.routeway.ai/v1`,
`GLIMMER_FALLBACK_API_KEY` (the Routeway key) and `GLIMMER_FALLBACK_MODEL=muse-glimmer-30b:free`
(works without credit; `muse-glimmer-30b` needs credit). Measured on 4 Nov with our URL dead on
purpose: the Tier 1 strip check answered through Routeway in 57 s.

## Checks

- `curl https://rulecam.vercel.app/rulecam-api/mode` → `{"mode":"live","badge":"LIVE"}`.
- `PUT /mode` through the public URL → 403 (also with a spoofed `X-Forwarded-For: 127.0.0.1`).
- `curl https://rulecam.vercel.app/rulecam-api/auth/me` → `"owner":false,…,"login_enabled":true`.
- Log in at `/login`; in the browser's dev tools the cookie `__Host-rulecam_owner` is on
  `rulecam.vercel.app` with HttpOnly, Secure and SameSite Strict (this also proves the Vercel
  rewrite passes `Set-Cookie` and `Origin` through); the planner, approve and questions controls
  appear; "Owner · Log out" removes them.
- `curl -o /dev/null -w '%{http_code}' https://rulecam.vercel.app/rulecam-api/evidence/<day>/chain`
  → 403; `/evidence/<day>/hashes` and `/evidence/<day>/root` → 200. The verify page
  (`/rulecam-api/verify/?day=<day>`) checks the hashes against the committed root for everyone
  and recomputes every record for the logged-in owner.
- Logged out, with `PUBLIC_DEMO` unset: `GET /rulecam-api/plans` → 403. With `PUBLIC_DEMO=true`: 200.
- Glimmer chat without the key → 401.
- SAM `POST /segment` without the key → 401; `GET /health` → `{"status": "ok", …}`.
- `curl https://<api-pod>-8000.proxy.runpod.net/metrics` → Prometheus text with
  `rulecam_dependency_up{service="glimmer"} 1` (and `sam` when self-hosted); serverless GPUs
  scaled to zero show `rulecam_dependency_state{…,state="cold-ready"} 1`, and
  `curl https://rulecam.vercel.app/rulecam-api/health` shows the same under `gpus`.
- `scripts/deploy/runpod_status.sh` → every pod `RUNNING` and `ok`.
- On the API pod: `uv run python -m rulecam.replay.preflight` (with the key files exported).

## Costs and shutdown

API pod about $0.06/h, Glimmer pod about $0.59/h (with a SAM pod, about $1.18/h for both; one
`--one-gpu` pod carries both for about $0.59/h, see "One GPU per site"), plus about $1.40/month
for the 20 GB network volume. Stop the GPU pod when nobody is viewing (`runpodctl pod stop <id>`): the model stays cached
on its volume.
