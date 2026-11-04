# Rulecam: the finals deck (10 slides)

For the live finals (top five per track, Nov 9). Built for about **4 minutes of talk plus a
60-second live moment**, then questions. Each slide has what goes on it (few words, one visual)
and the speaker notes (what to say, in plain words). Every number links to a committed report
or to its public source; the market research behind slides 1, 6 and 7 is in
[`research/`](research/) (retail, industrial, crowd; researched 4 Nov 2026).

Rules for the stage: never say "first natural-language camera rules" (Verkada, Hakimo, Spot AI,
Brivo Eeva and Conntour all have some version). Never call a replay live. Never use a real
crowd disaster as a hook; talk about what inquiries found, with respect.

---

## Slide 1. The problem

**On the slide**

> The cameras saw it. Nobody was watching.

Three small images in a row: a forklift passing a worker, a crowd at a gate, a checkout queue.
Under them, three facts:

- One retailer found **under 1%** of its recorded video is ever viewed ([Security Magazine](https://www.securitymagazine.com/articles/76977-so-why-isnt-shrink-shrinking-1)).
- Forklifts: about **85 deaths and 34,900 serious injuries** a year in the US ([OSHA](https://www.osha.gov/sites/default/files/enforcement/directives/reg1_fy2018_cpl-04-00-023e_powered-industrial-trucks.pdf)). **One in four** blue-collar workers saw a near miss last year and didn't report it ([TRADESAFE survey](https://trdsf.com/blogs/news/workplace-safety-silence-reporting-risks)).
- Crowd inquiries keep finding the same thing: **the warning existed, and it did not become action** ([our research](research/crowd.md#what-repeats)).

**Speaker notes (35 s)**

"Small sites already have cameras. A warehouse, a supermarket, a station gate. What they don't
have is someone watching. One retailer found that less than one percent of its video is ever
looked at. In a warehouse, a quarter of near misses are never reported. And when crowd
inquiries look back, they keep finding the same thing: someone saw the warning, and it never
became action. The cameras saw it. Nobody was watching."

---

## Slide 2. The insight

**On the slide**

> Typing a camera rule is easy now. Trusting it is not.

Three pains, each with one line:

- **False alarms teach people to ignore alerts.** Police cited burglar-alarm false-alarm rates
  of 92% in Los Angeles ([Security Sales](https://www.securitysales.com/news/lapd-still-responding-to-most-alarms-despite-new-policy/)).
- **Owners can't check vendor claims.** Almost every outcome number in this market is
  self-reported ([research](research/industrial.md#2-pain-points-with-evidence)).
- **Workers fear being watched.** France's CNIL fined Amazon's warehouse unit €32M for
  intrusive monitoring ([Privacy International](https://privacyinternational.org/examples/warehouse-workers)).

**Speaker notes (30 s)**

"Plain-language camera alerts are not new. Several companies sell them. The problem is what
comes after. Will the rule really work? Is this alert real? Who else sees the footage? Owners
have no way to check, workers have no say, and false alarms teach everyone to stop listening.
So we built Rulecam around one idea: every step should come with proof."

---

## Slide 3. The demo: one engine, three worlds

**On the slide**

The hero image ([`media/hero-same-rule.jpg`](media/hero-same-rule.jpg)): same rule, Glimmer
confirms the worker (0.99), rejects the seated driver (0.05). Three labels underneath:
**Warehouse · Crowd · Supermarket**. The LIVE / REPLAY badge visible on anything shown.

**Speaker notes (60 s live moment, then 20 s)**

The live moment, in this order. If any step stalls for more than 10 seconds, say "this is the
recorded run" and switch to `/demo?demo=1` (REPLAY). Warm the GPUs at T-60 min (`docs/DEMO.md`).

1. `/planner`: type the forklift rule in Spanish. "Any language. It plans, and it tells me
   what the camera can't see." (Do not wait for the plan; open the finished one.)
2. `/demo?demo=1`, Rule CI: "Before I can approve it, it attacks the rule. Sixteen edge
   cases, six broken copies. All caught."
3. The two verdicts (T02 and T04 goldens, REPLAY): "Same rule fires twice. Glimmer, Meta's open
   model on our own GPU, confirms the worker and rejects the driver. Nobody is bothered for
   the driver."
4. Click **Verify it in your browser**: "Your browser recomputes every fingerprint. You don't
   have to trust us."

Then, pointing at the other two labels: "The same parts warn a venue about crowd density, and
answer a shop owner's questions. Let me show you the numbers."

---

## Slide 4. How it works

**On the slide**

Four words across the top, each with one line:

1. **Describe.** Say what worries you, in any language.
2. **Plan.** Muse Spark plans it; SAM 3.1 checks what the camera can see and measures metres.
3. **Prove.** Rule CI attacks the rule before a person approves it.
4. **Trust.** Muse Glimmer checks every alert; everything is hash-chained; anyone can verify.

Bottom strip: "Phone or the CCTV you have · No face recognition · Workers can dispute any alert".

**Speaker notes (30 s)**

"Four steps. You describe the worry. Muse Spark turns it into a plan, and SAM 3.1 checks what
the camera can actually see and measures real distances. Rule CI attacks the rule the way tests
attack code. A person approves it. Then, at run time, every alert is checked by Glimmer before
anyone is told, and every step goes into a log you can verify yourself. Workers can read every
rule and dispute any alert. A false alarm becomes a permanent test."

---

## Slide 5. Proof: what we measured

**On the slide**

| World | Measured | Source |
|---|---|---|
| Warehouse | Glimmer **0.99** on the worker 2.90 m from a moving forklift; **0.98** on the same rule in Spanish; **0.05** on the seated driver, no alert; **19.0 s** event to verified decision | [live campaign, 1 Nov](../../reports/eval/2026-11-01-live-test-campaign.md) |
| Crowd | SAM 3.1 found **95–99%** of true heads; gate density within about **10%**; reading to alert about **40 s** | [Jülich run, 3 Nov](../../reports/eval/2026-11-03-crowd-live.md) |
| Supermarket | **13 of 13** owner questions answered on real checkout video, **10** fully right | [Ask run, 1 Nov](../../reports/eval/2026-11-01-supermarket-ask.md) |
| Cost | **$0.0039** GPU cost per alert check, 20 at once | [load test, 2 Nov](../../reports/eval/2026-11-02-serverless-load.json) |
| Weak, and shown | Ask counts within tolerance on hand-labelled CCTV: **7 of 36** | [Ask benchmark](../../reports/eval/ask/2026-11-02T230520Z-corridor.json) |

Beside the table: the crowd chart ([`media/crowd-density-vs-truth.png`](media/crowd-density-vs-truth.png)),
Rulecam's gate density against the truth, reading by reading.

**Speaker notes (35 s)**

"Every number here links to the run, the date and the commit that produced it. In the
warehouse, Glimmer confirmed a real violation at 0.99 and rejected the seated driver at 0.05.
On real footage from a Jülich crowd experiment, SAM 3.1 counted 95 to 99 percent of the true
heads, and the alert came about 40 seconds after the reading. We also show what doesn't work
yet: Ask's counts on hand-labelled CCTV were within tolerance 7 times out of 36. We publish
that, because the whole product is about not hiding things."

---

## Slide 6. Market, and the gaps nobody fills

**On the slide**

Three columns, one per world. Each has who buys, what exists, and the open gap.

| | Warehouses (10–200 people) | Supermarkets and small chains | Venues, stations, gatherings |
|---|---|---|---|
| What exists | Enterprise safety cameras (Voxel, Protex, Intenseye) with hidden prices; tags and truck cameras | Cloud cameras with search (Verkada, Spot AI at about $99 per camera per month, unverified); theft-only tools | Threshold alarms (count over a number); government-scale forecasting |
| The gap | A verified near miss tied to the OSHA duty it triggers; warnings in the crew's language; evidence an insurer can re-check | Proof a hazard was seen **and fixed**; shelf gaps without new hardware; answers with a receipt | An alert that becomes action: a named steward, an acknowledgement, a calm announcement, a record |
| Why it matters | One medically consulted work injury costs about **$48,000** ([NSC](https://injuryfacts.nsc.org/work/costs/work-injury-costs/)) | Out-of-stocks cost about **7.4%** of sales (NielsenIQ, [cited here](https://a-closer-look.com/wp-content/uploads/2024/09/Report-Out-of-Sight-Out-of-Mind-The-Hidden-Cost-of-Empty-Shelves-and-Broken-Displays_FINAL-sm.pdf)) | Build-up takes tens of minutes to hours: there is time to act ([research](research/crowd.md#realistic-lead-time)) |

**Speaker notes (40 s)**

"Three markets, one gap. Big safety-camera vendors sell to enterprises and hide their prices.
Cloud cameras make you buy their hardware. What a 50-person warehouse can't buy is a near miss
that's verified, tied to the OSHA training it legally triggers, and recorded so an insurer can
check it. A supermarket can't buy proof that a spill was seen and then cleaned up, which is
exactly what a slip-and-fall case turns on. And for crowds, inquiry after inquiry found the
warning existed and didn't become action. Rulecam is built around the step after the alert."

Sources for the notes: OSHA refresher-training clause 1910.178(l)(4)(ii)(B) and slip-and-fall
timing evidence are in [`research/industrial.md`](research/industrial.md#3-the-economics) and
[`research/retail.md`](research/retail.md#the-costs-a-small-shop-actually-carries).

---

## Slide 7. Business model

**On the slide** (labelled **Proposed pricing, to test with pilots**)

- **Free:** one phone camera, three rules. Brings in the owner.
- **Site:** a monthly price per camera on the CCTV they already own, with verified alerts,
  Rule CI and the evidence log. Priced well under the roughly $99 per camera per month that
  cloud camera platforms are reported to charge (unverified).
- **Chain:** one rule pack for every store, a store-level scorecard (never per worker), one
  evidence root for head office.
- **Proof portal:** read-only evidence packs for inspectors and insurers, paid per site.

Under it: "Measured GPU cost per alert check: $0.0039 (20 at once). Cost per camera-hour: not
yet measured."

**Speaker notes (30 s)**

"We sell to the small site the big vendors skip. Free for one phone, so an owner can try it
today. Then a monthly price per camera on the cameras they already own. Chains pay for one rule
pack across every store. The proof portal is for the people who pay for safety: inspectors and
insurers. We measured the GPU cost of checking one alert at about four tenths of a cent. We have
not yet measured cost per camera-hour, so the price is a hypothesis we will test with pilots."

> Fill in the Site price only after the team agrees it. Do not put a margin on the slide until
> cost per camera-hour is measured (`rulecam scorecard`, Cost rows).

---

## Slide 8. Why Meta's models

**On the slide**

| Model | Its job | Live? |
|---|---|---|
| Muse Spark | Plans rules, runs the Ask agent, judges crowd levels, searches the web with checked sources | Live |
| SAM 3.1 | Sees: surveys, probes, tracks, counts heads | Live |
| Muse Glimmer (open weights) | Checks every alert, on our own GPU | Live |
| Voice Transcribe, MMS-TTS | Listens and speaks, in the user's language | Live |
| Muse Code | Writes new rule types; Z3 proves them | Built |
| Spark Open, Prompt Guard 2 | Second check; scene-text firewall | Built, not live yet |

One line under it: "Each model is checked by another part of the system."

**Speaker notes (35 s)**

"We use Meta's models as a team that checks itself. Spark plans and reasons. SAM 3.1 sees and
measures. Glimmer, which we run ourselves as open weights, checks every alert. Three things
surprised us. Glimmer could tell the forklift operator from a pedestrian, which our rule engine
couldn't. SAM 3.1 counted heads in a dense crowd from above almost perfectly. And SAM can't see
'empty shelf', so we let Spark propose the gaps and SAM confirm there's no product. Spark Open
and Prompt Guard are built but not live in our deployment yet, and we say so."

> If Spark Open or Prompt Guard 2 goes live with a report before the finals, change their row
> to Live and add the number.

---

## Slide 9. Team

**On the slide**

Four photos, names and one line each. Confirm names and roles with each person.

| Name | Built |
|---|---|
| Jeevith (lead) | Pixels to events: capture, calibration, tracking, the rule engine, crowd watch, deploys |
| Nava | Request to proven plan: the planner, Rule CI, rule cards, worker disputes, Site Advisor pipeline |
| Raunak | The platform: contracts, CI, replay, the API and runtime loop, verification, evidence chain, actions, UI |
| Aris | Privacy rendering (pose, face blur, skeletons), statistics, and every evaluation |

Bottom line: "Built in 10 days, with every module and its pull request listed in
`WHAT-WE-BUILT.md`."

**Speaker notes (15 s)**

"We're four people. We wrote everything during the hackathon; every module and its pull request
is listed in the repo, with the licence of everything we reused."

---

## Slide 10. The ask

**On the slide**

> Plain words in. Proof out.

- **Pilots:** one warehouse, one supermarket, one venue or station, on cameras they already own.
- **Meta:** access to Muse Spark Open for the second check, and the gated Prompt Guard 2 model.
- **You:** open [rulecam.vercel.app/demo](https://rulecam.vercel.app/demo?demo=1) and press
  **Verify it in your browser**.

**Speaker notes (20 s)**

"We're asking for three pilots, one in each world, on cameras they already own. From Meta, the
access we need to switch on the second check. And from you: open the demo and press verify.
Your own browser will check our evidence. Plain words in. Proof out. Thank you."

---

## Questions we expect, and honest answers

| Question | Answer |
|---|---|
| How is this different from Verkada or Spot AI? | They let you describe an alert. We tell you what the camera can't check, attack the rule before it goes live, check every alert with a second model, and keep a record anyone can verify. On cameras you already own. |
| How accurate is it? | The forklift rule worked end to end on real video (0.99 confirm, 0.05 reject). Crowd counts were 95–99% of truth. Ask's counts are weak: 7 of 36 within tolerance on hand-labelled CCTV. We publish all of it. |
| Did Glimmer check the crowd alert? | Not in the 3 Nov run: no GPU was free, so the alert went out unchecked after 20 s, as designed. A slow check never holds a warning back. |
| Isn't this surveillance? | No face recognition, ever. Faces are blurred in evidence by default. Results are per rule and per store, never per worker. Workers see every rule and can dispute any alert. Our hackathon deployment runs a less strict prototype mode, and we document exactly what it changes. |
| Does Meta train on your footage? | Our hosted API key is Standard tier, never Contributor. The model that sees alert frames runs on open weights on our own GPU. All our measured runs used public stock or research footage. |
| What does it cost to run? | $0.0039 of GPU time per alert check with 20 at once. Cost per camera-hour is not measured yet. GPUs scale to zero when idle, so cold starts are slow (minutes). |
| What if the model is wrong? | Outside prototype mode, a rejected alert on a high-severity rule still goes to a person. A disputed alert becomes a permanent Rule CI test. |
| Is it a safety system? | No. It helps people watch. It does not replace a guard, a lock-out or a supervisor. |
