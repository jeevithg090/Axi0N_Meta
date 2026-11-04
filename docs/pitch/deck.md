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
confirms the worker (0.99), rejects the seated driver (0.05). Small print: "Recorded 1–2 Nov,
Glimmer on our own GPU." Three labels underneath:
**Warehouse · Crowd · Supermarket**. The LIVE / REPLAY badge visible on anything shown.

**Speaker notes (60 s live moment, then 20 s)**

The live moment, in this order. If any step stalls for more than 10 seconds, say "this is the
recorded run" and switch to the recorded page (REPLAY). Start the API and run the model
proofs at T-30 min ([`docs/demo-day.md`](../demo-day.md)); there are no GPUs to warm. Plan once
on the stage camera beforehand so the survey is reused.

1. `https://rulecam-live.vercel.app/usecases/warehouse`: say or type the forklift rule in Spanish, press "Plan
   this rule". "Any language. Spark plans it, and it tells me what the camera can't see." On
   the Meta API this plan took 146 s on 4 Nov, longer than the live moment: start it before you
   speak and talk over it, or open the plan made beforehand. (On a surveyed camera it took
   42–45 s before the switch; survey reuse has not been timed on the Meta API.)
2. Rule CI turns green: "Before I can approve it, it attacks the rule. Sixteen edge cases, six
   broken copies. All caught."
3. The verdicts (REPLAY cards on the same page): "Before we moved to the Meta API, Glimmer,
   Meta's open model, on our own GPU, confirmed six near misses out of six live, and knew the
   seated driver was the operator. Today the rule runs live on a stock clip, and Muse Spark 1.3
   checks each alert."
4. The proof portal share link: "An inspector's own browser re-checks every record and every
   number. Change one, and it turns red."

Then, pointing at the other two labels: "The same parts warn a venue before a crowd crush, and
answer a shop owner's questions. Here are the numbers."

---

## Slide 4. How it works

**On the slide**

Four words across the top, each with one line:

1. **Describe.** Say what worries you, in any language. The planner's guardrails screen it.
2. **Plan.** Muse Spark plans it; SAM 3.1 checks what the camera can see and measures metres.
3. **Prove.** Rule CI attacks the rule before a person approves it.
4. **Trust.** Muse Spark 1.3 checks every alert (Muse Wonder can check again); warnings are
   heard back before they play; everything is hash-chained; anyone can verify.

Bottom strip: "Any camera you have · Alerts to roles, never names · No face recognition ·
Workers can dispute any alert".

**Speaker notes (30 s)**

"Four steps. You describe the worry. Muse Spark turns it into a plan, and SAM 3.1 checks what
the camera can actually see and measures real distances. Rule CI attacks the rule the way tests
attack code. A person approves it. Then, at run time, every alert is checked by a second Meta
model before anyone is told, and every step goes into a log you can verify yourself. Workers
can read every rule and dispute any alert. A false alarm becomes a permanent test."

---

## Slide 5. Proof: what we measured

**On the slide**

