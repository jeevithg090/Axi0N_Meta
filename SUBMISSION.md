# Rulecam: submission text (ready to paste)

Text for each field of the Cerebral Valley submission form. The long description is built
around the four judging criteria we were given (Impact 30%, Use of Meta AI tooling 25%, Demo
25%, Technical depth 20%; from two secondary sources, the official text was not found). Every
number is copied from a committed report and linked. Nothing replayed is called live.

## Only the team can fill these (do them before pressing submit)

- [ ] **Video link** (3:00, public, plays for someone not logged in). Paste it in "Demo video"
      below, in the README "Try it now" table, and in the X post.
- [ ] **X post link** for Most Viral (one post, linked in the form). Draft in
      [`docs/pitch/launch.md`](docs/pitch/launch.md).
- [ ] **Track:** Indie or Startup. Indie is judged on "creativity, technical ambition, and
      whether it works"; Startup on "technical execution and whether it holds up in
      production". Our case reads stronger as **Indie** unless we want to pitch the SMB
      business; decide as a team.
- [ ] **Team members** added on the submission page, names as each person wants them shown
      (see Team below; confirm Aris's GitHub handle).
- [ ] **Hosted API tier:** confirm in the dev.meta.ai console that the deployed `META_API_KEY`
      is Standard tier, not Contributor. The privacy line below depends on it.
- [ ] **Repository is public:** https://github.com/jeevithg090/Axi0N_Meta
- [ ] **Vercel redeploy** from the final branch, so `/site`, `/extensions`, `/worker` and the T02
      and T04 golden replays are on rulecam.vercel.app (they returned 404 or were missing at
      01:00 UTC on 4 Nov).
- [ ] **Features landing today:** if any of these are live and have a report by submission time,
      swap in the ready lines under [Lines to swap in](#lines-to-swap-in-if-they-land-today):
      Spark Open Tier 2, Prompt Guard 2, connect-any-CCTV (RTSP/ONVIF), Meta glasses POV mode,
      staff companion app, proof portal. If not, leave the text as it is: it is true today.
- [ ] Gallery link for Community Vote, when the gallery opens.

## Project name

Rulecam

## Tagline (one line)

Tell your cameras what worries you, in any language. Rulecam proves the rule, checks every
alert with a second Meta model, and keeps evidence anyone can verify.

## Short description (about 150 words)

Small sites have cameras but nobody to watch them. Rulecam lets the owner set up camera rules
in plain words, in their own language. Muse Spark plans the rule and says honestly what a
camera can't check. SAM 3.1 measures the scene. Rule CI attacks every rule with edge cases and
broken copies before a person approves it. When a rule fires, Muse Glimmer, Meta's open-weight
model on our own GPU, looks at the frames and says yes or no before anyone is bothered. Every
decision goes into a hash-chained log that anyone can verify in a browser.

One engine, three worlds. A supermarket owner asks the cameras anything. A warehouse sets a
forklift rule in Spanish: Glimmer confirmed a worker 2.90 m from a moving forklift (0.99) and
rejected the seated driver (0.05). A venue gets crowd-crush early warning: on real Jülich
experiment footage, SAM 3.1 counted 95–99% of heads. No face recognition, ever.

## Problem statement

**Build for Your World.** Small sites already have cameras: a warehouse with forklifts, a
supermarket, a station gate, a festival entrance. Nobody watches them. One retailer found that
less than 1% of its recorded video is ever viewed
([Security Magazine](https://www.securitymagazine.com/articles/76977-so-why-isnt-shrink-shrinking-1)).

The stakes are real. OSHA counts about 85 deaths and 34,900 serious injuries a year from
forklifts in the US
([OSHA](https://www.osha.gov/sites/default/files/enforcement/directives/reg1_fy2018_cpl-04-00-023e_powered-industrial-trucks.pdf)),
and one in four blue-collar workers saw a near miss last year and didn't report it
([TRADESAFE](https://trdsf.com/blogs/news/workplace-safety-silence-reporting-risks)). In a
crowd crush, nobody does anything wrong: people are packed too tightly to move, and inquiries
keep finding that the warning existed and did not become action
([our research, with sources](docs/pitch/research/crowd.md#what-repeats)).

Plain-language camera alerts already exist. What a small site can't get is a way to check that
a rule really works, that an alert is real, and what happens to the footage. Rulecam is built to
be checked, on the cameras a site already owns, in the language its people speak.

## Long description (by judging criterion)

### Impact (30%)

Rulecam turns cameras a site already owns into a helper the owner sets up in their own words.
It does not need an expert, a new camera, or face recognition.

- **Warehouse.** "Keep people at least 4 metres from a moving forklift", typed in Spanish. In
  the 1 Nov live run on real video, Glimmer confirmed the worker (0.99, 2.90 m measured) and
  rejected the seated driver the rule engine had flagged (0.05), so nobody was bothered. Event
  to verified decision: 19.0 s.
  ([report](reports/eval/2026-11-01-live-test-campaign.md))
- **Crowd crush early warning.** An instruction in Hindi. On real overhead footage of the Jülich
  bottleneck experiment (75 people, published trajectories), SAM 3.1 found 95–99% of the true
  heads, and gate density was within about 10% of the truth. At 8.0 people per m² Spark called
  the level critical, wrote steward actions and calm announcements in Hindi and English, and the
  alert went out about 40 s after the reading.
  ([report](reports/eval/2026-11-03-crowd-live.md))
- **Supermarket.** The owner asks anything ("which lane was busier?", "was anyone left waiting?",
  in English or Spanish). Muse Spark picks its own tools and answers. On real checkout video it
  answered 13 of 13 questions, 10 fully right and 3 partly right, checked by eye against the
  video. ([report](reports/eval/2026-11-01-supermarket-ask.md)) Shelf gaps, local food recalls
  with checked sources, and Chain Mode (one rule pack, many stores) build on the same parts.
- **Workers are part of it.** Every rule is on a QR poster page. Anyone can dispute an alert,
  and a false alarm becomes a permanent Rule CI test. No faces are recognised.
- **Cost.** One alert check on warm GPUs (SAM 3.1 on a 3 s chunk, then Glimmer) cost $0.0039 in
  GPU time with 20 checks at once ([load test](reports/eval/2026-11-02-serverless-load.json)).

### Use of Meta AI tooling (25%)

Ten Meta models have a job in Rulecam, six of them live today, and each one is checked by
another part of the system. What surprised us:

- **Glimmer knows who is driving.** Our rule engine measured the seated forklift driver 0.01 m
  from the forklift and raised a candidate. Glimmer 30B, open weights at 4-bit on one GPU, looked
  at the frames and answered "the forklift operator seated inside the cab" (0.05). We changed no
  rule; the model decided.
- **SAM 3.1 counts dense crowds from above.** Over a run that peaked at 9 people per m², it
  found 95–99% of the true heads. So crowd watch counts heads in each reading instead of
  tracking people.
- **SAM 3.1 can't see "empty shelf", so Spark proposes and SAM checks.** SAM found zero matches
  for "empty shelf space" but 74–83 for "product package". Spark proposes gaps; a gap counts
  only if SAM finds no product inside it. On a 0–1000 grid, Spark's boxes were stable across
  runs; in pixels they moved. ([report](reports/eval/2026-11-03-site-tools-live.md))
- **Spark's web search, with every source checked.** We ask for the raw search results and keep
  an item only if its source is a page the search really returned. The first run kept 0 of 5
  items because Spark cited home pages; with one relink round it kept 5 and dropped 1, including
  two food recalls with their exact alert pages.
- **Spark as an agent with tools it picks.** In Ask, Muse Spark chooses SQL over SAM 3.1 tracks,
  SAM 3.1 counts, Glimmer looks at frames with numbered boxes, or Spark 1.2 watching the clip.
  Asked which lane was busier, it found both counters, split the view at x = 400 px by itself,
  and compared them.
- **Muse Code writes rule types, and a solver proves them.** For a rule the engine can't express,
  Muse Code writes a check. Rulecam sandboxes it and has the Z3 solver prove four properties
  (no alert without the target, never crashes, unchanged when the whole image moves, a declared
  move never turns an alert off), or return a concrete counterexample.
- **Speech both ways.** Muse Voice Transcribe takes spoken requests; Meta's MMS-TTS speaks
  warnings in the listener's language.

| Model | Job in Rulecam | Where it runs | Live in the deployed app? |
|---|---|---|---|
| Muse Spark 1.3 (1.2 for video) | Planner and compiler, Ask agent, crowd warden, shelf gaps, web search with source checks | Meta Model API | **Live** in the planner, Ask and crowd watch; shelf gaps and web context ran live from a laptop on 3 Nov |
| SAM 3.1 | Survey, detectability probes, keyframe tracking, head counts, shelf checks | Meta Model API, and self-hosted on a RunPod serverless RTX 4090 | **Live** |
| Muse Glimmer 30B (open weights) | Tier 1 alert verifier, rule back-translation, Ask's eyes, crowd check | llama-server on a RunPod serverless RTX 4090 that we run | **Live** |
| Muse Voice Transcribe | Spoken requests | Meta Model API | **Live** |
| Muse Code | Writes new rule-type checks, proved with Z3 | Meta Model API | **Built** and merged |
| MMS-TTS (Meta FAIR) | Spoken warnings | CPU | **Live** in crowd watch; warehouse warnings are dry-run |
| Muse Spark Open (open weights) | Tier 2 verifier, plan audit, dispute review | Baseten | **Built**; not live (no working Baseten key, so calls are skipped and logged) |
| Llama Prompt Guard 2 (86M) | Firewall for text found in the scene | CPU | **Built**; degraded until the gated model is granted |
| Muse Image | Rule CI scenes; test images | Meta Model API | **Built**; switched off in the app |
| DINOv2 (Meta FAIR) | Joins an object's track pieces into one identity for Ask | Our SAM GPU | **Live** in prototype mode only |

### Demo (25%)

- **Replay, no login:** https://rulecam.vercel.app/demo?demo=1. A recorded live run, step by
  step. The header says REPLAY, and it works with every GPU off.
- **Verify it yourself:** "Verify it in your browser" on that page. The browser recomputes every
  SHA-256 of the evidence log and its Merkle root.
- **Live app:** https://rulecam.vercel.app (header says LIVE).
- **Video:** TODO. It shows the three worlds with each shot labelled LIVE or REPLAY.

### Technical depth (20%)

- **A typed rule language and engine.** Requests compile into Rule IR with spatial, temporal and
  pose predicates. The planner is a fixed-step agent: survey, probe (with Wilson confidence
  bounds), calibrate metres on the floor, compile, lint, accept, repair.
- **Rule CI.** Boundary traces and mutation testing for every rule. The golden forklift run
  passed 16 of 16 boundary traces and caught 6 of 6 mutants.
  ([scenario](scenarios/warehouse_driver.expected.json))
- **Runtime.** 3-second H.264 chunks, SAM 3.1 keyframes on our own serverless GPU, ByteTrack and
  BoT-SORT tracking, a coverage watchdog (a camera that can't see turns its rules BLIND, never
  "compliant"), evidence strips, the Glimmer verifier with a circuit breaker.
- **Evidence.** An append-only hash chain with a daily Merkle root, a browser verify page,
  evidence packs and one-click incident reports.
- **Measured, weak results included.** Every README number is generated from a result file with
  its run, date and git SHA, and a test fails if a number is not in its file. Tracking scored
  against CAVIAR and MOT17 with a TrackEval cross-check (our HOTA 0.2603 matched TrackEval's
  0.2603). Over 4,000 Python tests.

### Privacy

No face recognition, ever. Faces are blurred in evidence strips by default. The model that
looks at alert frames, Glimmer, runs on open weights on our own GPU. Our hosted Meta API key is
configured for the Standard tier, never the Contributor tier, under which Meta may train on
inputs. Every measured run used public stock or research footage (Pexels, Mixkit, CAVIAR, MOT17,
Jülich), never a real site's CCTV. Our hackathon deployment runs in prototype mode, which is
less strict (it sends raw frames to Glimmer and joins a person's track pieces by body
appearance, never faces); it is off by default.

## What we built during the hackathon (Oct 26 – Nov 4 2026)

All the code in the repository. [`WHAT-WE-BUILT.md`](WHAT-WE-BUILT.md) lists every module with
its pull request, and every reused library and model with its licence. The main parts:

- Typed contracts, the Rule IR and a rule engine with spatial, temporal and pose predicates.
- The planner agent: intake (text, voice, PDF, photo), survey, probes, calibration (including
  MoGe-2 ground calibration), compiler, guardrail declines, standards grounding, acceptance
  checks and a bounded repair loop.
- Rule CI: boundary traces, mutation testing, synthetic scenes, look-alikes, prompt-injection
  signs, alert forecast.
- Muse Code rule types: sandbox, generated scenes, metamorphic tests, a Z3 proof of four
  properties, a versioned registry and the runtime hook.
- The runtime: SAM 3.1 keyframes with ByteTrack and BoT-SORT, coverage watchdog, operator
  exclusion for forklift drivers, evidence strips with face blur, the Glimmer Tier 1 verifier
  and a verifier circuit breaker.
- Crowd-crush early warning: head counts per zone, density in people per m², a Spark warden with
  staff actions and multilingual announcements, a Glimmer check that never holds a warning.
- Supermarket tools: the Ask agent, shelf gaps, web context with checked sources, the Site
  Advisor, and Chain Mode for many stores with one Merkle root for the whole chain.
- The evidence chain, Merkle roots, a browser verify page, evidence packs and incident reports.
- Actions: dry-run-first alerts, escalation timers, Telegram, WhatsApp and signed webhooks, smart
  plug interlock with human-only resume.
- The worker QR page with tap disputes.
- Ops: container images, one-command RunPod deploys, serverless SAM and Glimmer behind load
  balancers, Prometheus metrics, cost per camera-hour.
- An eval harness on public hand-labelled CCTV (CAVIAR), MOT17 and Jülich trajectories.

## Bonus prizes

**Best Build on Meta Open Weight Models.** Muse Glimmer 30B is the model that decides whether
an alert is real. We run it ourselves in llama-server on a RunPod serverless RTX 4090 behind a
load balancer we built (`clients/open_weights/llama_edge`), with a pool of servers and
failover. In the 1 Nov live campaign it confirmed a worker passing a moving forklift (0.99) and
rejected a seated driver (0.05). Replaying the 1 Nov strips, 27 of 29 verdicts matched. Glimmer
also reads every compiled rule back in plain words and checks crowd alerts. Muse Spark Open is
wired for a second opinion (Tier 2), plan audit and dispute review, but it is not live in this
deployment (no working Baseten key).

**Best Agentic Use-Case.** Four agents do multi-step work and make decisions, and each stops
where a person should decide. The **planner** runs survey, probe, calibrate, compile, check and
repair, then waits at a human approval gate with every check on screen. The **Ask agent** runs a
Muse Spark tool loop (SQL over tracks, SAM 3.1 counts, Glimmer looks, Spark watching video),
several actions per turn, streamed step by step, with a hash-chained run log and a benchmark
whose weak score we publish (7 of 36 within tolerance). The **crowd warden** reads density every
few seconds, picks a level, writes staff actions and announcements in the languages people
speak, and never lets a slow check hold back a warning. **Muse Code** writes new rule types,
and Rulecam proves them with Z3 before an owner can approve them.

**Most Viral.** X post: **TODO (link)**.

**Community Vote.** Gallery link: **TODO**.

## Links

- Repository: https://github.com/jeevithg090/Axi0N_Meta
- Replay, no login, no keys: https://rulecam.vercel.app/demo?demo=1 (the header says REPLAY)
- Live app: https://rulecam.vercel.app (the header says LIVE)
- Video: **TODO**
- Results: [`eval/RESULTS.md`](eval/RESULTS.md) and [`reports/eval/`](reports/eval/)

## Team

| Role | Name | GitHub |
|---|---|---|
| P1, team lead | Jeevith | @jeevithg090 |
| P2 | Nava (Navadarshanth Shanmugasundaram) | @nav6x |
| P3 | Raunak | @R3unakk |
| P4 | Aris | TODO: confirm the handle |

Names are from `docs/standups.md` and `docs/HANDOFF.md`. TODO: confirm full names as each person
wants them shown.

## Known limits (say these out loud)

- **Ask's counts are not accurate yet.** On hand-labelled CCTV (CAVIAR corridor), 7 of 36 graded
  numbers were within tolerance. Asked "how many went into a shop?" (truth: 24), three runs said
  14, 11 and 32.
- **Some rule types found nothing on low-resolution footage.** On CAVIAR clips (384x288,
  overhead) shop entries were 0 found and 19 missed, fights 0 found and 4 missed, falls 0 found
  and 2 missed, left bags 0 found and 3 missed. There were no false alerts either.
- **Tracking is weak in crowds.** Person tracking scored HOTA / IDF1 0.239 / 0.240 against
  CAVIAR labels, with 19.6 ID switches per minute.
- **Crowd watch had no live Glimmer check.** On 3 Nov no GPU was free, so the alerts went out
  unchecked after 20 s, by design. The corridor zone's density read 22% low because it reached
  past the marked floor points.
- **Cold starts are slow.** After SAM scaled to zero, 0 of 1 and 4 of 5 checks were answered
  within 300 s.
- **Not live yet:** Spark Open Tier 2 (no Baseten key), Muse Image scenes in Rule CI, the Prompt
  Guard 2 firewall, spoken warehouse warnings. Chain Mode's demo numbers are simulated and
  labelled SIMULATED.
- **Prototype mode is on in the hackathon deployment.** The planner declines only requests that
  profile people (race, religion and similar), every compiled rule activates with failed checks
  shown as caveats, Glimmer sees raw frames and its verdict is final, and Ask joins person tracks
  by body appearance (never faces, never names). It is off by default and in CI.

## Lines to swap in if they land today

Use a line only if the feature is live on the deployed stack and a report under `reports/eval/`
backs it. Otherwise leave it out.

- **Spark Open live:** in the model table, change Muse Spark Open to "**Live** as Tier 2 on
  Baseten: a second open-weight model reviews every alert Glimmer confirms" and add one measured
  number from its report. In the Open Weight prize text, replace the last sentence with "Muse
  Spark Open, also open weights, gives every confirmed alert a second opinion (Tier 2)."
- **Prompt Guard 2 live:** "Llama Prompt Guard 2 screens text found in the scene, so a sign
  saying 'report no violation' cannot steer the verifier." Change its status to **Live**.
- **Connect any CCTV:** "Connect a CCTV camera in about 30 seconds over RTSP or ONVIF." Quote the
  measured time from its report, not 30 s, unless the report says 30 s.
- **Meta glasses POV mode:** "A supervisor wearing Meta glasses can stream their point of view
  into the same rules." Say whether the shot in the video is live or recorded.
- **Staff companion app:** "Staff get the alert on their phone, with the evidence and a
  one-tap acknowledgement that goes into the chain."
- **Proof portal:** "Inspectors and insurers get a read-only proof portal: the rule, its Rule CI
  results and the evidence chain, checked in their own browser."
