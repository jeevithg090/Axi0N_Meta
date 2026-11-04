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
recorded run" and switch to the recorded page (REPLAY). Warm everything at T-60 min
([`docs/demo-day.md`](../demo-day.md)), and plan once on the stage camera beforehand so the
survey is reused.

1. `https://preferred-florist-labeled-trace.trycloudflare.com/usecases/warehouse`: say or type the forklift rule in Spanish, press "Plan
   this rule". "Any language. Llama Guard screens it, Spark plans it, and it tells me what the
   camera can't see." On a surveyed camera this took 42–45 s on 4 Nov; talk over it.
2. Rule CI turns green: "Before I can approve it, it attacks the rule. Sixteen edge cases, six
   broken copies. All caught."
3. The verdicts (REPLAY cards on the same page): "Glimmer, Meta's open model on our own GPU,
   confirmed six near misses out of six live, and knew the seated driver was the operator."
4. The proof portal share link: "An inspector's own browser re-checks every record and every
   number. Change one, and it turns red."

Then, pointing at the other two labels: "The same parts warn a venue before a crowd crush, and
answer a shop owner's questions. Here are the numbers."

---

## Slide 4. How it works

**On the slide**

Four words across the top, each with one line:

1. **Describe.** Say what worries you, in any language. Llama Guard 4 screens it.
2. **Plan.** Muse Spark plans it; SAM 3.1 checks what the camera can see and measures metres.
3. **Prove.** Rule CI attacks the rule before a person approves it.
4. **Trust.** Muse Glimmer checks every alert (Muse Spark Open can check again); warnings are
   heard back before they play; everything is hash-chained; anyone can verify.

Bottom strip: "Any camera you have · Alerts to roles, never names · No face recognition ·
Workers can dispute any alert".

**Speaker notes (30 s)**

"Four steps. You describe the worry. Muse Spark turns it into a plan, and SAM 3.1 checks what
the camera can actually see and measures real distances. Rule CI attacks the rule the way tests
attack code. A person approves it. Then, at run time, every alert is checked by Glimmer before
anyone is told, and every step goes into a log you can verify yourself. Workers can read every
rule and dispute any alert. A false alarm becomes a permanent test."

---

## Slide 5. Proof: what we measured

**On the slide**

| World | Measured live (4 Nov) | Source |
|---|---|---|
| Warehouse | Glimmer confirmed **6 of 6** near misses at **0.98–0.99**; the Spanish warning heard back with word error **0.09–0.18**, all passed; plan in **42–45 s** on a surveyed camera | [warehouse](../../reports/eval/2026-11-04-warehouse-live.md) |
| Crowd | Counts within **0–4%** of the Jülich truth; Glimmer confirmed in **6.2 s**, stewards told **26 s** after the reading; a mistranslated Hindi announcement caught and held back | [crowd](../../reports/eval/2026-11-04-crowd-live.md) |
| Supermarket | Busy-frame count **22 → 10** with three models (about **12** by hand); spill seen to cleared in **1.9 min**, checked by photo (the spill image is a generated SAMPLE) | [supermarket](../../reports/eval/2026-11-04-supermarket-live.md) |
| Trust | Proof pack: **395** records and **97** numbers re-checked in the browser in **0.3–0.4 s**; one edited number turns it red | [proof portal](../../reports/eval/2026-11-04-proof-portal.md) |
| Weak, and shown | Ask's counts on hand-labelled CCTV: **7 of 36** within tolerance (2 Nov). Glimmer once held back a true crowd alert; Spark now decides when Glimmer disagrees (unit-tested, not yet seen live) | [Ask benchmark](../../reports/eval/ask/2026-11-02T230520Z-corridor.json), [crowd](../../reports/eval/2026-11-04-crowd-live.md) |

Beside the table: the crowd chart ([`media/crowd-density-vs-truth.png`](media/crowd-density-vs-truth.png))
or the supermarket card "22 → 10 people" (`ui/public/screens/11-supermarket-journey.png`).

**Speaker notes (40 s)**

"Every number here links to the run that produced it. In the warehouse, Glimmer confirmed six
near misses out of six, and the Spanish warning was checked by listening to it. On real footage
from a Jülich crowd experiment, counts stayed within four percent of the truth, and stewards
were told 26 seconds after the reading. In the supermarket, the tracker said 22 people on a
busy frame; three Meta models counting the same frame said 10, and a person counted about 12.
And an inspector's browser re-checks 395 records in under half a second. We also show what
doesn't work yet, because the whole product is about not hiding things."

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
- **Proof portal:** proof packs for inspectors and insurers, re-checked in their own browser,
  paid per site.
- **Staff app** is included: alerts go to roles, in each person's language.

