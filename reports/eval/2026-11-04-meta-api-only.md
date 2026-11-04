# Meta API only: the live stack after we dropped RunPod (4 Nov 2026)

On 4 Nov at about 05:50 UTC we stopped using RunPod. Every model call now goes to the Meta API
(`https://api.meta.ai/v1`). The API and the web site run on the team's Mac and are public through
two Cloudflare tunnels. This report records what we measured on that stack the same morning.

Build: branch `p1/final-integration`, commits `1d98493` (clients), `b4338b7` (UI names the running
models). Settings: the "Meta API only" block in `.env.example` and `docs/deploy.md`.

## What runs where

| Job | Model | Where |
|---|---|---|
| Planner, Ask, guardrail pass, store checks | Muse Spark 1.3 | Meta API |
| Tier 1 check of each alert | Muse Spark 1.3, through the Glimmer client | Meta API |
| Tier 2: dispute review, plan audit, Ask answer review | Muse Wonder 1.0, through the Spark Open client | Meta API |
| Segmenting and tracking | SAM 3.1 (hosted) + ByteTrack | Meta API + the Mac's CPU |
| Speech in, images | Voice Transcribe, Muse Image | Meta API |

The key lists `muse-spark-1.3`, `1.2` and `1.1`, `muse-wonder-1.0`, `sam-3.1`,
`muse-voice-transcribe-1.0` and `muse-image-1.0` (`GET /v1/models`). Muse Glimmer 30B, Muse
Spark Open and Llama Guard 4 are not on it, so they no longer run. Prompt Guard 2 is not loaded
on the Mac, so the scene-text firewall is "degraded" and candidates with text in the scene go to
a person.

Two fixes were needed. The Meta API applies OpenAI's strict-schema rules and refused our
schemas with HTTP 400 until we sent `strict: false` (Rulecam still validates every reply). The
Tier 1 health check looked for a `/health` page the Meta API does not have, so it now asks
`GET /models` with our key.

## Model checks (`scripts/ops/model_proofs.py`)

| Check | Model | Time | Result |
|---|---|---|---|
| Tier 1 on a warehouse strip (hard-hat rule) | Muse Spark 1.3 | 9.8 s | violation, p = 0.97 |
| Tier 2 on the same candidate | Muse Wonder 1.0 | 6.1 s | violation, p = 0.97 |
| Plan audit of a stored plan | Muse Wonder 1.0 | 19.9 s | 6 findings |
| Ask answer review (the draft misreads its SQL on purpose) | Muse Wonder 1.0 | 7.7 s | sent back (`revise`), as it should |

Before the schema fix, the same run gave HTTP errors on three of the four checks.

## Cameras

Four looped stock clips, connected as cameras through `/connectors` (Spark's first look at each
took 22 to 30 s):

| Camera | Clip | Use case |
|---|---|---|
| cam-1 | Pexels 32243654 (a worker passes a moving forklift) | Warehouse |
| cam-2 | Pexels 32243653 (driver only) | Warehouse |
| cam-3 | CAVIAR OneShopOneWait1cor | Supermarket |
| cam-4 | CAVIAR OneStopEnter2cor | Supermarket |

## Warehouse

The Spanish request "keep people at least 4 m from a moving forklift" was planned in **146.1 s**:
survey 50.6, probe 59.8, gauge 6.0, ask 10.3, compile 0.2, audit 19.2, prove 0.1. It produced one
rule, on cam-1 only. Rule CI: boundary **16/16**, mutants **6/6**. It was approved as amber: the
forklift detection lower bound was 0.797 against a 0.80 bar, and the calibration error 0.20 m
against a 0.10 m limit (MoGe-2 ran on our GPU, so the floor is now measured from reference
objects and a 1.7 m person height).

In its first 6 minutes live it processed 64 chunks and raised 52 near-miss candidates, with no
failed verifications and no errors in the log. Tier 1 verdicts came from `muse-spark-1.3`, for
example "violation (0.92)" and "no_violation (0.05)". The verify chain on a real strip (Tier 1
then Tier 2) took **11.3 s** (5.4 s and 5.9 s); an earlier run the same morning took 12.2 s.

## Supermarket

- Queue check on cam-3: 18.6 s (SAM 3.1 + Muse Spark). Spark said the camera shows a corridor,
  not checkout tills, so nobody was waiting at a till.
- Hazard check on cam-4: 7.4 s.
- Two watches run every 5 minutes: queue on cam-3, hazard on cam-4.
- Ask ("How many people did the cameras see in the last hour, and when was it busiest?"):
  **57.4 s**, 7 steps, using Muse Spark 1.3, SAM 3.1 and a Muse Wonder 1.0 review that sent the
  first draft back once. With no cameras running yet, the same question took 29.6 s.

## Not live

- Crowd: the Jülich crowd clips were only on the deleted RunPod server.
- Muse Glimmer 30B, Muse Spark Open and Llama Guard 4 (not on the Meta API).
- Prompt Guard 2 (needs a model download on the Mac).

## Cost

Rulecam's own meter put cam-1 at about $36 an hour of hosted SAM 3.1. The 12 s clip loops
forever and always has motion, and chunks with motion on a camera with a high-severity rule are
never skipped (`src/rulecam/runtime/sampling.py`), so the per-camera budget cannot thin it. The
loop also makes about 15 Tier 1 calls a minute. We have not measured the cost per check on the
Meta API.

## One side observation

On a contact sheet of frames from one warehouse clip (`data/testclips/pexels-32243654-sheet.jpg`,
asked directly, not through Rulecam), Muse Spark 1.3 answered 10 people and noted they were
repeat appearances (18.2 s); Muse Wonder 1.0 answered 2, the same two men in every panel
(25.4 s). One image, not a benchmark.

## Earlier results

Everything measured from 1 to 4 Nov before the switch used Muse Glimmer 30B on our own RunPod
GPUs, and Muse Spark Open on 8 x H100. Those reports stand as they are; the docs now say which
stack each number came from.
