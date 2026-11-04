# Proof portal on real chains: 4 Nov 2026

Branch `p1/compliance`. Every number here comes from the real evidence chains of the live API pod.
The chains are `/workspace/data/evidence/chain/demo/2026-10-31..03.jsonl`, copied read-only over
SSH; the plan files are copied the same way. The recorded `warehouse_driver` golden chain of 1 Nov
was used too. Muse Spark 1.3 ran live (`RULECAM_MODE=record`), so its replies are now in the replay
cache. The simulated chain in `tests/compliance/conftest.py` is used only by the tests and is
labelled as simulated there.

## What was run

| Pack | Period (UTC) | Records disclosed | Numbers | Rules in force | Build time | Crew language |
|---|---|---|---|---|---|---|
| `pp-c4e31dd6f65b` | 1 Nov (vs 31 Oct) | 395 (all of 31 Oct and 1 Nov) | 97 | 12 | 89.7 s | Polish |
| `pp-1a2ad1c283d9` | 2–3 Nov (vs 31 Oct–1 Nov) | 563 (all four days) | 145 | 17 | 83.6 s | Hindi |
| `pp-ee3e17b1e93a` | 1 Nov, built through the API and UI | 395 | 97 | 12 | under 2 min | Polish |
| golden `warehouse_driver` | 1 Nov | 35 | 41 | 1 | 0.06 s, no models | none |

Almost all of the build time is Muse Spark. That is one web-searched clause per grounded rule
(4 to 6 per pack) plus four audience summaries. The numbers alone build in under a second
(`models: false`).

## What the packs show (1 Nov, the golden day)

- **Alerts:**
  - 59 alerts were raised (previous day: 59);
  - Muse Glimmer confirmed 22, rejected 33 and was unsure on 4 (previous day: 0, 2 and 57);
  - 4 went to a person to review, and the policy passed 11 on as alerts.
- **Response:**
  - 31 actions, all 31 dry-run: logged, nobody actually notified;
  - median time from alert to Glimmer's verdict 25 s (previous day 2 s);
  - median time from alert to first action 21 s, 90th percentile 149 s;
  - 0 acknowledgements, so there is no acknowledgement time.
- **Governance:**
  - 38 plan approvals on the day. This is a test site whose plans were replaced all day.
  - No two-key protected-setting activity. 0 changes without both keys, and the crew
    representative certificate shows it.
- **Disputes:** 0 filed.
- **Coverage:** no `coverage` records are chained on any day. Every pack says so, and treats BLIND
  time as unknown, not zero.
