# Site Advisor: business advice you can check

*Rulecam feature design, Oct 30 2026. It extends the master plan's "Proof, not promises" theme from safety rules to business advice. This doc is the source of truth for the feature; the master plan summarises it in §3.7. Research behind it, with full citations:*
- *`research_notes/Rulecam features/business_market.md`: the market and owner pains;*
- *`business_engine.md`: methods, schemas, SQL;*
- *`business_playbooks.md`: per-vertical metrics, benchmarks, worked examples.*

*Naming: we use "Site Advisor", not "Copilot", which is a Microsoft brand.*

---

## 1. The idea in plain words

The cameras that keep a crew safe already see how the work flows: when trucks wait, when the pass window backs up, when the excavator stands idle, when the exits go uncovered at shift change. Site Advisor turns that recorded activity into **advice the owner can act on today, and check tomorrow**.

Every piece of advice answers six questions:

| | Question | Example (warehouse, illustrative) |
|---|---|---|
| **Saw** | What did the data show? | Dock 3 turnaround is 52 min against 31 at dock 1. After 13:00, trucks wait a median of 14 min for a forklift |
| **Why** | What is causing it, and what did we rule out? | The 13:00 replenishment run takes both forklifts. *Ruled out:* trucks arriving off-appointment (the appointment upload shows they're on time) |
| **Do** | What should change, cheapest first? | Move replenishment before noon and reserve one forklift for the docks after 13:00 |
| **Gain** | How much better, as a range, in your own numbers? | 18–25 truck-minutes a day; with your detention rate, $X–Y per week |
| **Check** | How will we know it worked? | An alternating on/off trial over 6 blocks, with the method locked before the first block. The verdict comes back with an interval |
| **Safety** | Does it make anything more dangerous? | Passes. And "considered and **not** recommended: a second forklift on the dock floor, which raises exposure under rule *ped-forklift-2m*" |

**Three promises nobody else makes** (from the market scan):
1. **Every number is clickable.** It opens the query that produced it, and an independent model has recomputed it.
2. **Every recommendation states its expected gain before the owner acts.**
3. **Every accepted recommendation becomes a test that reports back.** The verdict is one of: *worked, no effect, worse,* or *not enough data yet*.

---

## 2. Why this matters (market and owner pains)

**Owners are drowning in data and short of people.**

- **Restaurants:** staffing is the top challenge (32.9%) and 64% run below full staffing (Restaurant365, mid-2026). 42% weren't profitable in 2025 (NRA 2026). Labour is a median 36.5% of sales at full-service restaurants (NRA 2025).
- **Construction:** 92% struggle to hire (AGC/NCCER 2025). 40–50% of equipment is under-used and 75% of firms track it by hand (Teletrac Navman 2026; secondary).
- **Warehouses:** drivers are detained at 39% of stops, costing $3.6B directly (ATRI). Receiving docks are now the most congested area (38%, up from 20%), and 51% still collect productivity data by hand (Peerless 2025).
- **Nursing homes:** 90% find recruiting hard (AHCA, Jan 2026), nursing-assistant turnover is 42.3% (LeadingAge 2025), and agency aides cost $36.03/hr against $20.22 for employees (ASPE).

**What exists, and where it falls short:**

- **Asking questions of your data is now commoditised**, often free: Toast IQ, Square, Shopify Sidekick, Intuit, Restaurant365 AI, and RetailNext Pulse AI included at no charge.
- **Proactive "next best action" suggestions are the 2026 trend:** Managerbot, Toast "For You", Fourth iQ, Crunchtime.
- **Camera vendors already write advice:** Voxel Risk Insights and Flow, and Spot AI's Operations Assistant.
- **But accuracy is disclaimed, not proven.** Toast says "AI tools can make mistakes. Always check your important information."
- **People don't act on dashboards.** Only about 29% of employees use BI tools, only 18% say they act on insights well, and only 25% of AI initiatives delivered the expected ROI.
- **Generic advice can hurt.** In a field trial, a GPT-4 business mentor made low-performing entrepreneurs 8–10% *worse*.

**The gap.** We found **no advisor that states a recommendation's expected gain before the owner acts, or tests afterwards whether its own recommendation worked.**

*One check is still open before we claim this on camera:* whether Voxel Flow's "impact of operational improvements" view can tag a specific change.

---

## 3. What the owner gets

| Feature | What it is | Tier |
|---|---|---|
| **Advice cards** | The six-part card above. Every number is bound to a query, every explanation is tested against the data, the reasoning is audited, and the safety check is shown | Must |
| **"Did it work?" checks** | Accepting a card creates a measurement plan whose method is locked (hashed onto the evidence chain) *before* the change. The verdict comes back with an interval | Must |
| **Station, not person** | Advice is about stations, equipment, zones and flows only. Per-person questions get a declined card. Cells that could point to one person are merged or hidden | Must |
| **Ask the site, honestly** | Ask by voice in your language. Each question gets an answer from three possibilities: *can measure*, *can't see* (with watched time), or *declined*. Then an advice card | Should |
| **Gain in your own numbers** | You enter your labour, detention or average-sale rates. Queue what-ifs use measured arrival rates, and the card shows *capacity* vs *cash* honestly. Staffing advice is flagged where predictive-scheduling laws apply | Should |
| **Morning brief** | Three cards plus "checks due", as a ≤ 60-second **voice note in the owner's language** (MMS-TTS), on WhatsApp or the LAN app | Should |
| **One map for safety and flow** | Near-miss hotspots overlaid on congestion, because they are usually the same place | Stretch |

---

## 4. How it works

It is a fixed-step pipeline, the same pattern as the Site Planner: code sets the order, and each model step fills a schema. Statistics are code, never a model.

1. **Ask.** A voice or text question, or the nightly scheduled brief. Spark 1.3 maps it to metrics, scope and a time window.
2. **Data window guard.** If the window wasn't recorded, it says so and lists what was. *No more "why was the dock slow Tuesday" answered from seeded data.*
3. **Process log.** Track events become an object-centric process log in DuckDB, OCEL 2.0-shaped. Trucks, pallets, plates and forklifts are objects; people are short-lived anonymous tracks that are never exported. Broken tracks are stitched.
4. **Quality gates.** Watched fraction (from the coverage watchdog), a Little's-law cross-check between occupancy and the case builder, the fragment rate, and incomplete cases. Failed gates downgrade or hide a finding.
5. **Measure.** Numbers come only from a **vetted metric catalogue** (no free-form SQL). Each carries a range, *n*, a source label and coverage.
6. **Detect.** Bottlenecks through three lenses (utilisation, waiting time, and Roser's active-period method), against an hour-of-day baseline with multiple-testing control.
7. **Explain.** A fixed hypothesis catalogue plus model-proposed causes, each turned into a data test. A mix/rate decomposition says how much of the gap each cause explains. Built-in checks for Simpson's paradox, regression to the mean and day-of-week effects.
8. **Challenge.** **Spark Open plays devil's advocate** in one round, including "it's just noise", with its reasoning shown. Its proposals are tested against the data, not trusted.
9. **What-if.** A ladder of models: Little's law, then Erlang C, then Allen–Cunneen, then simulation as a stretch (validated on a held-out session). Code picks the rung from measured assumption checks, and the **assumption card** shows pass/fail.
10. **Recommend.** In Theory of Constraints order (exploit, then subordinate, then elevate), from a closed vocabulary of changes: reschedule arrivals, add capacity, move location, resequence, change queue layout, add buffer.
11. **Safety veto (fails closed).** Exposure under every active safety rule is estimated by **backtesting the rule's precondition on stored tracks**. Any credible rise vetoes the recommendation unless a mitigation restores baseline. Vetoes are shown as *"considered and not recommended"*.
12. **Rank.** By a pessimistic (20th-percentile) weekly value, divided by effort, weighted by how easily the change can be reversed. Each item is labelled **Do**, **Test** or **Hold**.
13. **Ground and audit.**
    - Generated text contains **slots, never digits**, and code fills the slots.
    - **Glimmer recomputes every number with its own SQL.**
    - A claim-strength linter caps the wording at the evidence grade ("coincided with" vs "caused").
    - **Spark Open audits the reasoning.**
14. **Present.** The card shows clickable numbers with source badges and Muse Image charts, which Glimmer reads back to check that the chart matches the data, plus an MMS-TTS read-out.
15. **Accept → pre-register.** A measurement plan (primary metric, guard metrics such as near-misses, design, minimum sample, decision rule) is hashed onto the evidence chain **before** the change starts.
16. **Read out.** Interim results are labelled interim. The final verdict is written back to the card and the brief.

### The recommendation object (condensed)

```yaml
recommendation:
  id: rec-0007
  status: proposed            # proposed | accepted | measuring | adopted | reverted | rejected
  scope: { kind: station, ids: [dock_3] }          # never a person
  class: test                  # do | test | hold
  provenance: [recorded]       # recorded | upload | public | simulated — never mixed
  saw:
    claims: [{ template: "Dock 3 turnaround {m1} vs dock 1 {m2}", slots: { m1: mr_8812, m2: mr_8813 }, grade: B }]
    window: "2026-10-31T13:00/17:00 (staged sessions S2–S4)"
    n_cases: 41
    coverage: 0.94
    clips: [clip_221, clip_224]
  why:
    explanations:
      - { id: h1, statement: "13:00 replenishment occupies both forklifts", status: supported, share_of_gap: 0.6 }
      - { id: h2, statement: "trucks arrive off-appointment", status: ruled_out }
    pitfalls: { simpson: checked, regression_to_mean: checked, day_of_week: n/a }
    devil_advocate: { proposals: 3, tested: 3, top_changed: false, reasoning_ref: so_442 }
  do:
    changes: [{ op: resequence, params: { task: replenishment, to: "before 12:00" } }]
    who: { role: dock_lead }
    revert: "move replenishment back to 13:00"
  expect:
    metric: dock3_wait_for_forklift_min
    gain: { interval: [-14, -4], unit: min/truck, level: 0.8 }
    model: { rung: erlang_c, validated: held_out_S5 }
    assumptions: [poisson_arrivals: pass, stable_service: pass]
    money: { needs: owner_rate.detention_usd_per_hr }
  safety:
    rules_checked: [ped-forklift-2m, aisle-blocked]
    exposure: { ped-forklift-2m: { delta: -0.1, verdict: pass } }
    considered_not_recommended: [{ change: "add a second forklift at docks", reason: "raises ped-forklift-2m exposure" }]
  rank: { position: 1, value_p20_week: "…", effort_h: 0.5, reversibility: two_way }
  check: { measurement_plan: mp-0007 }
  audit: { numbers: { total: 14, reproduced_by_glimmer: 14 }, claim_linter: pass, spark_open_audit: pass, hash: "…", prev_hash: "…" }
```

### The measurement plan ("did it work?")

- **Design:** a **switchback by default**, alternating the change on and off by block. Each block starts and ends with an empty queue, to limit carryover between blocks.
- **Statistics:** an exact sign-flip test with an inverted confidence interval.
- **Honest limits:** with *k* pairs of blocks the smallest possible p-value is 2/2ᵏ. Three pairs can never reach significance; six pairs can. The plan states the minimum detectable effect before starting.
- **Fallbacks:** interrupted time series or difference-in-differences when switching isn't possible. These are confounded by day-level effects, so the strongest allowed wording is "coincided with".
- **Verdict labels:** *worked*, *no effect*, *worse*, *not enough data yet*. Guard metrics (near-misses, handwash compliance) are reported alongside.

---

## 5. Station, not person (the guardrail, in code)

- Track IDs are **banned as a grouping key** in the metric catalogue, and this is enforced in code.
- No number is shown unless it covers **≥ 5 tracks and ≥ 3 shifts or services**. Station-window cells with fewer than 3 tracks are merged upward.
- **Night crews:** where one or two aides are on shift, results are **unit-level only, over ≥ 7 nights**, and only system fixes (layout, timing, roles) are suggested.
- Shift-schedule uploads containing **names are rejected**; roles only.
- **Breaks are never counted as lost time.** Advice never proposes cutting breaks (consistent with CA AB 1331 from 2027).
- Per-person questions ("who is slowest?") get a **declined card** with a station alternative. Target: 20/20 on the decline test.
- Context: France fined Amazon €32M over per-second idle tracking, and several US states have warehouse quota laws. Rulecam's stance is the opposite.

---

## 6. Playbooks: what it watches in each setting

*The examples are illustrative. Real cards are generated from recorded data. Benchmarks are cited in `business_playbooks.md`.*

| Setting | Top metrics (station-level) | One example card (illustrative) | Safety–flow link |
|---|---|---|---|
| **Kitchen** | Plate wait on the pass; ticket time (POS + pass, via Little's law); station crowding against covers; handwash compliance against load; minutes the handsink was blocked; dish-pit backlog; 4-hour cleaning cadence | **Saw:** Friday 19:00–20:30 pass wait 3.6 min vs 1.1, pickup unattended 44%, no runner role. **Do:** the host or busser runs when the waitlist is empty, plus a pass bell; only then trial a paid runner 18:45–20:45 (~$41). **Check:** alternate runner on/off over 4 Fridays; guard: handwash compliance | Rush hours cut handwash compliance, so a staffing fix is also a hygiene fix |
| **Construction** | Machine active share, with the reason for each stationary spell (a safety hold never counts as waste); truck wait at the gate; lay-down free space; material staging dwell and double handling; crew presence **by zone** | **Saw:** excavator active 46%, 2.9 h a day waiting on bunched trucks. **Do:** fixed dispatch intervals; dig to a spoil pile when no truck is there. **Gain:** about $58–78 a day of operator time (capacity), or cash if the job finishes a day early. **Safety:** spoil at least 2 ft from the edge (29 CFR 1926.651(j)(2)) | Steadier dispatch means fewer trucks queuing by the trench |
| **Nursing-home unit** (operations only) | Exit-door response by corridor (7-day, unit-level); how long exits go uncovered during handoff; night staff presence by zone against the unit's own policy; dining-room flow; supper-to-breakfast gap (federal maximum 14 h, or 16 h with a snack); share of nuisance exit alarms | **Saw:** 15:00 handoff takes 34 min against a scheduled 15, exits uncovered 11 min, 9 of 23 unaccompanied exit approaches in that hour. **Do:** a rotating "corridor holder" role during handoff, or split the handoff by wing. **Check:** 14 days, unit-level only | Coverage rises at a high-risk hour (one facility study found 27% of falls between 4 and 8 pm) |
| **Warehouse** | Dock turnaround split into waiting for a forklift, handling, and dwell after handling; forklift active share and how many run at once; aisle blocking; staging overflow; pick travel share; near-misses per forklift-hour | The dock 3 card in §1 | A 15-min rise in truck dwell is linked to a 6.2% higher crash rate (DOT OIG estimate); congestion at the dock is a near-miss hotspot |
| **Retail / counter** | Queue wait; people leaving the queue, by queue length; conversion (footfall against transactions); minutes with a long queue and an unstaffed register; how long shelf gaps last | **Saw:** queue of 5 or more for 38 min a day, 11% leave at length 6 or more, register 2 opens at 12:40. **Do:** page the back-office role when the queue is 4 or more for 2 min, and a standing 11:55 open | The queue must not cross the fire exit |

**Benchmarks used for dollar maths** (verified unless marked; details in the playbooks file):
- **Wages (BLS May 2025 medians):** cook $17.98, equipment operator $28.78, nursing assistant $20.67, forklift operator $22.32, cashier $15.81. Loaded multiplier ×1.24–×1.46 (BLS ECEC).
- **Restaurants:** labour 36.5% (full-service) and 31.7% (limited-service) of sales (NRA 2025).
- **Trucking:** detention about $30–$75 an hour after 2 free hours (secondary), and fewer than half of detention invoices get paid.
- **Construction:** Komatsu's fleet is 38% idle on average, with managed fleets aiming for about 20%. A mini excavator rents for $310–$450 a day.
- **Nursing homes:** agency aides $36.03/hr vs $20.22 for employees (ASPE). CMS fines in the immediate-jeopardy range are $8,351–$27,378 a day.

**Money rule:** dollars appear only when the owner has entered their own rates. Otherwise the card says *capacity* (time freed) and labels it as such. Never show "saves X%" unmeasured.

---

## 7. The 60-second morning brief

At most 150 spoken words, in the owner's language (MMS-TTS voice note plus three cards), in this order:

1. **How much it actually watched** ("cam 1 was blind 12 min").
2. **Yesterday's load:** covers, trucks or residents.
3. **Three numbers** against the same weekday.
4. **The biggest leak,** as a dollar range or "capacity".
5. **One spot where safety and flow problems overlap.**
6. **Progress on running checks** ("runner trial: 3 of 6 pairs done, too early to tell").
7. **One decision to approve.**
8. **What it couldn't see.**

When nothing passes the evidence bar, it says *"nothing worth your time today"*. Every number has been recomputed by Glimmer. UC3 briefs show counts and 7-day figures only, never single-event response times.

---

## 8. Meta models in Site Advisor

| Model | Job |
|---|---|
| **Muse Spark 1.3** | Question → metrics and window; hypothesis proposals; recommendation wording (slots only); station map draft |
| **Muse Spark Open** (open weights) | **Devil's advocate**, and an audit of the reasoning, with `reasoning_content` shown |
| **Muse Glimmer** (open weights, local) | **Independent recompute of every number** with its own SQL; reads Muse Image charts back to check them; keeps analytics on site |
| **Muse Image** | Charts and the one-page brief (code-rendered) |
| **Muse Voice Transcribe** | "Ask the site" by voice, in 25 languages |
| **MMS-TTS** (open) | Morning brief as a voice note in the owner's language |
| **SAM 3.1 + Sapiens** | The tracks and the anonymous evidence clips behind every number |
| **V-JEPA 2** (stretch) | Surprise events offered as candidate causes ("the 14:10 slowdown follows the stack collapse") |

Code, not models, does all the statistics: queueing, switchbacks, sign-flip tests, intervals.

---

## 9. Honest-data plan

The red team's warning was that there will be no real week of history. The answer is to **record real sessions and label everything.**

- **Source labels on every number:** *recorded*, *upload*, *public*, or *simulated*. No aggregate mixes them.
- **Recorded sessions on the warehouse set, Fri Oct 30 – Sun Nov 1, about 4–6 hours in 20–30-minute sessions.**
  - These overlap with the UC4 recordings already needed for the backtest and for Surprise Lens.
  - Truck arrivals follow a **seeded random script**. Ideally friends who haven't seen the analysis operate the set, so findings can't be choreographed.
  - Session minutes are "set minutes" and are labelled as such. No real-world dollar claims come from the toy set.
- **Sealed-cause test (the credibility device):**
  - Before two of the sessions, a teammate writes the hidden cause (for example "replenishment at 13:00 takes both forklifts") and **hashes it onto the evidence chain**.
  - Site Advisor analyses the sessions blind, and then the cause is revealed.
  - There is also one **null session** with no planted cause. A good advisor finds nothing there.
- **One live switchback** of the accepted recommendation, across 6 block pairs, produces the verdict shown in the video.
- **Public data validates the method, never the business:**
  - the OCEL 2.0 Container Logistics log (simulated, labelled) for scale;
  - Melbourne hourly pedestrian counts for placebo calibration of the before/after statistics, *once its licence is confirmed*;
  - optionally, the Technion call-centre data to show where Erlang C holds.
- **Owner uploads** (POS, schedule by role, delivery appointments) are created for our set and labelled. Money appears only with entered rates, marked "example rates".
- **Never shown:** a business savings figure from staged data, "learned your history", or "caused" from before/after data.

---

## 10. Evaluation (E11 in the master plan)

| Check | Method | Shown as |
|---|---|---|
| Does it find real causes? | Two sealed-cause sessions plus one null session | Found / missed / false finding on the null session |
| Are the numbers right? | Glimmer recompute on every card. **Corrupted-card mutations** (swapped slots, wrong window, off-by-one hour) | "14/14 reproduced"; "mutations caught 19/20" |
| Is the what-if honest? | Predictions validated on a held-out session | Predicted against observed, with intervals |
| Did the advice work? | Live switchback, sign-flip test | Effect with CI; "smallest possible p with 6 pairs: 0.031" |
| Does it refuse per-person analysis? | 20 per-person requests | Declined 20/20 |
| Does the safety veto fire? | Recommendations that would raise exposure under active rules | Vetoed n/n; shown as "considered and not recommended" |

---

## 11. The demo

**In the 3-minute video (about 14 s, on the warehouse set, after UC4):**
1. The owner asks by voice: *"Why is dock 3 slow after lunch?"*
2. The advice card appears: Saw / Why (with "ruled out: off-appointment arrivals") / Do / Gain range.
3. **"Considered and not recommended: a second forklift, which raises exposure under ped-forklift-2m."** Spark Open's audit and "14/14 numbers reproduced by Glimmer" sit in the corner.
4. A cut to the check: *"Pre-registered at event 1,204"*. Then the verdict: **"Worked: −9 min per truck (CI −14 to −4), 6 pairs, staged sessions."**
5. Then **"Sealed cause revealed: match ✓"**.

*The figures here are placeholders; only measured numbers go in the video.*

**Finals (about 60 s, if asked):**
- tap any number to show its SQL;
- the assumption card for the what-if;
- the devil's-advocate panel;
- a per-person question declined;
- the Spanish voice-note morning brief.

---

## 12. Build plan and owners

Total budget: a **1-day core (must)** of about 14 hours across four people, plus a **should** layer if the Oct 31 cut-line check is green.

| Component | Owner | Hours | Tier |
|---|---|---|---|
| Dock station events from tracks; process log (OCEL-shaped); coverage and Little's-law gates | P1 | 3 | Must |
| Metric catalogue (8 dock metrics, ranges, source labels); Little's law + Erlang C / Allen–Cunneen with assumption card; sign-flip switchback stats | P4 | 4 | Must |
| Advice pipeline: question → metrics; 5 catalogue hypotheses + mix/rate; Spark Open devil's advocate; slot-only text; claim linter; per-person guard and decline cards; closed change vocabulary; safety veto via precondition backtest; ranking | P2 | 5 | Must |
| Advice card UI (clickable numbers → SQL, source badges, "considered and not recommended", verdict); Glimmer recompute service; pre-registration hash on the chain | P3 | 3 | Must |
| Recording sessions with the seeded script, sealed causes and null session | All (rotating, alongside building) | ~5 elapsed | Must |
| Ask by voice; owner rates for money; morning brief (Muse Image + MMS-TTS voice note, on WhatsApp or LAN) | P3/P2 | 6 | Should |
| Kitchen pass-window card (second setting) | P2 | 3 | Should |
| Corrupted-card mutation suite; held-out what-if validation | P4 | 3 | Should |
| Simulation (SimPy) rung; Shapley attribution; OCEL export; safety–flow map; public-data placebo; retail pack | — | — | Stretch |

**What it displaces in master-plan scope.** These move from *should* to *stretch* to make room:
- Video Seal;
- the re-identification attack test;
- sound rules (they come back only if Surprise Lens fails its gate and P4 is free).

**Never cut, even at 1 day:**
- source labels;
- the data-window guard;
- Glimmer recompute;
- the safety veto;
- pre-registration;
- the per-person guard.

---

## 13. Claims

**Can say:**
- "Every number is clickable and independently recomputed."
- "Every recommendation states its expected gain before you act."
- "Accepted advice becomes a pre-registered check that reports back. We found no other advisor that tests its own recommendations."
- "Never recommends anything that raises risk under your safety rules."
- "Stations, not people."
- "In our sealed-cause test, it found the hidden cause in N of 2 sessions and stayed quiet on the null session." (Only with the result.)

**Never say:**
- "First AI operations advisor" (Voxel, Spot AI, Toast IQ, Managerbot and others exist);
- "only one that refuses per-person metrics" (Voxel and Density also take this stance);
- "nobody measures outcomes", stated absolutely;
- any unmeasured savings figure;
- "proves the change caused the gain";
- "learns your business";
- "hallucination-free" (numbers match their queries, but the choice of metric can still be wrong);
- "anonymous";
- "compliant with scheduling or quota laws";
- "replaces Toast or 7shifts";
- "Copilot".

---

## 14. Risks

| Risk | Mitigation |
|---|---|
| Too little recorded data for a conclusion | Plan the sessions now; the switchback design states its minimum detectable effect; "not enough data yet" is a valid, shown verdict |
| A plausible but wrong explanation | Data tests decide; devil's advocate; sealed-cause test measures this directly |
| Toy-set minutes look silly as business numbers | Label them "set minutes"; no dollars without entered rates; frame the demo as proving the *method* |
| Related models (Glimmer is distilled from Spark) | Credibility rests on SQL executed twice and on data tests, not on models agreeing |
| Advice drifts toward per-person or punitive | Enforced in code: no track-ID grouping, k-thresholds, decline cards, no break-cutting |
| Scope | 14-hour core; the shoulds start only on a green Oct 31; three master-plan shoulds demoted to make room |