| World | Measured live (4 Nov) | Source |
|---|---|---|
| Warehouse | **Meta API only:** plan in **146 s**, Rule CI **16/16** edge cases and **6/6** mutants, approved with two amber caveats (forklift detection lower bound **0.797** vs the 0.80 bar; calibration error **0.20 m** vs 0.10 m), running live with Muse Spark 1.3 checking each alert. **Before the switch, Glimmer on our own GPU:** confirmed **6 of 6** near misses at **0.98–0.99**; the Spanish warning heard back with word error **0.09–0.18**, all passed; plan in **42–45 s** on a surveyed camera | Meta API only: measured 4 Nov, no report yet. Before: [warehouse](../../reports/eval/2026-11-04-warehouse-live.md) |
| Crowd | **Recorded, not live now.** Counts within **0–4%** of the Jülich truth; Glimmer on our own GPU confirmed in **6.2 s**, stewards told **26 s** after the reading; a mistranslated Hindi announcement caught and held back | [crowd](../../reports/eval/2026-11-04-crowd-live.md) |
| Supermarket | **Meta API only:** an Ask answer in **57.4 s**. **Before the switch:** busy-frame count **22 → 10** with three models, Glimmer one of them (about **12** by hand); spill seen to cleared in **1.9 min**, checked by photo (the spill image is a generated SAMPLE) | Meta API only: measured 4 Nov, no report yet. Before: [supermarket](../../reports/eval/2026-11-04-supermarket-live.md) |
| Trust | Proof pack: **395** records and **97** numbers re-checked in the browser in **0.3–0.4 s**; one edited number turns it red | [proof portal](../../reports/eval/2026-11-04-proof-portal.md) |
| Weak, and shown | Ask's counts on hand-labelled CCTV: **7 of 36** within tolerance (2 Nov). Glimmer once held back a true crowd alert; Spark now decides when the Tier 1 check disagrees (unit-tested, not yet seen live) | [Ask benchmark](../../reports/eval/ask/2026-11-02T230520Z-corridor.json), [crowd](../../reports/eval/2026-11-04-crowd-live.md) |

Under the table, small: "Since 4 Nov the live system uses only the Meta API. Glimmer results
were measured on our own GPUs before that."

Beside the table: the crowd chart ([`media/crowd-density-vs-truth.png`](media/crowd-density-vs-truth.png))
or the supermarket card "22 → 10 people" (`ui/public/screens/11-supermarket-journey.png`).

**Speaker notes (40 s)**

"Every number here comes from a run. Most were measured before we moved everything to the
Meta API, when Glimmer ran on our own GPU. In the warehouse, Glimmer confirmed six near misses
out of six, and the Spanish warning was checked by listening to it. On the Meta API alone, the
same Spanish rule planned in under two and a half minutes and passed every Rule CI check, with
two amber caveats. On real footage from a Jülich crowd experiment, counts stayed within four
percent of the truth, and stewards were told 26 seconds after the reading. In the supermarket,
the tracker said 22 people on a busy frame; three Meta models counting the same frame said 10,
and a person counted about 12.
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

Under it: "Measured GPU cost per alert check on our own GPUs: $0.0039 (20 at once, 2 Nov, before
the switch to the Meta API). Cost per check on the Meta API and cost per camera-hour: not yet
measured."

**Speaker notes (30 s)**

"We sell to the small site the big vendors skip. Free for one phone, so an owner can try it
today. Then a monthly price per camera on the cameras they already own. Chains pay for one rule
pack across every store. The proof portal is for the people who pay for safety: inspectors and
insurers. On our own GPUs, before we moved to the Meta API, checking one alert cost about four
tenths of a cent. We have not yet measured the cost per check on the Meta API, or the cost per
camera-hour, so the price is a hypothesis we will test with pilots."

> Fill in the Site price only after the team agrees it. Do not put a margin on the slide until
> cost per camera-hour is measured (`rulecam scorecard`, Cost rows).

---

## Slide 8. Why Meta's models

**On the slide**

| Model | Its job | Now |
|---|---|---|
| Muse Spark 1.3 (1.2 for video) | Plans rules, screens requests, runs the Ask agent, checks every alert (Tier 1), retail checks, searches the web with checked sources | Live, Meta API |
| Muse Wonder 1.0 | Second check: disputes, plan audit, Ask answer review | Live, Meta API |
| SAM 3.1 | Sees: surveys, probes, tracks, counts heads | Live, hosted by Meta |
| Muse Code | Writes new rule types; Z3 proves them | Live, local `muse` CLI |
| Voice Transcribe, MMS-TTS, Muse Image | Listens and speaks in the user's language, and checks itself by listening back; test images labelled SAMPLE | Live (MMS-TTS on our CPU) |
| Muse Glimmer, Muse Spark Open (open weights) | Checked every alert on our own GPUs, 1–4 Nov; most of our recorded results | Before 4 Nov |
| Llama Guard 4, Prompt Guard 2 | Rule screen; firewall for text in the scene | Before 4 Nov; not running now |

