# Demo day runbook

What to do before and during a live demo of Rulecam, and what to do when a piece fails. Since
4 Nov 2026 (about 05:50 UTC) every model call goes to the Meta API, and the Rulecam API and the
site run on the team's Mac, public through a Cloudflare tunnel. We run no GPUs of our own, so
there is nothing to warm. The setup is in [`deploy.md`](deploy.md), "Meta API only". Times are
counted back from the start of the demo (T-0).

## What runs where (since 4 Nov 2026)

| Piece | Where | Job |
|---|---|---|
| The site | Vercel, `https://rulecam-live.vercel.app` | The pages; `/rulecam-api/*` is passed on to the API |
| Rulecam API | The team's Mac, public through a Cloudflare tunnel (`PUBLIC_TUNNEL_URL` in `.env.live`) | Everything the live site does |
| Muse Spark 1.3 | Meta API | Planner, Ask, the planner's guardrail pass, retail checks, and the Tier 1 check of every alert (through the Glimmer client) |
| Muse Wonder 1.0 | Meta API | Tier 2: dispute review, plan audit, Ask answer review (through the Spark Open client) |
| SAM 3.1 | Meta API (hosted) | Survey, probes, keyframes, counts |
| Muse Voice Transcribe, Muse Image | Meta API | Speech in, hearing warnings back; SAMPLE images |
| Muse Spark 1.2 | Meta API | Where the code uses it: video with narration |
| Muse Code | The local `muse` CLI on the Mac | New rule types |
| MMS-TTS | CPU on the Mac | Spoken warnings |

Not running:

- **Muse Glimmer 30B and Muse Spark Open.** Their clients now call Muse Spark 1.3 and Muse
  Wonder 1.0.
- **Llama Guard 4.** The planner's own guardrails still decline banned requests: a fixed
  pattern check, then Muse Spark.
- **Prompt Guard 2** is not loaded. `/runtime/metrics` shows `firewall: degraded`, and
  candidates with scene text go to a person.
- **Crowd watch.** The Jülich clips were only on the server we deleted, so `/usecases/crowd`
  shows its recorded runs.

## T-30 min

1. On the Mac, start the API with the Meta API settings in `.env.live` (not `.env`: the tests
   read `.env`, and `RULECAM_PROTOTYPE_MODE=true` or `RULECAM_MODE=live` there changes what they
   expect):

   ```bash
   uv sync
   set -a; . ./.env.live; set +a
   ulimit -n 65536; uv run uvicorn rulecam.api.app:app --host 127.0.0.1 --port 8000
   ```

   The tunnel watchdog keeps running on the Mac (`pgrep -fl tunnel_watchdog`; the site itself is
   on Vercel). It replaces a dead tunnel and redeploys the site within about 2 minutes. Keep the
   Mac awake and on the network for the whole demo.
2. Check that the public address answers:

   ```bash
   curl -s https://rulecam-live.vercel.app/rulecam-api/health
   ```

   Expect `ok: true` and, under `models`, `muse-spark-1.3` (Tier 1), `muse-wonder-1.0` (Tier 2)
   and `sam-3.1`, each on `meta-api`. If this fails but `http://127.0.0.1:8000/health` answers,
   the tunnel dropped: the watchdog fixes it within about 2 minutes (`data/tunnel/watchdog.log`;
   [`deploy.md`](deploy.md), "The site on Vercel").
3. Prove the models answer through Rulecam's own clients, in a shell with `.env.live` loaded
   (as above):

   ```bash
   uv run python scripts/ops/model_proofs.py          # Tier 1, Tier 2, plan audit, Ask review
   ```

   On 4 Nov, on this stack: Tier 1 on a warehouse strip 9.8 s (violation, p = 0.97), Tier 2
   6.1 s, plan audit 19.9 s, Ask answer review 7.7 s, all passed.

## T-15 min

1. Run the use-case preflight from the Mac, in a shell with `.env.live` loaded (requests from
   the same machine count as the owner):

   ```bash
   uv run python scripts/ops/preflight_usecases.py --api http://127.0.0.1:8000
   ```

   Green: works. Yellow: not deployed, or nothing to check. The metrics line is yellow while
   Prompt Guard 2 is not loaded; that is expected. Red: fix or drop that part of the demo (see
   below). For reference, on 4 Nov on this stack the warehouse verify chain (Tier 1 + Tier 2)
   took 11.3 s and a supermarket Ask answer 57.4 s.
2. Plan the warehouse rule beforehand. On 4 Nov the Spanish 4 m forklift rule took 146 s on
   this stack (survey 50.6 s, probe 59.8 s, gauge 6.0 s, ask 10.3 s, compile 0.2 s, audit 19.2 s,
   prove 0.1 s): too long to wait for on stage.
3. Open the pages you will show once, so their first load is not on stage.

## T-0

- If a step stalls for more than 10 seconds, say "this is the recorded run" and switch to the
  REPLAY page (`/demo?demo=1`).
- Afterwards there are no GPUs to scale down. The Meta API is paid per call, and the runtime
  keeps calling it while a camera or a looped clip feeds it.

## When something fails

| Symptom | What happens by itself | What to do |
|---|---|---|
| Meta API errors or is slow | Planning and Ask fail with a visible error. A Tier 1 check that gets no answer sends the candidate to the human queue | Say "it waits for a person" and show the queue, or show the recorded REPLAY demo (`/demo?demo=1`) |
| Tier 2 (Muse Wonder 1.0) failing | Tier 2, plan audit and Ask review are skipped and logged: Tier 1 decides alone and anything unsure goes to a person; Ask answers without the second check | Do not wait for it on stage |
| Prompt Guard 2 not loaded (the normal state now) | `/runtime/metrics` shows `firewall: degraded`; candidates with scene text go to a person | Nothing. To turn it on: `HF_TOKEN` with access to the model, and `uv sync --extra ml` ([`deploy.md`](deploy.md)) |
| A banned request | Llama Guard 4 is not run; the planner's own guardrails decline it | Nothing |
| Someone asks for crowd live | Crowd is not live now | Show the recorded runs on `/usecases/crowd` (REPLAY) |
| The Mac or the tunnel is down | Nothing works from the public address | Restart them; if the address changed, replace it everywhere. Meanwhile run the replay offline: `cd ui && npm ci && npm run dev`, then `localhost:3000/demo?demo=1` |

## Before 4 Nov: the RunPod setup (record)

The recorded runs of 1–4 Nov used this. None of it runs now, and none of it is needed for a
demo. The RunPod sections of [`deploy.md`](deploy.md) have the details.

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

Warm-up was `scripts/ops/demo_warm.sh` (a RunPod key in `RUNPOD_API_KEY`): it kept each
endpoint at one warm worker, pinged it once a minute and set everything back to zero after the
demo. Spark Open needed about 18 minutes to load its weights (measured 4 Nov). For demos the
runtime's self-hosted SAM call was set to `SAM_HTTP_TIMEOUT_S` x `SAM_HTTP_ATTEMPTS` = 25 x 1
before falling back to hosted SAM 3.1.

## Ship GPU code without S3 keys

Record only: there are no GPUs of ours to ship code to now.

`make ship-gpu` needs a RunPod S3 key. Without one, and when the volume's data center has no pod
stock, `scripts/ops/volume_stager.sh` does the same from a throwaway serverless worker on that
volume: the bundle goes over `runpodctl send`, the old code moves to
`/runpod-volume/rulecam/backup-<time>/`. The script's header has the steps. For a data center
with pod stock, a CPU or GPU pod mounting the volume at `/runpod-volume` is simpler (scp the
bundle, untar it into `/runpod-volume/rulecam`).
