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
- `start.sh` runs `uvicorn rulecam.api.app:app --host 0.0.0.0 --port 8000`. Do not add
  `--forwarded-allow-ips="*"`: it would let anyone spoof a loopback client past the owner check.
- Auto-start: the pod's `dockerStartCmd` is
  `bash -c "(/workspace/rulecam/boot.sh &) ; exec /start.sh"`, and `boot.sh` runs `start.sh`
  (log: `/workspace/api.log`). Set it with the RunPod REST API (`PATCH /v1/pods/<id>`); `runpodctl`
  has no flag for it.
- The SSH port changes on every restart: `runpodctl pod get <id>` shows the current one.

Update the API: `git archive origin/main | ssh … 'tar -x -C /workspace/rulecam'`, then
`uv sync --no-dev` and restart (`pkill -f "[u]vicorn rulecam.api.app"` then `./start.sh` via
`setsid nohup`, or restart the pod).

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
