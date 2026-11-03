---
name: rulecam-ops
description: What runs where in Rulecam's live stack (RunPod API pod, serverless SAM and Glimmer, network volume), how to deploy it (make ship-api, make ship-gpu), how to roll back, and the gotchas that have bitten us. Use before touching the live stack, deploying, warming the GPUs or debugging the API pod.
---

# Rulecam ops

The live stack as of 2 Nov 2026. `docs/deploy.md` has the long form; this is the cheat sheet.
No keys or key values live here or anywhere in the repo: they are files with mode 600 on the
API pod or RunPod secrets, and scripts read them from env vars.

## What runs where

| Piece | Where | ID |
|---|---|---|
| API (FastAPI + runtime loop) | RunPod CPU pod, code at `/workspace/rulecam` | pod `7nv0ifjo2tmk00` |
| SAM 3.1 | RunPod serverless load balancer, RTX 4090, US-IL-1 | endpoint `imz4qu1vzwp4z6` |
| Glimmer (llama.cpp `server-cuda-b11669`) | RunPod serverless load balancer, RTX 4090, US-IL-1 | endpoint `z000o8uch2wj4i` |
| GPU code, venvs, weights | network volume, US-IL-1, mounted at `/runpod-volume` | volume `11ymk65bni` |
| UI | Vercel, `https://rulecam.vercel.app` (API behind `/rulecam-api`) | |

- Both endpoints: workers min 0, idle timeout 300 s, $1.34/h per running worker. SAM max 2,
  Glimmer max 3. Workers start from `/runpod-volume/rulecam/scripts/{sam_pod,glimmer_pod}/serverless.sh`
  and run `/runpod-volume/rulecam/src`.
- Public base URLs: `https://<endpoint-id>.api.runpod.ai` (Glimmer adds `/v1`). RunPod's edge
  checks a RunPod key in `Authorization` (`SAM_EDGE_KEY` / `GLIMMER_EDGE_KEY`); the model key goes
  in `X-Rulecam-Key`.
- On the API pod: `.env` (non-secret settings) and the key files `.sam_edge_key`,
  `.glimmer_edge_key`, `.glimmer_api_key` (and others) in `/workspace/rulecam`; `start.sh`
  exports them. Restart script: `/workspace/restart_api.sh`. API log: `/workspace/api.log`.

## Rollback targets (kept stopped)

- SAM pod `lw4tci4g4yrdzt`; Glimmer pods `7opg96jcpn1tkj` and `343s9gol5qlp36`.
- On the API pod: `/workspace/start.sh.bak-pod` and `/workspace/env.bak-pod` (SAM on the pod),
  `/workspace/start.sh.bak-glimmer-pod` and `/workspace/env.bak-glimmer-pod` (Glimmer on the
  pods): the `start.sh` and `.env` from before the serverless switch.
- To go back to pods: start the pod(s), restore the matching `start.sh`/`.env` backups into
  `/workspace/rulecam`, run `/workspace/restart_api.sh` detached, check `/health`.

## Deploy

Ship only committed code that passed CI. Both commands ship `HEAD` (or `REF=<sha>`).

**API** (`make ship-api`, `scripts/deploy/ship_api.sh`):

```bash
runpodctl pod get 7nv0ifjo2tmk00          # public IP and the port mapped to 22 (changes on restart)
export RULECAM_API_SSH_HOST=<ip> RULECAM_API_SSH_PORT=<port>
# RULECAM_API_SSH_KEY defaults to ~/.runpod/ssh/runpodctl-ssh-key
make ship-api
```

Uploads `git archive` into `/workspace/releases/<sha12>`, links `.env`, key files and data dirs
from `/workspace/rulecam-shared`, builds the venv, switches the `/workspace/rulecam` symlink,
restarts detached and waits until `GET /health` reports `revision` = the SHA. Rolls back by
itself when it does not. Result: `/workspace/releases/ship-<sha12>.status`, log next to it.

Manual rollback of the API:

```bash
ln -sfn /workspace/releases/<old-sha12> /workspace/rulecam.next
mv -Tf /workspace/rulecam.next /workspace/rulecam
setsid nohup /workspace/restart_api.sh > /dev/null 2>&1 < /dev/null &
curl -s localhost:8000/health        # check "revision"
```

**GPU code** (`make ship-gpu`, `scripts/deploy/ship_gpu.sh`): needs a RunPod S3 API key in
`RUNPOD_S3_ACCESS_KEY_ID` / `RUNPOD_S3_SECRET_ACCESS_KEY`. Copies `src` and the two GPU script
dirs onto volume `11ymk65bni` via `https://s3api-us-il-1.runpod.io`. Running workers keep the old
code; the next cold start loads the new one (`scripts/ops/warm.sh off`, wait 300 s). Roll back
with `make ship-gpu REF=<old sha>`. `DRY_RUN=1` prints the writes.

**Warm-up** (`scripts/ops/warm.sh`, needs `RUNPOD_API_KEY`): `on` (min 1 on both, waits for
`/ping` 200), `off` (min 0; do it right after a demo), `status`, `keepalive` (one `/ping` each,
warns when RunPod's idle scale-down lowered `workers.max`).

## Gotchas

- `runpodctl` can report a pod as `awaiting_container` while it serves fine. Trust `/health`
  (`curl https://7nv0ifjo2tmk00-8000.proxy.runpod.net/health`), not that status.
- `pkill -f <pattern>` over SSH can match the SSH command itself (its argv contains the
  pattern) and kill your own session. Use a bracket pattern (`pkill -f "[u]vicorn rulecam.api.app"`)
  or `/workspace/restart_api.sh`.
- SSH to the API pod drops now and then: use `-o ServerAliveInterval=15`, and run anything
  long (restarts, deploys) detached with `setsid nohup … < /dev/null &`, never in the foreground.
- The track store writes one Parquet file per append and keeps many open: the API process needs
  `ulimit -n 65536` (set it in the start script before uvicorn).
- Never put the GPU URLs in an uptime monitor or poll their `/health`: each request wakes a
  worker and bills. `GET /health` on the API reads worker state from RunPod's REST API instead.
- RunPod lowers `workers.max` on an endpoint with no requests (2 after 3 days, 0 after 7): run
  `warm.sh keepalive` daily through the finals, and check `warm.sh status` on demo mornings.
- Cold starts: SAM about 50 s, Glimmer 36 s (144 s the first time). Warm both before a demo.
