# Rulecam v2: a programmable, verifiable policy layer for any physical space

*Design document for the Meta Global AI Developer Hackathon (build Oct 27 – Nov 4 2026). Expands "Build 8 — Rulecam" from the research report into a full product concept.*

---

## 1. The thesis in one paragraph

Cameras are the most widely deployed sensor on Earth and almost all of them are dumb recorders: roughly a billion are installed and only about a third run any analytics at all ([ABI Research](https://www.abiresearch.com/news-resources/chart-data/number-of-video-analytics-cameras-worldwide)). Rulecam v2 turns any camera into a **policy engine that anyone can program by talking to it**: a supervisor, a chef, a lab manager or a parent states a rule in their own language, the rule is **compiled into a readable, editable, testable specification**, it is **tested against generated scenarios before it goes live**, every alert is **independently verified by an open-weight model whose reasoning is shown**, and the system **acts** (warns, notifies the right person, logs, reports, escalates) with a human gate before anything irreversible. The same events then feed a second lens: where the business is losing time, stock and customers, and what to do about it. The v1 pitch was "say the rule, get a sentinel." The v2 pitch is **"the first camera rule you can read, test, trust and audit, and the first safety camera that also finds the money."** That shift matters because the research found that plain-language camera alerts already exist at Verkada, Hakimo, Avigilon, Spot AI, Brivo, Conntour and others, so "natural language rules" alone cannot win. Verification, testing, inspectability, portability, privacy and price are where nobody is.

---

## 2. Why v1 was too small, and what the competitor research changed

The v1 concept in the report was construction-only, single-camera, and rule-to-alert. Three findings from the follow-up research reshape it:

**Natural-language camera rules are table stakes now.** Verkada shipped freeform text alerts in September 2024 ([Verkada](https://www.verkada.com/blog/september-2024-launches/)), Hakimo's AI Operator "can detect any anomaly ... that can be described in words" ([Hakimo](https://www.hakimo.ai/blog/hakimo-secures-10-5m-to-transform-physical-security-with-human-like-autonomous-security-agent)), Avigilon Unity takes prompts like "alert me if you see smoke and people running" on-prem ([Motorola](https://www.motorolasolutions.com/newsroom/press-releases/motorola-solutions-expands-avigilon-enterprise-security-suite.html)), Spot AI's Iris builds "custom Video AI Agents through simple conversation" ([Spot AI](https://www.spot.ai/blog/introducing-iris-the-first-natural-conversation-ai-agent-builder-for-security-cameras)), Brivo's Eeva launched in March 2026 ([Brivo](https://www.brivo.com/products/video-surveillance/eeva/)), and two YC startups, Conntour and OpenVector, pitch exactly "describe what to watch" ([Conntour](https://www.ycombinator.com/launches/Mii-conntour-ask-security-cameras-anything); [OpenVector](https://www.ycombinator.com/launches/SBk-openvector-self-improving-ai-that-turns-any-camera-into-an-autonomous-worker)). We must never claim "first natural-language camera rules."

**The safety specialists are closed catalogues.** Voxel ($44M Series B, June 2025; acquired Ventral AI on Oct 13 2026), Protex AI ($36M Series B), Intenseye ($64M Series B, Jetson "Sentinel" hardware) and viAct all sell fixed detection categories with parameter builders. Protex's "no-code" builder is zone, speed, direction, count, minimum distance and shift on pre-trained classes; duration and dwell rules are not documented anywhere ([Protex](https://www.protex.ai/safety/behavioural-safety); [Voxel](https://www.prnewswire.com/news-releases/voxel-raises-44m-in-series-b-funding-to-transform-workplace-safety-with-its-ai-powered-platform-302471555.html)). All are quote-only and enterprise-first; a Protex reviewer on G2 wrote that "cost limits how many cameras can be enabled" ([G2](https://www.g2.com/products/protex-ai/reviews)).

**Nobody verifies, tests, or publishes precision.** The only documented second-pass verifier is NVIDIA's VSS warehouse reference architecture, which is a blueprint rather than a product and recommends hand-labelling 10–50 clips per prompt ([NVIDIA VSS](https://docs.nvidia.com/vss/3.2.0/warehouse-docs/alerting-service.html)). No vendor publishes precision or recall; Voxel's "95%+ detection accuracy" has no defined metric. No vendor tests a rule against synthetic scenarios before deployment. No vendor offers voice or multilingual rule entry. G2 reviewers of Intenseye report "alerts flagged harmless movements or shadows, so we had to tune it again and again" ([G2](https://www.g2.com/products/intenseye/reviews)). Frigate, the leading open-source NVR, explicitly refuses text-to-image triggers because "CLIP scores can be inconsistent" ([Frigate docs](https://docs.frigate.video/configuration/semantic_search/)), which is the strongest possible endorsement of our detector-plus-predicate design over "ask a VLM to watch."

What nobody ships, and what v2 is built around:

| Capability | Precedent found | Our claim (honest wording) |
|---|---|---|
| Compile free text into inspectable metric and temporal predicates over open-vocabulary masks | None (Protex/Lumeo have fixed-class thresholds) | "Compiles your sentence into a rule you can read, edit and version" |
| Independent open-weight second-model verification of every alert, with reasoning shown | NVIDIA VSS blueprint only | "Every alert is double-checked by an independent open-weight model before it fires" |
| Synthetic-scenario testing of a rule before it goes live | None commercial | "Test a rule against generated scenarios before deployment; we found no commercial product that does this" |
| Live precision tracking with automatic rule pausing | None | "Rules that monitor their own precision and pause themselves" |
| Voice rule entry with diarization and code-switching across 25 languages | None | "The first camera rules you can speak, in a mixed-language crew" |
| Portable rule files and domain packs | None | "Rules are text files you can share, fork and audit" |
| Agentic, multi-step response with human gate and replayable log | Hakimo (speaker warnings, SOPs) in security | "Acts, with a human gate before anything irreversible, and every run replayable" |
| SMB price on existing cameras with open-weight on-prem reasoning | Conntour (on-prem), Frigate (local, no safety semantics) | "Runs on a laptop next to the cameras; video never leaves the site" |

---

## 3. Who it is for, and why the market is far bigger than construction

One engine, many "rule packs." The same compiler, perception core, verifier and agent serve every domain; only the rule files differ. The numbers below are the pitch material.

| Domain | The pain (cited) | Camera-observable rules in the pack |
|---|---|---|
| Construction | 1,034 US deaths in 2024; fall protection has topped OSHA's citation list for 15 straight years with 6,992 citations in FY2025; firms under 20 employees account for about half of deaths with 36.5% of workers ([BLS CFOI](https://www.bls.gov/news.release/cfoi.nr0.htm); [Safety+Health](https://www.safetyandhealthmagazine.com/?p=52496); [CPWR via Risk & Insurance](https://riskandinsurance.com/small-construction-firms-account-for-half-of-industry-fatalities-despite-employing-just-over-a-third-of-workers/)) | Harness on scaffold, hard hat and vest, distance from excavator swing radius, ladder angle and three-point contact, trench with person and no shoring box, exclusion zone under a load |
| Warehousing and logistics | Warehousing injury rate 4.8 per 100 workers, double the all-industry rate; 5,186 forklift severe-injury reports in ten years; powered industrial trucks cited 2,150 times in FY2025 ([BLS NAICS 493](https://www.bls.gov/iag/tgs/iag493.htm); [AIHA on OSHA SIR](https://www.aiha.org/news/260514-osha-releases-severe-occupational-injury-report-statistics-for-2024)) | Pedestrian within N m of a moving forklift, forklift speed in a pedestrian aisle, pallets stacked above N high, dock edge with no barrier, blocked aisle for more than T seconds, near-miss counterfactual |
| Manufacturing | 353 deaths in 2024; lockout/tagout cited 2,562 times and machine guarding 1,498 times in FY2025; 2,426 amputations reported to OSHA in 2024 ([BLS Table A-1](https://www.bls.gov/iif/fatal-injuries-tables/fatal-occupational-injuries-table-a-1-2024.htm); [Safety+Health](https://www.safetyandhealthmagazine.com/?p=52496)) | Hands inside a press or robot cell while it runs, guard removed, person in a cell without a lock on the panel, safety glasses at the grinder |
| Commercial kitchens | Handwashing-station violations are the single most common Food Code violation at roughly 6% of about 2 million inspections; the Food Code requires at least 20 seconds of handwashing; FDA observed noncompliance in 73% of full-service restaurants ([The Takeout, secondary](https://thetakeout.com/restaurant-food-safety-violation-fda-hand-washing-rules-1850972430); [Minn. R. 4626.0070](https://www.revisor.mn.gov/rules/4626.0070/version/2019-01-02T09:57:44-06:00); [CIDRAP on FDA](https://www.cidrap.umn.edu/foodborne-disease/disease-hazards-common-food-operations-fda-says)) | Handwash lasting at least 20 seconds after entering the line, gloves and hair restraint at prep, raw and ready-to-eat on separate boards, fryer unattended for more than T seconds, wet floor without a sign |
| Laboratories | Self-reported eye-protection use is 61% in academic labs versus 83% in industry, and compliance rose when behaviour was monitored ([K-State summary of UCLA study](https://www.k-state.edu/today/announcement/?id=20752)) | Goggles in the wet area, fume hood sash above the line while someone works, gloves worn outside the lab, unattended heating |
| Retail, back-of-house, small business | 36.2 million US small businesses; employers with 10 or fewer workers are exempt from injury logs entirely, and OSHA can inspect each workplace about once every 191 years ([SBA](https://advocacy.sba.gov/?p=30239); [29 CFR 1904.1](https://www.law.cornell.edu/cfr/text/29/1904.1); [AFL-CIO 2026](https://aflcio.org/2026/4/27/24-things-you-need-know-2026-death-job-report)) | Fire exit blocked for more than 60 seconds, stepladder used as a straight ladder, stockroom aisle obstructed, dumpster area with person after hours |
| Facilities and events | Egress and crowding are the perennial inspection items | Crowd density above N per zone, egress path obstructed, spill present for more than T seconds |
| Care facilities (operations only) | Six in ten people with dementia wander; a state review found about 230 elopements since 2019 in Utah alone; CMS cites unmonitored exit doors at immediate-jeopardy level ([Skilled Nursing News](https://skillednursingnews.com/2026/01/state-data-reveals-230-cases-of-older-adults-wandering-away-from-care-facilities-including-nursing-homes/); [CMS survey example](https://readyrule.com/nursing-home/395916-kirkland-village/report/2026-02-18)) | Exit door propped open for more than T seconds, resident in the exit vestibule without staff, person down in a corridor for more than T seconds (operational alert, no medical judgment) |
| Home | 376 child pool drownings a year, nearly 80% under five, more than 70% residential; CPSC recommends self-latching gates and a designated "water watcher" ([CPSC 2026](https://www.cpsc.gov/Newsroom/News-Releases/2026/CPSC-Report-Highlights-Persistent-Risk-of-Childhood-Drowning)) | Pool gate open with a small child in the yard and no adult within N m, garage door open after T |
| Utilities and energy field work | Arc-flash and exclusion-zone rules; 70% of electrical deaths are in non-electrical occupations ([ESFI via notes]) | Arc-rated PPE in the switchgear zone, exclusion zone around a live panel, two-person rule |

Cross-cutting multipliers that make this "Build for Your World" rather than an enterprise tool: the NSC prices a medically consulted injury at $48,000 and a death at $1.54 million ([NSC](https://injuryfacts.nsc.org/work/costs/work-injury-costs/)); Hispanic workers die at 4.3 per 100,000 versus 3.3 overall, 68.5% of them foreign-born, and more than 40% of construction labourers have limited English ([BLS CFOI](https://www.bls.gov/news.release/cfoi.nr0.htm); [CPWR via Safety+Health](https://www.safetyandhealthmagazine.com/articles/26416-report-details-surge-in-hispanic-construction-worker-deaths)), which is why multilingual voice entry and multilingual warnings are a core feature, not a nicety. Deploying a custom detector today means hundreds to a thousand labels per class and 600–1,500 person-hours per 100,000 images ([Superb AI, vendor](https://www.superb-ai.com/blog/part-2-three-dataops-challenges-that-most-computer-vision-teams-struggle-with)); SAM 3.1 makes the first detector free.

---

## 4. The pillars

Each pillar is a distinct feature set, maps to a distinct Meta capability, and gives judges something to score on the 25% tooling axis.

### Pillar 1: Rule Studio, multimodal rule authoring that compiles to a readable spec

**Inputs accepted.** Spoken rules via Muse Voice Transcribe in DIARIZATION mode with `keywords` biased to site jargon ("excavator", "swing radius", "LOTO", "sash") and code-switching across 25 languages, so a Spanish-speaking foreman and an English-speaking inspector can author rules in one session ([Speech to text docs](https://dev.meta.ai/docs/speech-to-text)). Typed rules. A **photo of the site's safety sign or a page of the SOP**, or the **PDF of the company safety plan**, read by Muse Spark 1.3 (PDF is a listed input modality with no cookbook recipe, which makes it a surprise) to propose every camera-observable rule it contains. An **OSHA standard excerpt** pasted in, from which Spark proposes rules and tags each as camera-observable or not.

**Zone definition without drawing.** Three ways: draw a polygon on the frame; say "the area around the excavator" and the system segments the excavator with SAM 3.1 and computes a metric buffer; or **walk the perimeter** holding a cone while SAM tracks the cone and the path becomes the zone.

**Metric calibration.** One-time ground-plane homography from four clicked points with known dimensions, or from a reference object SAM segments automatically (a standard 48×40 inch pallet, a 28-inch cone). After that, "two metres" is a real distance on the ground plane, measured from the bottom-centre of each person mask. Optional relative depth from Depth Anything V2 (reused, Apache-2.0) for elevated scenes.

**Compilation.** Muse Spark 1.3 with structured output emits a Rule IR (section 6). The IR is **back-translated to plain language by a different model** (Muse Glimmer locally) and shown side by side with the original sentence; the author approves or edits. This is the VeriMAP pattern (planner writes the check, independent verifier applies it) applied to rule authoring ([VeriMAP, EACL 2026](https://preview.aclanthology.org/ingest-eacl/2026.eacl-long.353.pdf)). Rules are saved as portable `*.rule.yaml` files with provenance (who, when, from what input), versioned in git.

**What is new here:** everything except the ASR and LLM calls. Nobody compiles to an inspectable IR, nobody does voice, nobody ingests signs or SOP PDFs.

### Pillar 2: Perception core, SAM 3.1 as the eyes with correction built in

**Tracking.** Each concept in a rule ("person", "hard hat", "safety harness", "forklift", "hands", "faucet", "fire exit door") is a separate SAM 3.1 call because the API takes one concept per request; calls run in parallel under the 3,000 RPM limit, and SAM 3.1's Object Multiplex tracks up to 16 objects per frame per call ([SAM segmenting guide](https://dev.meta.ai/docs/sam/segmenting)). Live video is handled as rolling 3-second H.264 chunks exported with `-movflags +faststart`, uploaded through the Files API, and streamed back per frame; identity across chunks is maintained by re-prompting the next chunk's first frame with the previous chunk's final boxes (box prompts, up to 16 objects) and falling back to IoU matching. For true sub-second loops, the same pipeline runs against a self-hosted `facebook/sam3.1` on a desktop GPU or a Modal GPU, which also unlocks **image-exemplar prompts** ("our harness looks like this") that the hosted API does not expose.

**Correction loop (a judge-visible surprise).** When the verifier or a human rejects an alert because SAM mis-segmented ("that is a backpack strap, not a harness"), the UI lets the user click the false region, which becomes a **negative point** on a re-prompt, and the clip is re-tracked backward with `propagation_direction: backward` so the correction applies to the whole event. Most demos never touch negative points or backward propagation ([SAM segmenting guide](https://dev.meta.ai/docs/sam/segmenting)).

**Predicate engine (code, not prompts).** Spatial: distance on the ground plane, inside zone, overlap, above or below, nearest. Set logic: negation by composition ("person without hard hat" is a person mask with no hat mask intersecting the top 25% of the person box), counting, grouping. Temporal, which no safety vendor documents: dwell (hands at the faucet for at least 20 s), absence (no staff in zone for more than T), sequence (entered line, then no handwash within 60 s), rate (more than N forklift passes per minute), and persistence (exit blocked continuously for more than 60 s). Near-miss: closest approach and time-to-collision from tracked boxes, with a VLM "freeze test" counterfactual at verification time.

**Reused:** SAM 3.1 API and open-source checkpoint, OpenCV, ffmpeg, Depth Anything V2. **New:** chunked live pipeline, identity hand-off, homography tool, predicate and temporal engine, correction loop.

### Pillar 3: Rule CI, test the rule before it goes live (Muse Image as a test generator)

When a rule compiles, Muse Spark 1.3 writes a **test matrix**: positive cases, negative cases, and edge cases ("person wearing a white cap, not a hard hat", "harness worn but unclipped", "forklift parked, not moving"). Muse Image renders each case **using the actual camera frame as a reference image** so lighting, angle and background match the site (up to 10 reference images; agentic generation is on by default) ([Image generation docs](https://dev.meta.ai/docs/image-generation)). Each generated image is **validated by SAM 3.1** to confirm it contains the intended concepts in the intended arrangement (regenerate if not), then the full pipeline runs against it and the rule gets a pass/fail report with per-case explanations before deployment. Human-labelled real clips accumulate into a regression suite; every rule edit re-runs it. Muse Image has no inpainting and opaque backgrounds, so edits are expressed in language and composition is checked rather than assumed.

This is the single most original feature: it uses a creative model as a software-testing tool, maps onto Verification Horizon's warning that verifiers must be task-specific and co-evolve with the generator ([arXiv 2606.26300](https://arxiv.org/abs/2606.26300)), and is a credible Best Creative Build entry because the model is used for something nobody expects.

### Pillar 4: Verifier, every alert is proven before it fires

A candidate event from the predicate engine never reaches a human raw. It is packaged as: the rule text and IR, a six-frame strip with mask overlays, the predicate trace (measured distances, durations, counts), and the question "does this clip show the rule being violated?"

**Tier 1, local and private:** Muse Glimmer (30B, Apache 2.0, text plus image, video at 2 fps) on the site laptop returns `{verdict, confidence, reason}` within a second or two using the DFlash drafter ([Hugging Face](https://huggingface.co/blog/muse-glimmer)). **Tier 2, escalation:** when Glimmer disagrees with the predicate engine or is low-confidence, the clip goes to Muse Spark Open on Baseten (`meta-models/muse-spark-open-1026`, confirmed in the organisers' FAQ) whose `reasoning_content` is displayed verbatim in the UI, so the judge watches an open-weight model think through "the strap is over the shoulder but the D-ring is not clipped." Verdicts follow strict AND with structured error feedback (VeriMAP). Disagreement after Tier 2 goes to a human review queue.

**Rule health.** Human labels on alerts feed a per-rule precision estimate; an E-valuator-style sequential test with a false-alarm bound pauses a rule automatically when precision falls below the operator's threshold and tells the author why ([E-valuator, ICML 2026](https://icml.cc/virtual/2026/74515)). This is the direct answer to the "tune it again and again" complaints in competitor reviews.

**Stretch:** LoRA fine-tune of Glimmer as a domain verifier on the accumulated labelled clips using the Oct 30 Fireworks session; a domain-tuned open verifier with its trace on screen would be rare among entries ([Muse Spark Open post-training](https://dev.meta.ai/docs/muse-spark-open/post-training)).

### Pillar 5: Response agent, it acts, with a human gate

An orchestrator (Muse Wonder for planning if available in the team's region, otherwise Muse Spark 1.3) receives verified events and chooses actions from a tool set: **speak** a multilingual warning on a site speaker (Meta ships no TTS, so this uses the OS voice or an open TTS and is labelled as non-Meta), **notify** the right role (PPE to the crew lead, forklift proximity to the dock lead, blocked exit to facilities) with the clip and explanation, **log** the event to the append-only store, **escalate** patterns (third proximity event in an hour triggers a stand-down suggestion), **physically interlock** through a relay or smart plug (demo: a lamp or a toy forklift stops), **draft** an OSHA 300-style record only for events a human marks recordable, and, as a stretch, **file** into an existing safety system through Muse Wonder's computer-use tool with `pending_safety_checks` surfaced in the UI ([Computer use docs](https://dev.meta.ai/docs/computer-use)). Irreversible actions (interlock, filing, external messages) sit behind a human approval gate that mirrors Muse Code's approval modes. Every run is an append-only event log that can be replayed.

**Self-extending rule engine (stretch, high tooling value).** When a rule needs a predicate the engine lacks ("pallets stacked more than three high"), Spark writes a predicate specification and **Muse Code is invoked at runtime** with `muse exec --json --disable-approval --max-model-steps N` to implement the predicate function and its unit tests inside Muse Code's sandbox; a `PostToolUse` hook runs the tests, the exported event log is attached as the audit trail, and the predicate is hot-loaded only after human approval ([Muse Code extending docs](https://dev.meta.ai/docs/muse-code/extending)). This is Muse Code as a runtime component, not a dev tool, which Meta explicitly positions as a pipeline use.

### Pillar 6: Ask the site, analytics over perception events

Every tracked object, zone entry, dwell and alert lands in a DuckDB event store. Muse Spark 1.3 turns questions into SQL plus clip retrieval: "how many forklift-pedestrian near-misses by hour this week," "how long was the fire exit blocked today," "which rule fires most on the night shift." This is structured analytics over machine-generated events, not document RAG. A weekly safety brief is generated automatically: Spark writes it, **Muse Image lays out the infographic with its code-execution charts**, and the toolbox-talk sheet is produced in each language the crew speaks. Yorio and Moore's analysis of 25,000 establishments found lower-severity incidents predict later fatalities ([Risk Analysis 2018](https://ideas.repec.org/a/wly/riskan/v38y2018i4p839-852.html)), which is the evidence for treating near-misses as the headline metric.

### Pillar 7: Privacy and worker trust by design

Intenseye's own CEO describes union and frontline fears that the technology would be used "to monitor productivity or enforce punitive measures" ([CBS Brand Studio](https://www.cbsnews.com/brandstudio/news/building-the-ai-backbone-of-workplace-safety/)), and NSC's 2026 survey found only about half of workers had any role in technology decisions. v2 makes the opposite choice visible: Tier 1 verification runs locally on Glimmer; **faces are masked by a SAM 3.1 "face" pass before any clip leaves the site**; no identity or biometrics; alerts are anonymised by default and aggregated in reports ("dignity mode": coaching, not names); the live rule list is **published to workers** as an auto-generated poster in their languages (Muse Image); every rule change is logged with author and reason. Worker-facing transparency is a feature on the demo, not a footnote.

### Pillar 7b: Anonymous Mode, every human becomes a skeleton

A per-camera or per-site toggle. When it is on, **no human pixel is ever stored, displayed or sent off-site**. Every person in the frame is replaced by a stick-figure skeleton drawn over a clean background, so supervisors, reviewers and judges see what happened without ever seeing who it happened to.

**How the frame is rebuilt.**
1. **Find every person.** SAM 3.1 tracks the concept "person" with stable IDs, which gives a pixel-exact mask per worker rather than a loose box.
2. **Estimate the pose.** Each person crop goes to a keypoint model. First choice is Meta's open **Sapiens** pose model, so the skeleton itself is also a Meta model; fallback is RTMPose or MediaPipe Pose (Apache-2.0) if Sapiens is too slow on the site laptop. Stretch: Meta's **SAM 3D Body** for a 3D skeleton, which makes "bending", "reaching overhead" and "person down" far more robust than 2D.
3. **Erase the human.** The masked region is filled from a **clean background plate**, a running median of recent frames in which that pixel was not covered by any person mask. Pixels that have never been seen empty get a neutral blur. No generative inpainting is used, so no invented detail appears.
4. **Draw the skeleton.** Bones are rendered on the clean plate in a colour keyed to the track ID. Safety attributes become symbols on the skeleton instead of pixels: a helmet glyph on the head joint, a vest band on the torso, a harness line, a glove dot on each wrist. Each symbol is green when the item is present and red when it is missing.
5. **Normalise the body (optional, "strict" setting).** Bone lengths are rescaled to a canonical body so height, build and limb proportions cannot be used to recognise someone. Gait timing is kept because the rules need it.

**What still works, and what gets better.** All rules keep working because predicates never needed faces. Distance, zones, dwell, sequence and near-miss all use the foot point and track ID, which the skeleton preserves. PPE checks run on the raw frame **in memory on the site laptop**, and only the result travels on as a symbol. Skeletons also unlock a new class of **pose rules** that boxes cannot express, such as:
- "Alert if anyone lifts with a bent back."
- "Flag three-point contact lost on a ladder."
- "Warn if hands are above shoulder height near the press."
- "Person down for more than 10 seconds."

So privacy mode is not a degraded mode. For ergonomics it is the better mode.

**Where raw pixels are allowed to exist.** In strict anonymous mode, raw frames live only in RAM on the site machine for the length of one chunk. That requires **self-hosted SAM 3.1** on the site GPU, because the hosted API would receive raw frames. Tier 1 verification by Glimmer runs locally on the raw strip. Anything that leaves the site uses skeleton renders only: Tier 2 Spark Open verification, alerts on phones, the event store, clips in ask-the-site, the weekly brief and the demo video. Spark Open receives the skeleton strip plus the attribute table and the predicate trace, which is enough to reason about "bucket moving, person 1.6 m away, helmet present."

**Break-glass, done honestly.** Some sites will need the real footage after a serious incident. The default is that raw footage is never kept. An operator can enable an **encrypted, short-retention raw buffer** (for example 24 hours), and opening it requires two named approvers and writes an entry to the append-only log that workers can see. Without that setting there is nothing to unlock.

**Why it matters for the pitch.** Worker surveillance is the top objection to these systems. Intenseye's own CEO describes union fears about punitive monitoring, and competitors at best blur faces. Anonymous Mode answers the objection visually in two seconds of video: the camera understands everything about safety and nothing about identity. It also opens doors that face-blurring cannot, such as unionised sites, EU workplaces under GDPR and works-council rules, care facilities, schools and homes.

**Honest limits to state.** Skeletons reduce re-identification risk but do not eliminate it. Gait, clothing colour in the attribute symbols, and context such as "the only person on the night shift" can still point to someone. The strict setting normalises the body and drops clothing colour; the docs should say "privacy-preserving by design," never "impossible to identify."

### Pillar 9: Business Lens, the same cameras pay for themselves twice

Every safety rule already produces the raw material of operations analytics: who-is-where (as anonymous tracks), what-is-where (pallets, carts, products, vehicles), how long things dwell, and how often they move. Business Lens reuses the identical event store to answer the owner's second question after "is anyone getting hurt?", which is **"where is my business leaking time, stock and customers, and what should I do about it?"** Nothing new is captured; the same tracks are read through a second lens.

**What the owner sees, by pack.**

| Pack | Business metrics derived from existing tracks | Example insight the system writes |
|---|---|---|
| Warehouse | Forklift utilisation and idle share, dock-door turnaround, pallets per bay over time, aisle congestion heatmap, pick-zone dwell | "Dock 3 averages 52 min per truck versus 31 min at dock 1; the difference is the 14 min trucks wait for a forklift between 13:00 and 15:00" |
| Kitchen and restaurant | Station congestion versus ticket volume, table turnover, time from plating to pickup, queue length at the counter, cleaning cycle adherence | "Pass window plates wait 4.2 min at Friday dinner; one runner from 18:30 to 20:00 would cut it to under 2 min" |
| Retail | Footfall by hour, dwell by zone, queue length at registers, shelf-gap duration, stockroom-to-floor replenishment time | "Register queue exceeds five people for 38 min a day, all between 12:00 and 13:30; the end-cap on aisle 2 draws 9% of dwell but 1% of pickups" |
| Manufacturing cell | Cycle time per station, machine running versus stopped share, changeover duration, WIP piling at a station, first-pass yield proxies from rework loops | "Station 4 is the bottleneck 71% of the shift; upstream WIP peaks at 11 units" |
| Construction | Equipment utilisation, crew-on-task share by zone, material staging dwell, delivery wait times | "The excavator ran 3.1 h of an 8 h day; two deliveries waited 40 min each for a clear lay-down area" |
| Lab | Instrument occupancy, bench utilisation, consumable counts, time-to-first-use after setup | "The centrifuge is idle 80% of the day but booked 100%; two groups could share it" |
| Care facility operations | Call-light to staff-arrival time by corridor, dining-room throughput, corridor congestion at shift change | "Shift-change congestion at the north wing adds 6 min to response times" |

**How Meta models turn tracks into advice and action.**
- **Muse Spark 1.3** runs the weekly analysis: it queries the event store (text-to-SQL), finds bottlenecks and anomalies, quantifies them in minutes and money using the owner's own numbers (labour rate, margin, truck demurrage), and writes recommendations ranked by expected value with the evidence clips attached. It is also the engine behind **"Ask the business"** in plain language, voice included through Muse Voice Transcribe, so an owner on the floor can say "why was the dock slow Tuesday?" and get an answer with footage.
- **What-if planning.** Spark reasons over the real event distribution to answer "what happens if I open a second register at noon?" or "if I move staging to bay 2?", stated as an estimate with the assumptions listed, never as a promise.
- **Muse Image** lays out the owner-facing dashboard and the one-page weekly Business Brief with code-rendered charts, and produces shift-wall posters ("this week's bottleneck and the fix") in the crew's languages.
- **Muse Glimmer** keeps the floor-level analytics local for owners who never want operational data in the cloud, and acts as the independent checker that a recommendation's numbers match the query results before the brief is sent.
- **Muse Spark Open** on Baseten audits any recommendation above an owner-set money threshold, with its reasoning shown, so the brief says "checked by a second model" and means it.
- **The response agent (Muse Wonder or Spark)** can act on accepted recommendations through the same gated tool set: draft a staffing-shift proposal, create a replenishment task when a shelf gap persists, schedule a delivery window change, or, as a stretch, use computer use to update a schedule or inventory system. All irreversible actions stay behind the human gate.
- **Muse Code (stretch)** generates custom metric definitions on demand: when an owner asks for a metric the engine lacks ("time from truck arrival to first pallet off"), Spark specifies it, Muse Code implements it with tests in the sandbox, and it is hot-loaded after approval, exactly as with safety predicates.
- **Meta Business Agent Platform / WhatsApp delivery (stretch, unverified access).** Meta's Connect recap lists a Business Agent Platform handling over a billion messages a day; if developer access is available in the window, the weekly brief and daily alerts are delivered to the owner's WhatsApp, which is where most small-business owners already run their business. Build the brief as a plain message first and treat WhatsApp as an optional channel.

**Business rules use the same language as safety rules.** "Alert if the register queue exceeds five people for three minutes." "Flag a shelf gap longer than ten minutes." "Warn if a dock door is occupied for more than 45 minutes." They compile to the same Rule IR, pass the same Rule CI, and fire through the same verifier and agent. The owner learns one system, not two.

**Safety and business share one heatmap.** A near-miss hotspot is usually a flow bottleneck, and a blocked aisle is both a hazard and lost throughput. The brief shows both lenses on one floor plan, which is the pitch's ROI line: "the camera that keeps your crew safe also found 40 minutes a day in your dock."

**The line Business Lens never crosses.** Metrics are about **processes, stations, equipment and flows, never individuals**. There is no per-worker productivity score, no leaderboard, and no "who is slow" query; the compiler refuses to compile such a rule and says why. Anonymous Mode applies to every business clip. This is the opposite of the productivity-surveillance pattern that unions and works councils reject, and it should be said out loud in the demo.

**What is new here.** Voxel, Protex and Spot AI each sell some operations analytics (Voxel "Flow", Protex "Operations", Spot AI dashboards), but as fixed enterprise modules. The new pieces are: one rule language and one event store for both safety and operations; recommendations written in money terms with evidence clips and a second-model check; what-if estimates; gated actions; owner-level voice queries; local-only analytics on Glimmer; and the explicit no-individual-metrics guarantee.

### Pillar 8: Domain packs and the mobile audit

Each pack is 5–10 portable rules plus their test matrices and calibration notes: construction, warehouse, manufacturing cell, kitchen, lab, retail back-of-house, facilities, care-facility operations, home pool, utilities. Packs are plain files anyone can fork, and the demo switches between three of them live. **Mobile audit mode** (stretch): walk a site with a phone (or Ray-Ban Meta glasses if the Device Access Toolkit's camera stream works in the window, which is unverified), and every pack rule is evaluated on the fly to produce a punch list with annotated frames.

---

## 5. Architecture and model map

```
 Inputs                 Rule Studio                    Perception core                 Verification                 Response
 ─────────              ───────────                    ───────────────                 ────────────                 ────────
 voice (diarized) ─┐    Spark 1.3 ──► Rule IR ──┐      cameras ─► 3 s chunks ─► SAM 3.1 (parallel concepts,
 text ─────────────┼─►  structured     (yaml)    │               multiplex tracking, IDs, confidence)
 sign photo / PDF ─┘    output         │         │                    │
                        Glimmer back-  │         ▼                    ▼
                        translation ◄──┘   Rule CI: Spark test     Predicate + temporal engine (code)
                        + diff shown       matrix → Muse Image     homography, zones, dwell, sequence, near-miss
                                           scenes → SAM validate          │ candidate event
                                           → pipeline pass/fail           ▼
                                                                   Tier 1 Glimmer (local) ─► Tier 2 Spark Open
                                                                   verdict + reason          reasoning_content shown
                                                                          │ verified event (strict AND)
                                                                          ▼
                                                      Response agent (Wonder / Spark 1.3): speak, notify, log,
                                                      escalate, interlock, draft record, [computer-use filing]
                                                      HITL gate for irreversible actions · append-only log · replay
                                                                          │
                                                      Event store (DuckDB) ─► Ask-the-site (text→SQL + clips)
                                                      ─► weekly brief (Spark text + Muse Image infographic)
                                                      ─► Business Lens: bottlenecks, utilisation, queues, what-if, ranked
                                                         recommendations (Spark) · checked (Glimmer / Spark Open) · gated actions
                                           Muse Code (runtime, stretch): implements missing predicates + tests in sandbox
```

| Component | Meta model / surface | Runs where | Why this one |
|---|---|---|---|
| Spoken rule capture | Muse Voice Transcribe, DIARIZATION, keywords, code-switching | Hosted API | Only first-party ASR with diarization and 25-language code-switching in the same key |
| Rule compilation, test-matrix authoring, PDF and sign ingestion, text-to-SQL, brief writing | Muse Spark 1.3, structured output, PDF and image input | Hosted API | Strongest reasoning; PDF input undemoed in cookbook |
| Back-translation check, Tier 1 verifier, local privacy mode | Muse Glimmer 30B (GGUF or ExecuTorch, DFlash) | Site laptop (24–32 GB Mac or desktop GPU) | Open-weight, Apache 2.0, built for always-on local agents; qualifies for the open-weight bonus |
| Tier 2 verifier with visible reasoning | Muse Spark Open on Baseten (`meta-models/muse-spark-open-1026`) | Partner inference | Exposed `reasoning_content`; qualifies for the open-weight bonus |
| Detection, segmentation, tracking, face masking, synthetic-image validation | SAM 3.1 (hosted) and self-hosted `facebook/sam3.1` | Hosted API; optional desktop or Modal GPU | Open-vocabulary, 16-object multiplex, negative points, backward propagation, exemplars when self-hosted |
| Business Lens analysis, what-if, owner voice queries, brief and dashboard | Muse Spark 1.3 (analysis), Voice Transcribe (owner queries), Muse Image (dashboard), Glimmer and Spark Open (checks), Wonder/Spark agent (gated actions) | Hosted plus site laptop | Same models, second lens; no new capture |
| Anonymous Mode: person masks, pose, skeleton render | Self-hosted SAM 3.1 masks + Meta Sapiens pose (stretch: SAM 3D Body); RTMPose fallback | Site GPU only, raw frames never leave RAM | Pixel-exact person removal plus a skeleton that keeps every safety signal |
| Synthetic test scenes, infographics, worker posters | Muse Image 1.0 with reference images and code execution | Hosted API | $0.01 per image; agentic layout |
| Orchestration, sub-agents, computer-use filing (stretch) | Muse Wonder 1.0 (fallback Spark 1.3) | Hosted API | Computer-use tool with `pending_safety_checks`; sub-agent delegation |
| Runtime predicate synthesis with tests and audit log (stretch) | Muse Code (`muse exec --json`, hooks, `muse export`) | Site laptop | Sandboxed code generation with replayable event log |

That is eight distinct Meta surfaces in one product, each doing a job the others cannot.

---

## 6. The Rule IR (what "inspectable" means)

A compiled rule is a small typed document. Three examples from three packs.

```yaml
# construction/excavator-swing-radius.rule.yaml
id: excavator-swing-radius
source: { kind: voice, lang: "es/en", speaker: "foreman", text: "avísame si alguien está a menos de dos metros del balde de la excavadora" }
concepts:
  person:    { prompt: "person" }
  bucket:    { prompt: "excavator bucket" }
zones: []
calibration: ground-plane-homography@cam2
predicate:
  all:
    - distance_m: { from: person.foot_point, to: bucket.mask, lt: 2.0 }
    - moving: { object: bucket, speed_mps_gt: 0.2 }
temporal: { persist_s: 1.0 }
severity: high
actions: [speak(lang=auto), notify(role=crew_lead), log]
verify: { tier1: glimmer, tier2: spark-open-on-disagree, min_confidence: 0.7 }
tests: { generated: 12, passed: 12, regression: 7 }
```

```yaml
# kitchen/handwash-20s.rule.yaml
id: handwash-20s
source: { kind: text, text: "Everyone entering the line must wash hands for at least 20 seconds" }
concepts: { person: {prompt: "person"}, hands: {prompt: "hands"}, faucet: {prompt: "running faucet"} }
zones: { line_entry: polygon@cam1, sink: polygon@cam1 }
predicate:
  sequence:
    - enter: { object: person, zone: line_entry }
    - within_s: 90
    - satisfy: { dwell_s: { object: hands, overlapping: faucet, gte: 20, continuous: true } }
  on_fail: violation
severity: medium
actions: [notify(role=shift_lead), log]
verify: { tier1: glimmer }
```

```yaml
# retail/fire-exit-blocked.rule.yaml
id: fire-exit-blocked
source: { kind: sign_photo, text: "KEEP CLEAR — FIRE EXIT" }
concepts: { obstruction: {prompt: "box or cart or pallet"}, door: {prompt: "fire exit door"} }
zones: { egress: buffer_m(door, 1.5) }
predicate: { inside: { object: obstruction, zone: egress } }
temporal: { persist_s: 60 }
severity: high
actions: [speak, notify(role=facilities), escalate_after: 3]
verify: { tier1: glimmer, tier2: spark-open-on-disagree }
```

Readable by a safety officer, diffable in git, forkable into a pack.

---

## 7. What is reused and what is new (for the WHAT-WE-BUILT file)

**Reused, as dependencies with licences listed:** SAM 3.1 API and the `facebook/sam3.1` checkpoint; Muse Glimmer GGUF or ExecuTorch builds; Depth Anything V2 (Apache-2.0); OpenCV, ffmpeg, DuckDB, FastAPI, a React or Next.js front end; a YOLOv8 PPE model as the **baseline comparator only**; Meta Sapiens pose (CC-BY-NC-4.0, fine for a hackathon, flag for commercial use) or RTMPose/MediaPipe (Apache-2.0); an OS or open TTS voice. **New during the hackathon:** Rule Studio (voice, text, sign, PDF ingestion; zone tools; homography), the Rule IR and compiler with back-translation check, the chunked live SAM pipeline with identity hand-off and correction loop, the predicate and temporal engine, Rule CI (test matrix, Muse Image scene generation, SAM validation, harness), the two-tier verifier and rule-health monitor, the response agent with tools, gate and replayable log, the event store and ask-the-site, the brief and poster generator, the privacy masking pass, Anonymous Mode (background plate, skeleton renderer, PPE glyphs, body normalisation, pose rules, break-glass buffer), Business Lens (operations metrics per pack, recommendation engine with money framing and second-model check, what-if estimates, business rules, owner dashboard and brief), ten domain packs, and the evaluation set. Start the repo fresh on Oct 27 (the organisers require a standalone repo with only hackathon work) and open the video with the required one-line statement of what was pre-existing versus built this week.

---

## 8. Competitive positioning in one table

| | Verkada AI Alerts | Spot AI Iris | Hakimo | Brivo Eeva | Conntour | Protex AI | Intenseye | Voxel | NVIDIA VSS | Frigate | **Rulecam v2** |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Plain-language rules | Yes | Few-shot (20 images) | Yes | Yes | Yes | No (builder) | No | No | Yes (config) | No (CLIP triggers refused) | **Yes, voice + text + sign + PDF** |
| Arbitrary concepts | Yes | Trained | Yes | Yes | Yes | Fixed classes | Fixed classes | Fixed classes | Yes | Fixed | **Yes (SAM 3.1 open vocabulary)** |
| Temporal rules (dwell, sequence, absence) | Not documented | No | No | "How often to check" | No | Not documented | Partial | Partial | Partial | No | **Yes, first-class** |
| Rule shown as inspectable spec | No | No | No | No | No | Parameters | No | No | Prompt text | No | **Yes, versioned YAML** |
| Pre-deployment test | No | No | No | Historical preview | No | No | No | No | Hand-labelled clips | No | **Generated scenes + regression suite** |
| Independent second-model verification | No | No | Human | No | Confidence only | No | No | No | Yes (blueprint) | No | **Two-tier open-weight, reasoning shown** |
| Precision tracking, auto-pause | No | No | No | No | No | No | No | No | No | No | **Yes** |
| Operations analytics with written recommendations, money framing, second-model check, gated actions | Dashboards | Dashboards | No | No | Counts | "Operations" module | "Chief" Q&A | "Flow" module | Summaries | No | **Yes, same rule language, no per-person metrics** |
| Anonymous skeleton mode (no human pixels stored or shown) | No | No | No | No | No | Anonymisation, no skeleton | Face blur | Not documented | No | No | **Yes, with pose rules** |
| On-prem open-weight reasoning | No (cloud) | No | No | No (cloud) | On-prem, unnamed | On-prem, closed | Jetson, closed | Hybrid | Partial | Yes (llama.cpp) | **Yes (Glimmer local)** |
| Voice, multilingual authoring | No | No | No | No | No | No | No | No | No | No | **Yes, 25 languages** |
| Agentic response with human gate | Alerts | Alerts | Speaker + SOP | Alerts | Alerts | Alerts | "Hard Stop" | Alerts | Alerts | Automations | **Multi-step, gated, replayable** |
| Portable rules and packs | No | No | No | No | No | No | No | No | Config | Config | **Yes** |
| Price and hardware | $899–$4,299 proprietary cameras plus ~$33/cam/month | ~$99/cam/month (unofficial) | Quote | Not published | Not published | Quote, camera-count gated | Enterprise, Fortune 500 | Quote | Free blueprint, NVIDIA GPUs | Free | **Any camera, one laptop, per-site flat** |

Sources for every competitor cell are in `research_notes/Rulecam expansion/video_analytics_competitors.md`. Pricing for Spot AI and Conntour is third-party or unpublished and should be described as such.

---

## 9. How it scores, and which bonuses it targets

The organisers' FAQ says the four categories are "scored equally"; the participant guide gives 30/25/20/25. Either way the build must be strong on all four.

**Impact.** Universal, bottom-up, quantified. The pitch opens on deaths and costs, pivots to "the firms where half the deaths happen have no safety staff and will never be inspected," and lands on a laptop and the cameras they already own. The multilingual angle addresses the fastest-growing fatality group. Ten packs show the engine is horizontal, and Business Lens turns a cost centre into a tool owners will pay for: the same camera that prevents injuries finds lost throughput, which is the adoption story the Startups track wants.

**Use of Meta tooling.** Eight surfaces, each doing something the cookbook has not demoed: diarized code-switching voice input driving a compiler; PDF and sign ingestion; SAM 3.1 negative points and backward propagation as a correction UI; Muse Image as a test generator with reference-image grounding; Glimmer as a local verifier; Spark Open's reasoning trace on screen; Wonder computer use with safety checks; Muse Code as a runtime predicate synthesizer with hooks and an exported audit log.

**Technical depth.** A typed IR, a temporal predicate engine, homography calibration, identity hand-off across chunks, a two-tier verifier with strict AND and structured feedback, a sequential-test rule-health monitor, a replayable event log, and a published N-run precision number against a YOLOv8 baseline on a fixed clip set.

**Demo.** Three physical environments, a rule authored by voice in two languages mid-demo, a synthetic test matrix appearing and passing, a staged mis-segmentation that the verifier rejects and a click that fixes it, a verified alert that triggers a multilingual warning and stops a device, and an "ask the site" question answered with clips.

**Bonus fit.** Best Build on Meta Open Weight Models (Glimmer local plus Spark Open on Baseten, with logs and screenshots for partner verification, and "Built with Muse Spark Open" displayed as the licence requires). Best Agentic Use-Case (gated multi-step response, replayable, with recovery on camera). Best Creative Build (Muse Image as a test-scene generator is an original use). Community Vote and Most Viral (a 20-second clip of "I said the rule in Spanish and the forklift stopped" is shareable; post on day 7 and on submission day).

---

## 10. The three-minute demo (v2)

**0:00–0:20, the problem.** One sentence over footage: "Five thousand people died at work in the US last year. Half the construction deaths happen at firms too small to have a safety officer, and OSHA can inspect each workplace once every 191 years." Required opening line: everything shown was built this week; SAM 3.1, Glimmer and the listed libraries are dependencies.

**0:20–0:55, Rule Studio.** A teammate in a hi-vis vest speaks a rule in Spanish; a second teammate adds a clause in English in the same session. The diarized transcript, the compiled YAML and Glimmer's back-translation appear side by side; the author approves. The camera's own frame is used to generate twelve test scenes; SAM validates them; the test panel turns green.

**0:55–1:45, live enforcement across two environments.** Yard camera: masks track people, hats, vests and a vehicle standing in for the excavator; a teammate steps inside two metres; the predicate trace shows 1.6 m; Glimmer verifies; the speaker warns in Spanish; the lamp (or toy forklift) stops. Cut to the kitchen camera: a teammate washes hands for eight seconds and walks to the line; the dwell counter stops at 8 s; the sequence rule fires; the verifier agrees; the shift lead gets the clip.

**1:45–2:15, the recovery moment.** A staged mis-detection: a backpack strap is segmented as a harness and a candidate alert appears; the verifier panel shows Spark Open's reasoning rejecting it; the operator clicks the strap, which becomes a negative point, and the clip re-tracks backward with the false alert gone. The rule's precision gauge updates.

**2:15–2:25, Anonymous Mode.** One click on the toggle: the same live scene re-renders with every worker as a colour-coded skeleton on an empty yard, helmet and vest glyphs green, and a near-miss alert still fires. A pose rule ("lifting with a bent back") fires on a skeleton. Caption: "The camera knows everything about safety and nothing about who you are."

**2:25–2:45, ask the site, then Business Lens.** "How many forklift near-misses this week, by hour?" Four skeleton clips. Then the owner asks by voice: "Why was the dock slow on Tuesday?" The Business Brief renders: dock 3 at 52 min per truck versus 31, the 14-minute forklift wait highlighted on the floor plan next to the near-miss hotspot, a recommendation in minutes and dollars, "checked by Spark Open" with the trace, and a one-click proposal that waits for the owner's approval. "How many forklift near-misses this week, by hour?" The answer appears with four clips; the weekly brief renders with a Muse Image infographic and a Spanish toolbox-talk sheet; the worker-facing rule poster is shown on the wall.

**2:45–3:00, the numbers and the model map.** Precision versus the YOLOv8 PPE baseline on 60 staged clips, run five times; the eight-surface model map; "Built with Muse Spark Open"; the pack list.

Record the video from a real run on Nov 3 with a replay-mode switch that serves cached real API responses so the live finals cannot be broken by rate limits.

---

## 11. Nine-day plan for four people

**Roles.** A: Perception and predicates. B: Rule Studio and Rule CI. C: Verifier and response agent. D: Product, packs, evaluation and video.

**Oct 26 (tonight).** Fresh public repo with `WHAT-WE-BUILT.md`; claim Meta credits; Baseten, Modal and Fireworks requests; confirm Muse Wonder availability in your region; download Glimmer and run one local inference; buy or gather props (hard hats, vests, cones, a toy forklift or RC car, a smart plug, two webcams).

**Day 1, Oct 27.** Smoke tests that decide feasibility, all four in parallel: SAM 3.1 on "hard hat", "safety harness", "hands", "running faucet", "fire exit door", "face" on your own frames (A); Voice Transcribe diarization with Spanish-English code-switching and keyword bias (B); Glimmer verdict latency on a six-frame strip, and a Spark Open call on Baseten returning `reasoning_content` (C); Muse Image fidelity when given the camera frame as reference and asked for "same scene, a person without a hard hat near the vehicle" (D). Attend Agents 101 at noon ET. Decide by evening which of hosted or self-hosted SAM carries the live loop.

**Day 2, Oct 28.** A: chunked live pipeline and identity hand-off. B: Rule IR schema, compiler with structured output, back-translation diff. C: Tier 1 verifier service and event log. D: UI shell with live overlays and verifier panel. Attend Agents 102 and adopt its vocabulary in the README.

**Day 3, Oct 29.** A: homography tool, spatial predicates, negation. B: zone tools (draw, "around the X", walk-the-perimeter). C: Tier 2 escalation, strict AND, review queue. D: construction and kitchen packs, props test in the yard at the demo hour for lighting.

**Day 4, Oct 30.** A: temporal engine (dwell, sequence, absence, persistence), near-miss metrics. B: Rule CI (test matrix, Muse Image generation, SAM validation, harness). C: response agent tools (speak, notify, log, interlock) and HITL gate. D: retail and warehouse packs; start the 60-clip eval set. Attend the Fireworks session for the LoRA stretch.

**Day 5, Oct 31.** A: correction loop (negative points, backward propagation). B: sign-photo and PDF ingestion. C: rule-health sequential test, precision gauge, replay-mode switch. D: ask-the-site text-to-SQL and clip retrieval, plus the first Business Lens metrics (utilisation, dwell, queue length) over the same store. **Cut-line check:** if the core is not stable, drop computer-use filing, Muse Code self-extension, LoRA and glasses now.

**Day 6, Nov 1.** A: Anonymous Mode (background plate, pose model, skeleton renderer with PPE glyphs; strict body normalisation if time) and two pose rules. B: worker poster and weekly brief with Muse Image infographic. C: Muse Code runtime predicate synthesis (stretch) or hardening. D: Business Brief (recommendation engine, money framing, Glimmer check, Muse Image dashboard) and two business rules; remaining packs; N-run evaluation against the YOLOv8 baseline; README.

**Day 7, Nov 2.** Full rehearsals in all three environments; fix flakiness; post the first 20-second clip on X; freeze features.

**Day 8, Nov 3.** Record the video from a real run; cut it; write the submission; verify the public repo, licences and the open-weight evidence folder.

**Day 9, Nov 4.** Final fixes before noon, submit by early afternoon ET, hours ahead of the 9 PM deadline; post the second clip on X.

**Must-haves if everything else slips:** live rules in two environments, compile-and-inspect with back-translation, Rule CI with generated scenes, the two-tier verifier with Spark Open's trace, one gated agentic action chain, ask-the-site, Anonymous Mode with skeletons, one Business Brief with a checked recommendation, three packs, the N-run precision number.

---

## 12. Risks, gotchas and mitigations specific to this build

| Risk | Mitigation |
|---|---|
| Hosted SAM 3.1 is chunked, so live latency is 2–5 s | Fine for warnings and logging; for the interlock demo, self-host SAM 3.1 on a desktop GPU or Modal; show both paths honestly |
| One concept per SAM request; text and box prompts cannot be combined; negation unreliable in prompts | Parallel per-concept calls; negation and spatial logic live in the predicate engine, never in the prompt |
| Zero SAM matches returns an empty completion, not an error; frames may arrive out of order with box prompts | Treat empty as a valid negative; sort by frame index |
| Outdoor lighting and crowds beyond 16 objects | Test at the demo hour; zone the camera; split concepts across calls |
| Muse Image scenes may not respect geometry | SAM-validate every generated scene; regenerate on failure; present Rule CI as "catches compile errors and obvious misses," not as ground truth |
| Glimmer has no audio and no phone builds; 24–32 GB needed | Audio goes through Voice Transcribe; Glimmer runs on a Mac or desktop GPU; say "on-site laptop," not "on-device phone" |
| Muse Spark 1.3 audio is degraded | Never send raw audio to 1.3; Voice Transcribe handles speech |
| `tool_choice` is `auto` only | Use structured output for the compiler; prompt for tool use in the agent |
| Rate limits are per team (3,000 RPM, 4M TPM) and Muse Image is 150 RPM | Cache compiled rules and test scenes; generate test matrices once per rule version |
| Meta has no TTS | Use OS or open TTS and label it non-Meta in the model map |
| Muse Wonder is preview and unavailable in South Korea | Confirm on day 0; Spark 1.3 is the fallback orchestrator |
| Muse Code Workflows unavailable on Apple Silicon; hooks run outside the sandbox | Use `muse exec` plus hooks; gate hot-loading on your own tests and human approval |
| Contributor-mode pricing lets Meta train on prompts | Use Standard mode; demo data only |
| Business metrics drift into productivity surveillance | Process and station metrics only; the compiler rejects per-person rules and explains why; Anonymous Mode on all business clips |
| Recommendations overstate savings | Show assumptions and the query behind every number; second-model check; call what-if results estimates |
| Worker-surveillance optics | Anonymous Mode skeletons, published rule list, no identity; show the toggle on camera |
| Skeleton pipeline adds latency (pose per person per frame) | Run pose at 5–10 fps and interpolate; batch crops; render skeletons only for display while predicates use foot points |
| Background plate has holes where a person never moved | Neutral blur fill for never-seen pixels; pre-capture an empty-scene plate at setup |
| Pose fails on occlusion or odd angles | Fall back to the SAM mask silhouette filled with a flat colour, still no identity shown |
| Strict mode needs self-hosted SAM | Self-host on the site GPU or a dedicated Modal GPU; label the hosted path as "standard privacy" |
| Anti-project line on medical advice and politics | Care-facility and home packs are operational alerts only; no health judgments; pitch stays on operators and families |
| Spending beyond credits bills the card | Set alerts, cap `max_tool_calls`, and use replay mode for rehearsals |

---

## 13. Open questions to settle on day 1

Whether hosted SAM 3.1 segments "face", "hands" and "running faucet" reliably on your frames. Whether Muse Image honours a reference frame well enough for Rule CI to be credible. Whether Baseten's Spark Open endpoint accepts image input (Fireworks lists text and images; confirm) and returns `reasoning_content` as documented. Whether Muse Wonder is available to the team. Whether the Ray-Ban Device Access Toolkit gives a continuous frame stream (treat glasses as optional). What Agents 102 calls "verifiable," so the README uses Meta's words.

---

## 14. Naming

Keep **Rulecam** as the engine name; it is literal and memorable. If you want a product name with more reach for the Startups track, **Groundrule** ("ground rules for any space") or **Standing Orders** both carry the idea that rules are written down, visible and enforced. Tagline either way: *Say the rule. Read the rule. Test the rule. Trust the alert.*
