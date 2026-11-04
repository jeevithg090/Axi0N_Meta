# Rulecam: submission text (ready to paste)

Text for each field of the Cerebral Valley submission form. The long description is built
around the four judging criteria we were given (Impact 30%, Use of Meta AI tooling 25%, Demo
25%, Technical depth 20%; from two secondary sources, the official text was not found).
Nothing replayed is called live.

Since 4 Nov 2026 (about 05:50 UTC) the live system uses only the Meta API: no RunPod, no GPUs
of our own. Numbers from 1–4 Nov are copied from committed reports and linked; they were
measured before that switch, with Muse Glimmer 30B on our own GPUs (and Muse Spark Open on
8 x H100 where a run used it), and we say so where we quote them. Numbers marked **Meta API
only** were measured live on 4 Nov after the switch; the model proofs are in the message of
commit `1d98493`, and the rest are not yet in a committed report.

## Only the team can fill these (do them before pressing submit)

- [x] **Live site:** `https://preferred-florist-labeled-trace.trycloudflare.com` (4 Nov 2026), a Cloudflare tunnel from the team's Mac; models on
      the Meta API. The replay, the verify link, `/usecases/*`, `/staff` and `/glasses` open for
      someone not logged in (checked 4 Nov). If cloudflared restarts, the address changes: replace
      it everywhere with `git grep -l <old> | xargs sed -i '' 's#<old>#<new>#g'`.
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
      is Standard tier, not Contributor. The privacy line below depends on it, and since 4 Nov
      every alert check goes to the Meta API too.
- [ ] **Keep the Mac and the tunnel up while judges try the site.** The live site is the team's
      Mac through a Cloudflare tunnel; there is nothing to warm (`docs/demo-day.md`). If the Mac
      sleeps or the tunnel stops, the live pages stop.
- [ ] **Best Build on Meta Open Weight Models:** decide whether to enter. Our open-weight models
      (Muse Glimmer 30B, Muse Spark Open, Llama Guard 4) ran on our own GPUs from 1 to 4 Nov and
      do not run in the live system now. The text below says so.
- [ ] **Repository is public:** https://github.com/jeevithg090/Axi0N_Meta
- [ ] **Deadline:** submissions reportedly close Nov 4, 9:00 PM ET (unverified); we target noon ET
      (17:00 UTC) for the code and the form.
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
broken copies before a person approves it. When a rule fires, a second Meta model checks the
frames before anyone is bothered. Every step is hash-chained, and anyone can verify it in a
browser. It runs on the Meta API, with no GPUs of our own.

One engine, three worlds. A warehouse sets a forklift rule in Spanish: in our earlier runs,
Glimmer on our own GPU confirmed 6 of 6 live near misses, and the spoken Spanish warning was
heard back to check it. A venue gets crowd-crush early warning: on real Jülich experiment
footage, counts were within 0–4% of the truth. A shop owner asks the cameras anything. No face
recognition, ever.

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

- **Warehouse.** "Keep people at least 4 metres from a moving forklift", in Spanish. **Meta API
  only:** the rule planned in 146 s, passed Rule CI (16 of 16 edge cases, 6 of 6 mutants), was
  approved with amber caveats (forklift detection lower bound 0.797 against the 0.80 bar;
  calibration error 0.20 m against the 0.10 m limit), and runs live on a looped stock clip with
  Tier 1 verdicts from Muse Spark 1.3. **Earlier, on 4 Nov before the switch,** Glimmer on our
  own GPU confirmed 6 of 6 near misses at 0.98–0.99. Spark wrote the warning in Spanish, Meta's
  MMS-TTS spoke it, and Voice Transcribe heard it back with word error 0.09–0.18, all passed.
  Spark found the OSHA refresher-training clause a near miss triggers. A worker's dispute was
  upheld and became a permanent test. On 1 Nov, Glimmer rejected the seated driver the rule
  engine had flagged (0.05), so nobody was bothered.
  ([4 Nov](reports/eval/2026-11-04-warehouse-live.md), [1 Nov](reports/eval/2026-11-01-live-test-campaign.md))
- **Crowd crush early warning.** Recorded on 4 Nov before the switch; not live now, because the
  Jülich clips were only on the server we deleted. An instruction in Hindi. On real overhead
  footage of the Jülich bottleneck experiment, SAM 3.1's counts were within 0–4% of the
  published truth in every zone, up to 8–9 people per m². Spark called it critical and wrote
  steward actions and calm announcements in Hindi and English. With Glimmer warm on our GPU, it
  confirmed at 0.95 in 6.2 s, and stewards were told 26 s after the reading. When the plan was
  to send people to side door B, a route guard saw it was already filling, and Spark held the
  inflow instead.
  ([report](reports/eval/2026-11-04-crowd-live.md))