Under it: "Measured GPU cost per alert check: $0.0039 (20 at once, 2 Nov). Cost per
camera-hour: not yet measured."

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
| Muse Spark | Plans rules, runs the Ask agent, judges crowds and plans routes, reviews disputes, searches the web with checked sources | Live |
| SAM 3.1 | Sees: surveys, probes, tracks, counts heads | Live |
| Muse Glimmer (open weights) | Checks every alert, on our own GPUs | Live |
| Muse Spark Open (open weights) | Second check, plan audit, Ask review, on our own 8 x H100 | Live |
| Muse Code | Writes new rule types; Z3 proves them | Live |
| Llama Guard 4, Prompt Guard 2 | Refuses rules about who people are; firewall for text in the scene | Live |
| Voice Transcribe, MMS-TTS | Listens and speaks, in the user's language, and checks itself by listening back | Live |

One line under it: "Each model is checked by another part of the system."

**Speaker notes (40 s)**

"We use Meta's models as a team that checks itself. Spark plans and reasons. SAM 3.1 sees and
measures. Glimmer and Spark Open, which we run ourselves as open weights, check every alert.
Four things surprised us. Glimmer could tell the forklift operator from a pedestrian, which our
rule engine couldn't. No single model counts a busy queue right, but SAM, Spark and Glimmer
together got within two people. Listening back caught a Hindi announcement that said 'crowd'
instead of 'gate'. And Z3 found a divide-by-zero in a check Muse Code wrote; the repair round
fixed it."

> Spark Open costs $43.12 an hour warm and takes about 18 minutes to start
> ([`docs/demo-day.md`](../demo-day.md)). Warm it before the finals or leave it out of the live
> moment.

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

> Plain words in. Verified alerts out.

- **Pilots:** one warehouse, one supermarket, one venue or station, on cameras they already own.
- **Meta:** help putting glasses mode on Ray-Ban Meta glasses (the Wearables Device Access
  Toolkit), so a supervisor can walk the floor with it.
- **You:** open `https://preferred-florist-labeled-trace.trycloudflare.com/demo?demo=1` and press **Verify it in your browser**.

**Speaker notes (20 s)**

"We're asking for three pilots, one in each world, on cameras they already own. From Meta,
help putting glasses mode on real glasses. And from you: open the demo and press verify.
Your own browser will check our evidence. Plain words in. Verified alerts out. Thank you."

---

## Questions we expect, and honest answers

| Question | Answer |
|---|---|
| How is this different from Verkada or Spot AI? | They let you describe an alert. We tell you what the camera can't check, attack the rule before it goes live, check every alert with a second model, and keep a record anyone can verify. On cameras you already own. |
| How accurate is it? | Live on 4 Nov: Glimmer confirmed 6 of 6 forklift near misses; crowd counts were within 0–4% of the Jülich truth; a busy-frame count came to 10 against about 12 by hand. Weak: Ask's counts on hand-labelled CCTV were 7 of 36 within tolerance (2 Nov). We publish all of it. |
| Did Glimmer check the crowd alert? | When it had a GPU, yes: 0.95 in 6.2 s in run 6. When no GPU was free, alerts went out unchecked and said so; a late check never holds a warning back. Once Glimmer held a true alert (it would not confirm more than 5 per m² from one image); now Spark decides to send or hold when Glimmer disagrees, unit-tested and not yet seen live. |
| Isn't this surveillance? | No face recognition, ever. Faces are blurred in evidence by default. Results are per rule and per store, never per worker. Workers see every rule and can dispute any alert. Our hackathon deployment runs a less strict prototype mode, and we document exactly what it changes. |
| Does Meta train on your footage? | Our hosted API key is Standard tier, never Contributor. Glimmer and Spark Open, which check alerts, run on open weights on our own GPUs (a hosted Glimmer is the last fallback). All our measured runs used public footage or generated images labelled SAMPLE. |
| What does it cost to run? | $0.0039 of GPU time per alert check with 20 at once (2 Nov). Spark Open is the expensive part: $43.12 an hour warm, so it runs only when needed. Cost per camera-hour is not measured yet. GPUs scale to zero when idle, so cold starts are slow (minutes). |
| What if the model is wrong? | Outside prototype mode, a rejected alert on a high-severity rule still goes to a person. A disputed alert becomes a permanent Rule CI test. |
| Is it a safety system? | No. It helps people watch. It does not replace a guard, a lock-out or a supervisor. |
| Has it run on real CCTV or real glasses? | Not yet. Connectors were tested on streaming servers and a simulated ONVIF recorder; glasses mode on a phone page with stock stills; push in Chromium. Each report says so. |