- **Rules in force:** 12, from the chained approvals. Each one shows the gate chained at approval
  (2 `pass`, 10 `amber` with reasons such as "detect_lb 0.62 < 0.8 on phone-52" or "ci_green
  failed: no boundary-trace generator for count") and the Rule CI detail from the plan file.
  - One rule in force reads "Tell me the name of every worker who walks past the forklift"
    (`worker-near-forklift-anonymized`, amber). The pack lists it as chained. A crew
    representative would want to see this.
- **Golden `warehouse_driver` chain** (the stock driver-only clip, a negative):
  - 6 alerts; Glimmer rejected all 6;
  - 3 went to a person and 3 were passed on act-first, all dry-run;
  - median time from alert to first action 1.2 s, to Glimmer's verdict 23 s;
  - the rule's gate is `pass`, with Rule CI at boundary 16/16 and mutations 6/6.
- **2–3 Nov:**
  - 27 alerts (previous two days: 118);
  - Glimmer confirmed 2 and rejected 22;
  - 23 actions, 14 of them dry-run;
  - median time to first action 6 s, 90th percentile 12 s;
  - 2 incidents.

## In-browser verification (the demo moment)

The 1 Nov pack was opened from its share link in the built-in browser, at the UI's desktop width
and at 375 px.

- **All checks passed:**
  - pack hash equals the link's hash (1/1);
  - 395/395 records rehashed and their Merkle proofs folded to the day roots;
  - 2/2 day windows complete, with both edges;
  - 97/97 numbers recounted, for this period and the previous one;
  - 22/22 summary sentences re-filled from the recomputed numbers;
  - 12/12 rule gates match their approval records;
  - 97/97 recipes have no per-person filter.
- **Speed:** the checks took **0.3–0.4 s** in the browser.
- **Live log:** both days' roots matched the site's live log exactly
  (`GET /evidence/{day}/hashes`). No root is committed to git for these days
  (`evidence/roots/` is empty), and the page says so.
- **Tamper test:** one number in the pack stored on the server was edited (Glimmer rejections 33 →
  23), simulating a compromised or careless server. The recipient page turned red: "the pack's
  contents do not match its hash" and "Rejected by Muse Glimmer: recomputed 33, pack says 23".
  The file was then restored.
- **Cross-language tests:** `engine.test.ts` checks the browser against packs the Python builder
  wrote. It catches an edited record, a changed number with a recomputed hash, a withheld record,
  a false summary sentence and a per-person filter. 12 UI tests and 24 Python tests pass.

## Muse Spark: summaries

- **Run:** 4 audiences × 3 packs, 0 sentences dropped. Spark wrote no digits; every number is a
  filled placeholder.
- **Crew language:** the Polish and Hindi crew-representative summaries read naturally. The
  earlier Polish run left "evidence for" in English; the prompt now asks for it translated.
- **Example** (insurer, 2–3 Nov): "Response timing shows median time to first action at 6 s versus
  21 s before, with the upper tail at 12 s versus 2 min".

## Muse Spark: clause grounding (honest result: mostly unconfirmed)

Every run asked Spark, with web search, for the clause each rule gives evidence for, and then
fetched the cited page to look for the quote.

| Run | Clauses Spark named | Quote confirmed |
|---|---|---|
| first question (the planner's "governing standard") | hair cover → FDA Food Code §2-402.11; forklift, tracking → none | 0 (the FDA page is a PDF) |
| "which clause do this rule's alerts give evidence about" | forklift 2 m → UK Workplace Regulations 1992 reg. 17(1); forklift 4 m → 29 CFR 1910.176(a); hard hat → 29 CFR 1910.135(a)(1); hair cover → FDA §2-402.11 | 0: osha.gov answers our fetch with HTTP 403, legislation.gov.uk timed out, FDA is a PDF |
| same question through `grounded` (search results kept) | hard hat → 1910.135(a)(1); hair cover → §2-402.11; forklift → none this time | 0: the search results carried empty snippets, and Spark cited osha.gov pages its search had not returned |

What this means:

- Spark names plausible clauses. The pack prints them as "quote not confirmed", with the reason,
  instead of as fact.
- The answers vary from run to run. They are cached per rule text, so one site's packs stay
  consistent.
- The latest build also fetches up to three other pages the search returned and accepts the quote
  only if it appears verbatim on one of them. That run's outcome is in the addendum below.
- We did not disguise our fetcher to get past osha.gov's 403.

## Not done or not measured

- **Coverage and BLIND hours:** the code and the browser recount are tested on the simulated
  chain only. The live runtime never chains `coverage` records.
- **Acknowledgement times:** the live chains hold no acks (actions ran dry-run).
- **Time-to-clear for spills:** tested on the simulated chain only. No supermarket chain has a
  `hazard_cleared` record yet.
- **Refresher training:** the `refresher_training_done` label from the warehouse duty tracker is
  counted per forklift rule. It is tested on the simulated chain only; the warehouse branch is not
  merged here.
- **Stills:** stills are inlined only from the runtime's strips folder. The local run had no
  strips, so no stills appear.
- **Not deployed:** nothing was deployed to the pod. The pod's `/proof` routes appear only after a
  merge and a deploy.