- **Supermarket.** The owner asks anything, by text or voice, in their language. Spark picks its
  own tools and answers. **Meta API only:** an Ask answer took 57.4 s with Muse Spark 1.3, SAM
  3.1 and a Muse Wonder review; on CAVIAR corridor clips a queue check took 18.6 s and a hazard
  check 7.4 s, and Spark reported that the corridor has no checkout tills. **Earlier, on 4 Nov
  before the switch:** on a busy frame where the tracker said 22 people, counting three ways
  (SAM 3.1, Spark pointing, Glimmer) gave 10, against about 12 by hand. A spill was seen and
  cleared in 1.9 min, closed only by a photo of the clean floor (the spill image is a generated
  SAMPLE). A spoken Spanish question was answered in Spanish in 45 s.
  ([report](reports/eval/2026-11-04-supermarket-live.md))
- **Everyone in the loop.** Staff get alerts by role on their phone (on 4 Nov, before the
  switch, a Glimmer-verified alert reached the push service 8.9 s after the camera chunk).
  Inspectors and insurers get a proof portal
  that re-checks 395 records and 97 numbers in their own browser in 0.3–0.4 s. Workers can read
  every rule and dispute any alert.
  ([staff app](reports/eval/2026-11-04-staff-app.md), [proof portal](reports/eval/2026-11-04-proof-portal.md))
- **Cameras they already have.** RTSP, HLS, MJPEG, ONVIF discovery or a public stream: 23–33 s
  from Connect to a camera Spark has named and surveyed
  ([connectors](reports/eval/2026-11-04-connectors.md)).

### Use of Meta AI tooling (25%)

