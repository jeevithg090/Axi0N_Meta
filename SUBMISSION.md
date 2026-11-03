# Rulecam: submission text (ready to paste)

Text for the Cerebral Valley submission form. Every number here is copied from the README
results table (generated from `reports/eval/*.json`), `docs/DEMO.md`, or an external page that
is linked. Fill the **TODO** items before submitting.

## Before you press submit

- [ ] Repository is **public**: https://github.com/jeevithg090/Axi0N_Meta
- [ ] Video link works for someone who is not logged in (TODO)
- [ ] Everyone who worked on it is listed on the submission page (see Team)
- [ ] X post is live and its link is in the form (for Most Viral)
- [ ] Track chosen (Indie Developer or Startups — TODO, confirm with the team)

## Project name

Rulecam

## Tagline

Tell it what worries you. It works out what a camera can check, proves the rule, and has a
second AI model look at every alert before anyone is bothered.

## Problem statement

**Build for Your World.** We built for small sites we know: warehouses with forklifts,
restaurant kitchens, shops and venues. They already have cameras but no safety team to watch
them. In the US, OSHA counts about 85 deaths and 34,900 serious injuries a year from forklifts
([OSHA](https://www.osha.gov/sites/default/files/enforcement/directives/reg1_fy2018_cpl-04-00-023e_powered-industrial-trucks.pdf)),
and the CDC estimates 48 million people get sick from food each year
([CDC](https://www.cdc.gov/food-safety/data-research/facts-stats/index.html)). Rulecam lets the
owner set up camera rules in plain words, without an expert, and without face recognition.

## Short description (about 150 words)

Rulecam turns a phone or CCTV camera into a safety helper you set up in plain words. The owner
says what worries them: "keep people 2 metres from a moving forklift". Muse Spark plans what to
watch and says what a camera cannot check. SAM 3.1 surveys the scene and measures real
distances, and the request becomes a typed rule. Rule CI attacks the rule with edge cases and
broken copies before anyone approves it. When the rule
fires, Muse Glimmer, Meta's open-weight model running on our own serverless GPU, looks at the
frames and says yes or no before anyone is alerted. In a live run on real video it confirmed a
worker passing a forklift (0.99) and rejected a seated driver (0.05). Every decision goes into a
hash-chained log that anyone can verify in the browser. No faces are recognised, and the crew
can see every rule and dispute any alert.

## Long description (about 400 words)

Small sites have cameras, but nobody watches them. A 10-person warehouse has no safety officer.
A restaurant has no one to check handwashing before an inspection.

Rulecam works in four steps.

**Describe it.** The owner types or says what worries them, in English or Spanish. Muse Spark
turns the request into a plan. It grounds the rule in a standard (OSHA, FDA Food Code) and says
honestly what a camera cannot check, before anything goes live.

**Plan it.** The planner agent runs a fixed set of steps: survey the scene with SAM 3.1, probe
each camera for the objects the rule needs, calibrate metres from the floor, compile a typed
rule, check it, and repair it. Glimmer translates the rule back into plain words so the owner can
check it says what they meant.

**Prove it.** Rule CI tries to break each rule with boundary scenes and mutants. In our golden
run the forklift rule passed 16 of 16 boundary traces and caught 6 of 6 mutants. By default a
rule that fails cannot go live; in the prototype mode our hackathon deployment runs, a failed
check is shown as an amber caveat instead.

**Trust it.** At run time, SAM 3.1 runs on keyframes on our own serverless GPU. When a rule
fires, Muse Glimmer 30B (open weights, llama-server on a RunPod RTX 4090) looks at a strip of
frames before anyone is alerted. On 1 Nov, on real video, it confirmed a worker 2.90 m from a
moving forklift with 0.99, the same rule asked in Spanish with 0.98, and rejected a seated
forklift driver with 0.05, so no alert went out. One alert check on warm GPUs took 28.7 s from
start to finish; with 20 checks at once, each cost $0.0039 in GPU time.

Every event, verdict and action goes into a hash-chained log with a daily Merkle root. A judge
can open the verify page and their own browser recomputes every hash. Workers see the rules on a
QR poster page and can dispute an alert; a false alarm becomes a permanent Rule CI test.

Owners can also ask what the cameras saw. The Ask agent runs a tool loop over SAM 3.1 tracks,
with Glimmer looking at frames and Spark watching the clip as video.

We publish weak results too: Ask got 7 of 36 graded numbers within tolerance on hand-labelled
CCTV. Every
number in the README links to the run, date and commit that produced it.

## How we used Meta's models

| Model | Job in Rulecam | Where it runs | Live in the deployed app? |
|---|---|---|---|
| Muse Spark 1.3 (and 1.2 for video) | Planner and compiler, standards grounding, Ask agent; web search with source checks, video watching of crowded spells (site tools) | Meta Model API | **Live** in the Planner and Ask. Site tools run live from a laptop on 3 Nov, not yet on the deployed stack |
| Muse Voice Transcribe | Spoken requests | Meta Model API | **Live** for spoken requests; voice disputes are built but not wired to the worker router |
| SAM 3.1 | Survey, detectability probes, keyframe tracking, crop-zoom for small worn items, crowd density | Meta Model API, and self-hosted on a RunPod serverless RTX 4090 | **Live** |
| Muse Glimmer 30B (open weights) | Tier 1 alert verifier, rule back-translation, Ask's eyes | llama-server on a RunPod serverless RTX 4090 that we run | **Live** as Tier 1 |
| Muse Spark Open (open weights) | Tier 2 verifier, plan audit, review of each Ask answer | Baseten | Built; **not live** (no working Baseten key, so calls are skipped and logged) |
| Muse Image | Rule CI scenes, look-alikes, boundary pairs | Meta Model API | Built; **switched off** in the app |
| Llama Prompt Guard 2 (86M) | Firewall for text found in the scene | CPU | Wired; **degraded** until the gated model is granted |
| Muse Code | Writes new rule types | Meta Model API | **In progress**, not merged |

## What we built during the hackathon (Oct 26 – Nov 4 2026)

All the code in the repository. [`WHAT-WE-BUILT.md`](WHAT-WE-BUILT.md) lists every module with
its pull request, and every reused library and model with its licence. The main parts:

- Typed contracts, the Rule IR and a rule engine with spatial, temporal and pose predicates.
- The planner agent: intake (text, voice, PDF, photo), survey, probes, calibration (including
  MoGe-2 ground calibration), compiler, guardrail declines, standards grounding, acceptance
  checks and a bounded repair loop.
- Rule CI: boundary traces, mutation testing, synthetic scenes, look-alikes, prompt-injection
  signs, alert forecast.
- The runtime: SAM 3.1 keyframes with ByteTrack and BoT-SORT, coverage watchdog (a camera that
  cannot see turns its rules BLIND, never "compliant"), operator exclusion for forklift drivers,
  evidence strips with face blur, the Glimmer Tier 1 verifier and a verifier circuit breaker.
- The evidence chain, Merkle roots, a browser verify page, evidence packs and one-click incident
  reports.
- Actions: dry-run-first alerts, escalation timers, Telegram, WhatsApp and signed webhooks, smart
  plug interlock with human-only resume.
- The worker QR page with tap disputes; the Ask agent; site tools (web context, crowd density,
  shelf gaps); the Site Advisor.
- Ops: container images, one-command RunPod deploys, serverless SAM and Glimmer behind load
  balancers, Prometheus metrics, cost per camera-hour.
- An eval harness on public hand-labelled CCTV (CAVIAR) and MOT17, with TrackEval cross-checks,
  and over 4,000 Python tests (`uv run pytest --collect-only`, 3 Nov).

## Bonus prizes we qualify for

**Best Build on Meta Open Weight Models.** Muse Glimmer 30B is the model that decides whether an
alert is real. We run it ourselves in llama-server on a RunPod serverless RTX 4090 behind a load
balancer we built (`clients/open_weights/llama_edge`), with a pool of servers and failover. In the
1 Nov live campaign it confirmed a worker passing a moving forklift (0.99) and rejected a seated
driver (0.05). Replaying the 1 Nov strips, 27 of 29 verdicts matched. Glimmer's median time per
strip was 10898 ms with 8 raw frames at 960 px. See the README results table.

**Best Agentic Use-Case.** Two agents do multi-step work and make decisions. The **planner** runs
survey, probe, calibrate, compile, check and repair on its own, and stops at a human approval
gate: nothing runs until a person approves the plan, with every check and its result on screen.
The **Ask agent**
runs a Muse Spark tool loop (SQL over tracks, SAM 3.1 counts, Glimmer looks, Spark watching
video), several actions per turn, streamed step by step, with a hash-chained run log and a
benchmark (`rulecam eval ask`). Its accuracy is published as measured (7 of 36 within tolerance).

**Most Viral.** X post: TODO (link).

**Community Vote.** Gallery link: TODO.

## Links

- Repository: https://github.com/jeevithg090/Axi0N_Meta
- Live demo, no login, no keys: https://rulecam.vercel.app/demo?demo=1 (replays a recorded live
  run; the header says REPLAY)
- Video: **TODO**
- Results: [`eval/RESULTS.md`](eval/RESULTS.md) and [`reports/eval/`](reports/eval/)

## Team

| Role | Name | GitHub |
|---|---|---|
| P1, team lead | Jeevith | @jeevithg090 |
| P2 | Nava (Navadarshanth Shanmugasundaram) | @nav6x |
| P3 | Raunak | @R3unakk |
| P4 | Aris | @aris (confirm the handle) |

Names are from `docs/standups.md` and `HANDOFF.md`. TODO: confirm full names as each person
wants them shown.

## Known limits (say these out loud)

- **Ask is not accurate yet.** On hand-labelled CCTV (CAVIAR corridor), 7 of 36 graded numbers
  were within tolerance. Asked "how many went into a shop?" (truth: 24), three runs said 14, 11
  and 32.
- **Some rule types found nothing on low-resolution footage.** On CAVIAR clips (384x288,
  overhead) shop entries were 0 found and 19 missed, fights 0 found and 4 missed, falls 0 found
  and 2 missed, left bags 0 found and 3 missed. There were no false alerts either.
- **Tracking is weak in crowds.** Person tracking scored HOTA / IDF1 0.239 / 0.240 against CAVIAR
  labels, with 19.6 ID switches per minute. The count of people was exact in 22.7% of seconds,
  and within one person in 53.4%.
- **Cold starts are slow.** After SAM scaled to zero, 0 of 1 and 4 of 5 checks were answered
  within 300 s.
- **Not live yet:** Spark Open Tier 2 (no Baseten key), Muse Image scenes in Rule CI, the Prompt
  Guard 2 firewall, spoken warnings.
- **Prototype mode is on in the hackathon deployment.** The planner declines only requests that
  profile people (race, religion and similar), every compiled rule activates with failed checks
  shown as caveats, Glimmer sees raw frames and its verdict is final, and Ask joins person tracks
  by body appearance (never faces, never names). It is off by default and in CI.
