# Demo day runbook

What to do before and during a live demo of Rulecam, and what to do when a piece fails. The
commands run from a clone of the repo with `RUNPOD_API_KEY` set (a RunPod key; never paste it
into chat or a file in the repo). Times are counted back from the start of the demo (T-0).

## What runs where (4 Nov 2026)

| Model | Where | Endpoint | Warm cost |
|---|---|---|---|
| SAM 3.1 + DINOv2 + OSNet re-ID | RunPod serverless, 24-48 GB GPUs, US-NC-1 and US-TX-3 | `imz4qu1vzwp4z6` | $0.75-1.75/h |
| Muse Glimmer 30B (llama.cpp) | RunPod serverless, 24-48 GB GPUs, US-IL-1, US-NC-1, US-TX-3 | `z000o8uch2wj4i` | $0.75-1.75/h |
| Muse Glimmer 30B, standby | RunPod serverless, 24-48 GB GPUs, EU-RO-1 and EUR-IS-1 | `kkj76vah84y5uj` | $0.75-1.75/h |
| Muse Spark Open 816B-A42B (llama.cpp, Q4_K_S) | RunPod serverless, 8 x H100, EUR-NO-2 | `hljmhlgjyaacyv` | $43.12/h |
| Llama Guard 4 12B (vLLM, FP8) | RunPod serverless, any 24-48 GB GPU, any region | `vzi5ij5gclndjc` | $0.75-1.75/h |
| Llama Prompt Guard 2 86M | CPU, inside the API process | API pod `7nv0ifjo2tmk00` | none |
| Muse Spark 1.3 / 1.2, SAM 3.1 hosted, Voice | Meta Model API | `api.meta.ai` | per call |
| Hosted Glimmer (fallback only) | Routeway | `api.routeway.ai` | per call (`:free` model costs nothing) |

SAM and Glimmer workers read their code and weights from one network volume per data center:
`11ymk65bni` (US-IL-1, Glimmer only), `4q7tspgi9t` (US-NC-1) and `ysopmf0fjp` (US-TX-3). US-IL-1
often has no RTX 4090 left; the other data centers and GPU types are why the GPUs came back on
the night of 3 to 4 Nov.

## T-60 min

1. `scripts/ops/demo_warm.sh status`: every endpoint shows `max` at least 1 (SAM 2, Glimmer 3).
   If a `max` dropped to 0, raise it in the RunPod console (Serverless > endpoint > Edit).
2. `curl -s https://rulecam.vercel.app/rulecam-api/health`: `ok: true`, `revision` is the commit
   we meant to ship, `gpus.sam` and `gpus.glimmer` not `unhealthy`.
3. Decide whether Spark Open is in the demo. It is the expensive one ($43.12/h) and needs about
   18 minutes to load its weights (measured 4 Nov). If yes, warm everything now:

   ```bash
   scripts/ops/demo_warm.sh at 16:30        # the demo's start in UTC; warms 30 min before
   # or: SPARK=0 scripts/ops/demo_warm.sh at 16:30   (no Spark Open)
   ```

   It keeps every endpoint at one warm worker, pings each once a minute, prints a loud line
   with the fallback if one stops answering, and sets everything back to zero 2 hours after the
   demo (`HOLD_MIN`) or on Ctrl-C. Leave it running in its own terminal.

## T-15 min

1. Every endpoint answered `/ping 200` in the `demo_warm.sh` output.
2. Run the use-case preflight on the API pod (owner routes need the pod itself, or set
   `RULECAM_OWNER_PASSPHRASE`):

   ```bash
   uv run python scripts/ops/preflight_usecases.py --api http://localhost:8000 --skip-models
   ```

   and, from a machine with the GPU keys in its environment, the model chain:

   ```bash
   uv run python scripts/ops/preflight_usecases.py --models-only
   ```

   Green: works. Yellow: not deployed or nothing to check (fine). Red: fix or drop that part of
   the demo (see below).
3. Optional: `uv run python scripts/ops/model_proofs.py` for the full Spark Open proof (Tier 2
   on a strip, plan audit, Ask review, Llama Guard), about 2 to 3 minutes.
4. Open the pages you will show once, so their first load is not on stage.

## T-0

- Keep the `demo_warm.sh` terminal visible to whoever runs the demo: a `!!!` line means a model
  stopped answering, and it says what the product does about it.
- After the demo: Ctrl-C in that terminal (or `scripts/ops/demo_warm.sh down`), then
  `scripts/ops/demo_warm.sh status` shows `min=0` everywhere.

## When something fails

| Symptom | What happens by itself | What to do |
|---|---|---|
| SAM endpoint cold or out of GPUs | The runtime falls back to hosted SAM 3.1 per call after `SAM_HTTP_TIMEOUT_S` x `SAM_HTTP_ATTEMPTS` (set 25 x 1 for demos; `sam_fallback_to_hosted` in the API log, `sam_fallbacks` in `/runtime/metrics`) | Nothing for the demo. Afterwards check `demo_warm.sh status` for throttled workers |
| Glimmer endpoint down | A Tier 1 call that gets no answer goes to the standby endpoint (`GLIMMER_STANDBY_BASE_URL`), then to hosted Glimmer (`GLIMMER_FALLBACK_*`; 10 to 60 s a call); `model_failover` in the API log. If all fail, unverified candidates go to the human queue | Say "it waits for a person" and show the queue |
| Spark Open not warm or failing | Tier 2, plan audit and Ask review are skipped: Tier 1 decides alone and anything unsure goes to a person; Ask answers without the second check | Warm it 30 min earlier next time (`demo_warm.sh up`); do not wait for it on stage |
| Llama Guard 4 down | The plan-time screen is skipped; the planner's own guardrails still decline identity rules | Nothing |
| Prompt Guard 2 not loaded | `/runtime/metrics` shows `firewall: degraded`; candidates with scene text go to a person | Check `HF_TOKEN` on the API pod |
| Meta Model API errors | Ask and planning fail with a visible error | Show the recorded REPLAY demo (`/demo?demo=1`) |
| API pod down | Nothing works live | Restart is the cleanup agent's job (`/workspace/restart_api.sh`); meanwhile show REPLAY |

## Ship GPU code without S3 keys

`make ship-gpu` needs a RunPod S3 key. Without one, and when the volume's data center has no pod
stock, `scripts/ops/volume_stager.sh` does the same from a throwaway serverless worker on that
volume: the bundle goes over `runpodctl send`, the old code moves to
`/runpod-volume/rulecam/backup-<time>/`. The script's header has the steps. For a data center
with pod stock, a CPU or GPU pod mounting the volume at `/runpod-volume` is simpler (scp the
bundle, untar it into `/runpod-volume/rulecam`).