**What runs now.** Since 4 Nov the live system uses only the Meta API: Muse Spark 1.3 (planner,
Ask, the planner's guardrail pass, retail checks and the Tier 1 check of every alert), Muse
Wonder 1.0 (Tier 2: dispute review, plan audit, Ask answer review), SAM 3.1 hosted by Meta,
Muse Voice Transcribe, Muse Image, and Muse Spark 1.2 where the code uses it (video with
narration). Muse Code runs as the local `muse` CLI, and MMS-TTS voices run on the CPU. The
existing Tier 1 and Tier 2 clients point at the Meta API, which speaks the same OpenAI chat
format ([`docs/deploy.md`](docs/deploy.md)). On this stack the model proofs passed: Tier 1 on a
warehouse strip in 9.8 s (violation, p = 0.97), Tier 2 in 6.1 s, plan audit in 19.9 s and Ask
answer review in 7.7 s (commit `1d98493`).

**What ran before.** From 1 to 4 Nov we also ran Muse Glimmer 30B and Muse Spark Open on our own
RunPod GPUs, Llama Guard 4 as a rule screen, Prompt Guard 2 as a scene-text firewall, and
DINOv2 for Ask's identities. None of them runs now. The planner's own guardrails still decline
banned requests (a fixed pattern check, then Muse Spark), and with Prompt Guard 2 not loaded,
candidates with scene text go to a person. Most results in this text come from those earlier
runs.

Each model is checked by another part of the system. What surprised us (all in the earlier
runs):

- **Glimmer knows who is driving.** Our rule engine measured the seated forklift driver 0.01 m
  from the forklift and raised a candidate. Glimmer, open weights at 4-bit on our own GPU, looked
  at the frames and answered "the forklift operator seated inside the cab" (0.05). We changed no
  rule; the model decided.
- **No single model counts a busy queue right; together they get close.** SAM 3.1 over-counted
  (15–17), Spark's points missed half-hidden people (9), Glimmer said 10–11. Shown how SAM's
  boxes and Spark's points line up, Spark answered 10 (about 12 by hand), where the tracker had
  said 22.
- **Listening back catches what reading can't.** A Hindi announcement, "do not push at the gate",
  came back from MMS-TTS and Voice Transcribe as "do not push at the crowd". Spark judged the
  meaning had changed, so the page did not play it by itself.
- **Spark plans routes, and a guard checks them.** Spark planned to divert the crowd to side
  door B, as the operator had asked. Before anything went out, the route guard read door B's
  camera: 3.75 per m² and filling. Asked again with those numbers, Spark held the inflow
  instead, and said why.
- **Muse Code's proof found a real bug.** Z3 showed that one version of a Muse Code check divided
  by zero on a vertical line. The repair round fixed it; the final version passed 4 of 4 proofs
  and caught 25 of 26 broken copies.
- **Spark reads the scene like an inspector.** It found the OSHA clause by web search and read
  the forklift's painted unit number ("K 35") off the frames for the training record.
- **A worker's dispute found our bug.** Spark upheld a dispute by reading the evidence strip's
  ruler, which was drawn to the wrong point. We fixed the ruler; the dispute stays on the chain.

| Model | Job in Rulecam | Now (since 4 Nov) | Before (1–4 Nov) |
|---|---|---|---|
| Muse Spark 1.3 | Planner and compiler, the planner's guardrail pass, Ask agent, retail checks (queues, shelf gaps, spills), crowd warden and route planning, dispute review when no Tier 2 answers, web search with source checks; since 4 Nov the Tier 1 alert check | **Live**, Meta API | Meta API, everything but the Tier 1 check |
| Muse Spark 1.2 | Watches a recorded clip as video, with narration | **Live** where the code uses it, Meta API | Meta API |
| Muse Wonder 1.0 | Tier 2: alert review, dispute review, plan audit, review of Ask answers | **Live**, Meta API | Not used |
| SAM 3.1 | Survey, detectability probes, keyframe tracking, head counts, shelf and spill checks | **Live**, hosted by Meta | Hosted by Meta, and self-hosted on RunPod serverless GPUs |
| Muse Code | Writes the code for new rule types, proved with Z3 | **Live**, the local `muse` CLI | From a laptop with the CLI; the API pod used Spark 1.3 as the writer, labelled |
| Muse Voice Transcribe | Spoken rules, questions, disputes, near-miss reports; hears warnings back | **Live**, Meta API | Meta API |
| Muse Image | Test images where no footage exists (labelled SAMPLE), toolbox-talk drawings | **Live**, Meta API; the Rule CI scenes step is still skipped | Meta API |
| MMS-TTS (Meta FAIR) | Spoken warnings and announcements | **Live**, CPU | CPU |
| Muse Glimmer 30B (open weights) | Tier 1 alert check, three-way counts, rule back-translation, Ask's and glasses mode's eyes | Not running; its client calls Muse Spark 1.3 | llama-server on our RunPod serverless GPUs, with a standby in a second region |
| Muse Spark Open 816B-A42B (open weights) | Tier 2 alert check, plan audit, review of Ask answers | Not running; its client calls Muse Wonder 1.0 | llama.cpp on our own 8 x H100 serverless endpoint ($43.12 an hour warm, about 18 minutes to start) |
| Llama Guard 4 12B | Screens each request before planning; refuses rules about who people are | Not running; the planner's own guardrails still decline banned requests | vLLM on our own serverless GPU |
| Llama Prompt Guard 2 (86M) | Firewall for text found in the scene | Not loaded; the firewall shows "degraded" and candidates with scene text go to a person | CPU, inside the API |
| DINOv2 (Meta FAIR) | Joins an object's track pieces into one identity for Ask | Not running | Our SAM GPU, prototype mode only |

Sources: the 4 Nov reports above, [`docs/deploy.md`](docs/deploy.md) ("Meta API only"),
[`docs/demo-day.md`](docs/demo-day.md) and [`WHAT-WE-BUILT.md`](WHAT-WE-BUILT.md). Several
4 Nov runs used a laptop running the API against the live models rather than the deployed pod;
each report says where it ran.

### Demo (25%)

- **Replay, no login:** `https://preferred-florist-labeled-trace.trycloudflare.com/demo?demo=1`. A recorded live run, step by step. The
  header says REPLAY, and it makes no model calls.
- **Verify it yourself:**
  `https://preferred-florist-labeled-trace.trycloudflare.com/verify/index.html?chain=/golden/warehouse_driver/chain.jsonl&root=6fe7daeb1f2543aef6a3d01d27f3a7793c4516520be41433ba6ee0871f87082e&n=35`.
  The browser recomputes every SHA-256 of the evidence log and checks it against the day's
  Merkle root.
- **The three worlds:** `https://preferred-florist-labeled-trace.trycloudflare.com/usecases/warehouse`, `/usecases/crowd`,
  `/usecases/supermarket`. The warehouse and supermarket pages run live on the Meta API; the
  crowd page shows its recorded 4 Nov runs, because crowd is not live now. **On a phone:**
  `https://preferred-florist-labeled-trace.trycloudflare.com/staff`, `https://preferred-florist-labeled-trace.trycloudflare.com/glasses`.
- **Video:** TODO. It shows the three worlds with each shot labelled LIVE, REPLAY, RESEARCH
  FOOTAGE or SAMPLE.

### Technical depth (20%)

- **A typed rule language and engine.** Requests compile into Rule IR with spatial, temporal and
  pose predicates. The planner is a fixed-step agent: screen, survey, probe (with Wilson
  confidence bounds), calibrate metres on the floor, compile, lint, accept, repair. **Meta API
  only:** the Spanish forklift rule planned in 146 s (survey 50.6 s, probe 59.8 s, gauge 6.0 s,
  ask 10.3 s, compile 0.2 s, audit 19.2 s, prove 0.1 s). Before the switch, with the camera's
  survey reused, a plan took 42–45 s on 4 Nov; a new camera about 2 minutes.
- **Rule CI.** Boundary traces and mutation testing for every rule: every forklift plan on 4 Nov,
  before and after the switch, passed 16 of 16 boundary traces and caught 6 of 6 mutants.
- **Proved extensions.** Muse Code's checks run in a sandbox and must pass mutation testing,
  common-sense scene changes and a Z3 proof of four properties before an owner can approve them.
- **Runtime and failover.** 3-second H.264 chunks from any connector; SAM 3.1 keyframes (hosted
  by Meta now; on our serverless GPUs with a hosted fallback before 4 Nov); before 4 Nov, Glimmer
  with a standby region and a hosted last resort; a failed Tier 1 check sends the candidate to a
  person; a coverage watchdog (a camera that can't see turns its rules BLIND, never
  "compliant").
- **Evidence.** An append-only hash chain with a daily Merkle root, a browser verify page, proof
  packs with Merkle inclusion proofs and signed share links, and one-click incident reports.
- **Measured, weak results included.** README numbers come from result files with their run,
  date and git SHA, and a test fails if a generated number is not in its file. Tracking is scored
  against CAVIAR and MOT17 with a TrackEval cross-check; crowd counts against the Jülich
  trajectories.

### Privacy

No face recognition, ever. Alerts go to roles, never to names. The planner's guardrails refuse
rules about who people are (Llama Guard 4 also screened them until 4 Nov). Faces are blurred in
evidence strips by default. Since 4 Nov every model call, the alert checks included, goes to the
Meta API; before that, Glimmer and Spark Open checked alerts on open weights on our own GPUs,
with a hosted Glimmer only as the last fallback. Our hosted Meta API key is configured for the
Standard tier, never the Contributor tier, under which Meta may train on inputs. Every measured
run used public footage (stock video, research data, Wikimedia Commons photos, a public webcam)
or generated images labelled SAMPLE, never a real site's CCTV. Our hackathon deployment runs in
prototype mode, which is less strict (raw frames to the Tier 1 model, which is on the Meta API
since 4 Nov; before 4 Nov, Ask joined a person's track pieces by body appearance, never faces);
it is off by default.

## What we built during the hackathon (Oct 26 – Nov 4 2026)

All the code in the repository. [`WHAT-WE-BUILT.md`](WHAT-WE-BUILT.md) lists every module with
its pull request, and every reused library and model with its licence. The main parts:

- Typed contracts, the Rule IR and a rule engine with spatial, temporal and pose predicates.
- The planner agent: intake (text, voice, PDF, photo), Llama Guard 4 screen, survey, probes,
  calibration (including MoGe-2), compiler, standards grounding, acceptance checks, a bounded
  repair loop, and survey reuse for fast re-planning.
- Rule CI: boundary traces, mutation testing, look-alikes, prompt-injection signs, alert
  forecast.
- Muse Code rule types: sandbox, generated scenes, metamorphic tests, a Z3 proof, a repair round,
  a versioned registry and the runtime hook.
- The runtime: camera connectors (RTSP, HLS, MJPEG, ONVIF, public streams, uploads), SAM 3.1
  keyframes with ByteTrack and BoT-SORT, coverage watchdog, operator exclusion for forklift
  drivers, evidence strips, the Glimmer and Spark Open verifiers with failover.
- Warehouse: spoken warnings heard back, OSHA refresher duty, voice near-miss reports, worker
  disputes reviewed by a model, toolbox talks.
- Crowd-crush early warning: head counts per zone, a Spark warden with a phase meter, a route
  guard, three-way counts, announcements heard back, escalation to named roles.
- Supermarket: Ask with three-way counts and visible reasoning, shelf gaps, spills and blocked
  exits, staff tasks closed by photo, advice cards with pre-registered switchbacks, Chain Mode.
- The staff app (roles, three languages, Web Push), glasses mode, and the proof portal.
- Ops: container images, one-command RunPod deploys, serverless GPUs in several regions,
  Prometheus metrics, a demo-day runbook; on 4 Nov, the move of every model call to the Meta
  API, with `/health` naming the models in use.
- An eval harness on public hand-labelled CCTV (CAVIAR), MOT17 and Jülich trajectories.

## Bonus prizes

**Best Build on Meta Open Weight Models.** From 1 to 4 Nov, three open-weight Meta models ran on
GPUs we operated. Muse Glimmer 30B decided whether an alert was real, in llama-server on RunPod
serverless GPUs with a standby region; on 4 Nov it confirmed 6 of 6 live forklift near misses
(0.98–0.99), and on 1 Nov it rejected the seated driver (0.05). Muse Spark Open 816B-A42B ran on
our own 8 x H100 endpoint for the second check, the plan audit and the review of Ask's answers.
Llama Guard 4 ran on our own GPU to refuse rules about who people are. Glimmer also counted for
the three-way count, read every compiled rule back in plain words, and looked for glasses mode.
On 4 Nov we moved every model call to the Meta API, so none of these runs in the live system
now; the results above are from the recorded runs.

**Best Agentic Use-Case.** Several agents do multi-step work and make decisions, and each stops
where a person should decide. The **planner** screens, surveys, probes, calibrates, compiles,
checks and repairs, then waits at a human approval gate. The **Ask agent** picks its own tools
(SQL over tracks, SAM 3.1 counts, a model looking at frames, three-way counts, Spark watching
video) and shows its reasoning. The **crowd warden** (recorded runs; not live now) reads density
every few seconds, names the phase, plans routes, has the route guard check them, and never lets
a late check hold back a warning.
**Muse Code** writes new rule types, gets one repair round when a proof fails, and waits for the
owner to approve.

**Most Viral.** X post: **TODO (link)**.

**Community Vote.** Gallery link: **TODO**.

## Links

- Repository: https://github.com/jeevithg090/Axi0N_Meta
- Replay, no login, no keys: `https://preferred-florist-labeled-trace.trycloudflare.com/demo?demo=1` (the header says REPLAY)
- Live app: `https://preferred-florist-labeled-trace.trycloudflare.com` (the header says LIVE)
- Video: **TODO**
- Results: [`README.md#results`](README.md#results), [`eval/RESULTS.md`](eval/RESULTS.md) and
  [`reports/eval/`](reports/eval/)

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

- **Most results come from the earlier stack.** The 1–4 Nov results were measured with Glimmer on
  our own GPUs, before the move to the Meta API. On the Meta API alone we have the numbers marked
  "Meta API only", not a full re-run.
- **The live site is one Mac.** The API and the site run on the team's Mac through a Cloudflare
  tunnel; if either stops, the live pages stop, and a restarted tunnel has a new address.
- **Not running now:** Llama Guard 4 (the planner's own guardrails still decline banned
  requests) and Prompt Guard 2 (the firewall shows "degraded"; candidates with scene text go to
  a person). **Crowd is not live now**: the Jülich clips were only on the server we deleted.
- **Several 4 Nov runs were on a laptop** running the API against the live models, not on the
  deployed pod. Each report says where it ran.
- **GPU capacity decided the speed before 4 Nov.** When the serverless SAM pool was throttled, a
  camera chunk took about 123 s to become a candidate (about 2 s when warm). Spark Open took
  about 18 minutes to start.
- **Glimmer once held a true crowd alert** ("not confirmed", 0.68, while describing a crush-level
  crowd). Spark now decides to send or hold when the Tier 1 check disagrees; that path is
  unit-tested and has not come up live.
- **Counts from tracks are high on busy views**, and Ask's counts on hand-labelled CCTV were weak
  (7 of 36 within tolerance, 2 Nov). The three-way count fixed the busy frames it was tested on.
- **Some images are generated.** The spill, the restocked shelf and the blocked fire exit are Muse
  Image edits labelled SAMPLE. Chain Mode's scorecard is SIMULATED.
- **Not tested on real hardware:** a real CCTV camera or NVR (streaming servers and a simulated
  ONVIF recorder stood in), real glasses (stand-in stills), push on a real phone (Chromium), a
  real speaker.
- **Some rule types found nothing on low-resolution footage.** On CAVIAR clips (384x288,
  overhead) shop entries, fights, falls and left bags were all missed, with no false alerts.
- **Prototype mode is on in the hackathon deployment.** The planner declines only requests that
  profile people (race, religion and similar), every compiled rule activates with failed checks
  shown as caveats, the Tier 1 model sees raw frames (on the Meta API since 4 Nov) and its
  verdict is final, and Ask joins person tracks by body appearance (never faces, never names;
  this needed our SAM GPU, so it has not run since 4 Nov). It is off by default and in CI.