One line under it: "Each model is checked by another part of the system."

**Speaker notes (40 s)**

"We use Meta's models as a team that checks itself. Spark plans and reasons. SAM 3.1 sees and
measures. Until 4 Nov we ran Glimmer and Spark Open ourselves, as open weights, to check every
alert; now Muse Spark 1.3 and Muse Wonder do it on the Meta API. Four things surprised us, in
those earlier runs. Glimmer could tell the forklift operator from a pedestrian, which our
rule engine couldn't. No single model counts a busy queue right, but SAM, Spark and Glimmer
together got within two people. Listening back caught a Hindi announcement that said 'crowd'
instead of 'gate'. And Z3 found a divide-by-zero in a check Muse Code wrote; the repair round
fixed it."

> Nothing to warm: since 4 Nov the live system has no GPUs of our own
> ([`docs/deploy.md`](../deploy.md), "Meta API only").

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
- **You:** open `https://rulecam-live.vercel.app/demo?demo=1` and press **Verify it in your browser**.

**Speaker notes (20 s)**

"We're asking for three pilots, one in each world, on cameras they already own. From Meta,
help putting glasses mode on real glasses. And from you: open the demo and press verify.
Your own browser will check our evidence. Plain words in. Verified alerts out. Thank you."

---

## Questions we expect, and honest answers

| Question | Answer |
|---|---|
| How is this different from Verkada or Spot AI? | They let you describe an alert. We tell you what the camera can't check, attack the rule before it goes live, check every alert with a second model, and keep a record anyone can verify. On cameras you already own. |
| How accurate is it? | Live on 4 Nov, before we moved to the Meta API: Glimmer on our own GPU confirmed 6 of 6 forklift near misses; crowd counts were within 0–4% of the Jülich truth; a busy-frame count came to 10 against about 12 by hand. On the Meta API, the model proofs pass and the forklift rule passed Rule CI 16/16 and 6/6. Weak: Ask's counts on hand-labelled CCTV were 7 of 36 within tolerance (2 Nov). We publish all of it. |
| Did Glimmer check the crowd alert? | In the recorded runs (crowd is not live now), when it had a GPU, yes: 0.95 in 6.2 s in run 6. When no GPU was free, alerts went out unchecked and said so; a late check never holds a warning back. Once Glimmer held a true alert (it would not confirm more than 5 per m² from one image); now Spark decides to send or hold when Glimmer disagrees, unit-tested and not yet seen live. |
| Isn't this surveillance? | No face recognition, ever. Faces are blurred in evidence by default. Results are per rule and per store, never per worker. Workers see every rule and can dispute any alert. Our hackathon deployment runs a less strict prototype mode, and we document exactly what it changes. |
| Does Meta train on your footage? | Our Meta API key is Standard tier, never Contributor. Since 4 Nov every model call, the alert checks included, goes to the Meta API; before that, Glimmer and Spark Open ran on open weights on our own GPUs. All our measured runs used public footage or generated images labelled SAMPLE. |
| What does it cost to run? | Since 4 Nov we pay the Meta API per call, and we have not yet measured the cost per check there. Before, on our own GPUs: $0.0039 of GPU time per alert check with 20 at once (2 Nov), and Spark Open cost $43.12 an hour warm. Cost per camera-hour is not measured yet. |
| What if the model is wrong? | Outside prototype mode, a rejected alert on a high-severity rule still goes to a person. A disputed alert becomes a permanent Rule CI test. |
| Is it a safety system? | No. It helps people watch. It does not replace a guard, a lock-out or a supervisor. |
| Has it run on real CCTV or real glasses? | Not yet. Connectors were tested on streaming servers and a simulated ONVIF recorder; glasses mode on a phone page with stock stills; push in Chromium. Each report says so. |
