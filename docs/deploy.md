# Deployment: Vercel UI + RunPod API + RunPod Glimmer

A public deployment for showing the product. Visitors can watch; everything that changes state
(mode switch, planning, plan approval, answering the planner, pairing, corrections, runtime plan)
and the full evidence log are owner-only: from the API pod itself (loopback, over SSH) or from a
browser logged in with the owner passphrase ("Owner login" below).

```
browser ──► https://rulecam.vercel.app  (Next.js, ui/)
              │  /rulecam-api/*  (Next rewrite)
              ▼
            https://<api-pod>-8000.proxy.runpod.net   (FastAPI, live mode)
              │  /ws  (read-only bus mirror; allowed for the PUBLIC_UI_URL origin only)
              ├─► Meta Model API (Spark, Voice, Image, SAM) via the replay cache
              ├─► https://<gpu-pod>-8080.proxy.runpod.net/v1  (llama-server + Muse Glimmer 30B)
              └─► https://<sam-pod>-8090.proxy.runpod.net     (sam_server + SAM 3.1, runtime only)
```

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

Update the API: `git archive origin/main | ssh … 'tar -x -C /workspace/rulecam'`, then
`uv sync --no-dev` and restart (`pkill -f "[u]vicorn rulecam.api.app"` then `./start.sh` via
`setsid nohup`, or restart the pod).

### Operator exclusion (forklift drivers)

A person riding a vehicle (forklift, pallet jack, cart, truck, ...) inside its box and moving with
it is that vehicle's operator, not a pedestrian: person-vehicle distance and buffer rules ignore
the ridden vehicle for them (`src/rulecam/rules/operators.py`). It is on for every rule. To count
riders as pedestrians for a rule, list its id in `RULECAM_OPERATOR_EXCLUSION_OFF` (comma-separated,
e.g. `RULECAM_OPERATOR_EXCLUSION_OFF=forklift-proximity`) and restart the API; each candidate's
evidence record keeps the flag it was decided with.

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

## Evidence strips (what Glimmer sees)

Every candidate gets a strip before Tier 1 (`runtime/strips.py`, P4-05): up to 6 keyframes
spread over the episode (at least its last 2 s), decoded from the chunks on disk with one ffmpeg
call per chunk straight into memory (no raw frame is written), at most 640 px wide. Each frame
shows the rule's objects as labelled boxes (the subject in red), the rule's polygon zones, a
caption (frame, offset, rule, measured value) and, when the camera has a calibration in the
approved plan, a ruler from the subject's foot to the nearest other object with its ground
distance and a one-metre tick. The JPEGs go to `$RULECAM_DATA_DIR/strips/<camera>/<event>/`.

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

## Self-hosted SAM 3.1

Why: with hosted SAM on the continuous runtime, each call took **6 to 17 s** live and billed
**at least 50 frames**, so the loop could not keep up with 3 s chunks (**lag 142 s**, keyframe
mode). The router already intends self-hosted SAM 3.1 for the continuous runtime
(`perception/router.py`); this runs it on our own GPU pod instead of Modal.

What runs where:

- GPU pod: `rulecam.perception.sam_server`, a stdlib HTTP server around `Sam31Engine`
  (`facebook/sam3.1`, `sam3` at the commit pinned in `sam_worker.SAM3_COMMIT`). `GET /health` is
  open; `POST /segment` needs `Authorization: Bearer <SAM_API_KEY>`. The body is
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
- Glimmer chat without the key → 401.
- SAM `POST /segment` without the key → 401; `GET /health` → `{"status": "ok", …}`.
- On the API pod: `uv run python -m rulecam.replay.preflight` (with the key files exported).

## Costs and shutdown

API pod about $0.06/h, Glimmer pod about $0.59/h, plus about $1.40/month for the 20 GB network
volume. Stop the GPU pod when nobody is viewing (`runpodctl pod stop <id>`): the model stays cached
on its volume.
