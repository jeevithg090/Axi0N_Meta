# Deployment: Vercel UI + RunPod API + RunPod Glimmer

A view-only public deployment for showing the product. There is no owner login yet: everything that
changes state (mode switch, plan approval, pairing, runtime plan) only works from the API pod itself.

```
browser ──► https://rulecam.vercel.app  (Next.js, ui/)
              │  /rulecam-api/*  (Next rewrite)
              ▼
            https://<api-pod>-8000.proxy.runpod.net   (FastAPI, live mode)
              │  /ws  (read-only bus mirror; allowed for the PUBLIC_UI_URL origin only)
              ├─► Meta Model API (Spark, Voice, Image, SAM) via the replay cache
              └─► https://<gpu-pod>-8080.proxy.runpod.net/v1  (llama-server + Muse Glimmer 30B)
```

## Vercel (UI)

- Project `rulecam`, **Root Directory `ui`**, connected to the GitHub repo: every push to `main`
  deploys production, and every PR gets a preview.
- One environment variable: `NEXT_PUBLIC_RULECAM_API` = the API pod URL. It is shipped to browsers,
  so never put a secret in any `NEXT_PUBLIC_*` variable. Model keys belong on the API pod only.

## API pod (RunPod CPU)

- Image `runpod/base:1.4.0-ubuntu2204` (ships `uv`, Python 3.12, ffmpeg), 20 GB network volume at
  `/workspace`, ports `8000/http, 22/tcp`.
- Code in `/workspace/rulecam` (a `git archive` of `main`), deps with `uv sync --no-dev`.
- `/workspace/rulecam/.env` holds non-secret settings: `RULECAM_MODE=live`, data and evidence dirs
  on `/workspace`, `META_API_BASE_URL`, `GLIMMER_*`, `PUBLIC_TUNNEL_URL` (this pod's proxy URL),
  `PUBLIC_UI_URL=https://rulecam.vercel.app`.
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

Pair a phone (owner action, so it runs on the pod):

```bash
ssh -i ~/.ssh/runpod_ed25519 -p <port> root@<ip> 'curl -s -X POST localhost:8000/pair/token' | python3 -c 'import json,sys; print(json.load(sys.stdin)["url"])'
```

Open the printed URL on the phone (or scan the QR in `qr_png_base64`) before it expires.
- Auto-start: the pod's `dockerStartCmd` is
  `bash -c "(/workspace/rulecam/boot.sh &) ; exec /start.sh"`, and `boot.sh` runs `start.sh`
  (log: `/workspace/api.log`). Set it with the RunPod REST API (`PATCH /v1/pods/<id>`); `runpodctl`
  has no flag for it.
- The SSH port changes on every restart: `runpodctl pod get <id>` shows the current one.

Update the API: `git archive origin/main | ssh … 'tar -x -C /workspace/rulecam'`, then
`uv sync --no-dev` and restart (`pkill -f "[u]vicorn rulecam.api.app"` then `./start.sh` via
`setsid nohup`, or restart the pod).

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
5. **Budget.** When a camera's billed frames over the last minute reach
   `RULECAM_SAM_BUDGET_FRAMES_PER_MIN` (default 1,000; 0 turns it off), it degrades to one chunk
   in `RULECAM_SAM_BUDGET_STRIDE` (3), logged `budget_exceeded`, instead of falling behind. It
   recovers by itself when the minute drains (`budget_recovered`).

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
idle (every 2nd chunk) and **2,000/min** while something moves (every chunk; tracker box
re-prompts for objects already in view add up to one request per concept per chunk). The same
rule at medium severity: about 200/min on a still camera (gate keyframes only), 670/min while
idle with motion, and at most the 1,000/min budget while active.

`GET /runtime/metrics` (owner) shows it: `sam_calls`, `billed_frames`, `billed_frames_per_min`
per camera, `chunks_skipped_no_rule`, `chunks_skipped_idle`, `chunks_skipped_still`,
`chunks_skipped_budget`, `budget_exceeded`, `budget_degraded`, next to the existing lag and
keyframe fields.

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

## Checks

- `curl https://rulecam.vercel.app/rulecam-api/mode` → `{"mode":"live","badge":"LIVE"}`.
- `PUT /mode` through the public URL → 403 (also with a spoofed `X-Forwarded-For: 127.0.0.1`).
- Glimmer chat without the key → 401.
- On the API pod: `uv run python -m rulecam.replay.preflight` (with the key files exported).

## Costs and shutdown

API pod about $0.06/h, Glimmer pod about $0.59/h, plus about $1.40/month for the 20 GB network
volume. Stop the GPU pod when nobody is viewing (`runpodctl pod stop <id>`): the model stays cached
on its volume.
