# Supermarket, live: from a question to a fixed problem (4 Nov 2026)

What was tested: the whole supermarket story on real Meta models, end to end, with the numbers
as they came out, including what went wrong. The raw replies are in
`2026-11-04-supermarket-live.json` (`e2e` is the final end-to-end run; `round1` and `round2` are
the earlier runs on the same day).

**Where it ran.** The API from branch `p1/uc-supermarket` ran on a laptop in live mode
(`RULECAM_MODE=live`) against the Meta Model API (Muse Spark 1.3 and 1.2, hosted SAM 3.1, Muse
Voice Transcribe, Muse Image) and the team's RunPod serverless SAM 3.1 and Muse Glimmer
endpoints. It was not the deployed pod (the integration agent deploys); nothing on the pod was
changed.

**Footage and photos.**

| Input | What it is | Real or SAMPLE |
|---|---|---|
| "checkout lanes" camera | Pexels 39221979, a busy New Zealand checkout, 35 s, looped as a paired phone camera | real stock video |
| "front-end tills" camera | Pexels 4249560, a row of staffed tills | real stock video |
| "checkout queue" camera | Pexels 39221981, a self-checkout queue | real stock video |
| "aisle 3" camera | Pexels 39238831, a walk down supermarket aisles | real stock video |
| Pasta-aisle shelf photo | Wikimedia Commons, "Dried pasta shelves empty in an Australian supermarket" (CC BY-SA 4.0) | real photo |
| Second shelf photo | Wikimedia Commons, "2020-03-15 Empty supermarket shelves in Australian supermarket 01" (CC BY-SA 4.0) | real photo |
| Spill photo | a real frame of the aisle video, with a milk spill added by **Muse Image** | **SAMPLE** (generated edit) |
| Restocked "after" photo | the pasta-aisle photo, restocked by **Muse Image** | **SAMPLE** (generated edit) |
| Spoken question | "¿Cuántos clientes esperaban en las cajas y cuántas cajas estaban abiertas?", spoken by macOS text-to-speech, 16 kHz WAV | synthetic voice |

The video clips loop, so anything about "per minute" or "how long" describes repeats of the
same people. No real spill footage was available; the spill is a generated edit and is labelled
SAMPLE wherever it appears.

## The end-to-end run (one pass, 13 calls, about 6 minutes of model time)

| # | Step | Wall time | What the models said | Right? |
|---|---|---|---|---|
| 1 | Ask: "When was the checkout queue longest, how many tills were open, … do we need another till?" | **60.4 s** | 4 SQL queries, 2 Glimmer looks (24.0 s, 18.9 s), 1 Spark video watch (15.0 s), Spark Open review. Peak "22 customers present in view" on the checkout-queue camera, 2 of 2 tills open, ~6 arrivals a minute, ~60-90 s per customer, "open a 3rd till"; it cited the queue check that had already alerted staff | partly: 22 is the tracker's count of `customer` boxes at one instant and is high (the queue check and the spoken answer read 8-11 people on the same clip); tills and the advice match the video |
| 1b | Receipt | 0.01 s | the run log row re-hashes to the receipt; 4 rows of the run log intact | yes |
| 2 | Advice card from that answer | **19.5 s** | "Open a third till to cut the checkout queue"; 4 numbers, **all 4 found in the Ask evidence**; switchback 6 pairs of 30-minute blocks; watch the checkout-queue camera every 120 s, role `duty-manager` | yes (wording of the "saw" lines was clumsy: "in words"; the prompt is fixed since) |
| 2b | What-if (code, not a model) | – | 6 arrivals/min × 60 s = 6 erlangs of work; with 2 or 3 tills the queue grows without bound, so the card says "even one more till would not keep up" | the arithmetic is right for those inputs; the inputs are rough visual estimates |
| 3 | Accept | **0.01 s** | card `measuring`; the switchback plan pre-registered on the evidence chain at record #16 before anything changed; watch on | yes |
| 4 | Watch run | **20.6 s** | SAM 3.1 2.7 s, Spark 16.3 s: "8 waiting, 2 of 2 tills open; about 300 s wait; open another till"; **re-alerted** because it was worse than the earlier reading of 6; announcement "A new till is opening, please come through. / Una nueva caja está abriendo, por favor pase." | yes, matches the frames |
| 5 | Shelf photo (pasta aisle) | **51.1 s** | Spark + SAM 3.1: 11 gaps, ~136 facings; Glimmer's veto look timed out at 25 s, so all 11 stood; restock task opened, alert sent with the marked photo | mostly: the boxes sit on the bare shelves; two boxes take in some product at their edges |
| 6 | Floor photo with the spill (SAMPLE) | **27.7 s** | Spark 10.5 s found the spill; SAM 3.1 found "puddle of milk" in the box (4.2 s); Glimmer agreed (12.5 s); hazard task opened, alert sent | yes |
| 6b | Floor photo, real aisle frame (no spill) | **7.1 s** | "No floor hazard seen" | yes (negative control) |
| 7a | Shelf task proof with the unchanged before photo | 41.7 s | Spark: "no restock visible in any numbered gap"; task stays open (Glimmer timed out) | yes (negative control) |
| 7b | Shelf task proof with the restocked photo (SAMPLE) | 41.2 s | Spark: all 11 gaps stocked; Glimmer timed out, so Spark's verdict closed it; **fixed in 2.0 min**, chain record #20 | yes |
| 7c | Hazard task proof with the real aisle frame | **24.9 s** | Spark (9.8 s) and Glimmer (15.1 s) both: spill gone; **cleared in 1.9 min**, chain record #21 | yes |
| 8 | Owner brief (English) | **11.1 s** | headline "Busy tills went unanswered, shelves and spill still open"; counts from the stored checks (18 queue, 6 hazard, 3 shelf checks; 9 alerts, 0 answered; 3 tasks open, 3 done) | yes; it said "fix time was not recorded" because the median time to fix was not counted yet (fixed after this run) |
| 9 | Spoken question in Spanish | **45.0 s** | Voice Transcribe 3.2 s, exact words, `es`; Ask 41.8 s with 3 Glimmer looks; answered in Spanish: ~9 waiting, 2 tills open on each view, and it noted the three cameras overlap so it did not add them up | yes |

Nobody tapped *On my way* during the run, so "0 answered" in the brief is true.

## Earlier runs the same day

| Check | Result | Time |
|---|---|---|
| Queue, checkout lanes (first check of the day) | 8 people found by SAM 3.1; Spark: 3 tills open, 5 waiting, ~3 min wait, no alert; the marked frame matches the video | 49.1 s (SAM 22.3 s run frame by frame, Spark 17.6 s); the three SAM calls now run at once |
| Queue, self-checkout queue | 9 waiting, 1 open area, "open another till", alert sent | 27.3 s |
| Queue, same camera again 2 min later (watch) | still 9 waiting; Spark saw staff were already alerted for the same queue and **did not alert again** | 26.7 s |
| Hazard, SAMPLE spill, Glimmer cold | SAM 3.1 confirmed, Glimmer did not answer in 60 s, alert sent on Spark + SAM | 79.1 s (the wait for Glimmer is now 25 s) |
| Hazard, SAMPLE spill, Glimmer warm | Spark, SAM 3.1 and Glimmer all agree | 42.2 s |
| Hazard, two real aisle frames | no hazard (both) | 8.6 s, 6.4 s |
| Shelf, second Commons photo | 8 gaps, ~86 facings | 31.9 s |
| Shelf proof, SAMPLE restocked photo, first try | **stayed open**: Spark saw 10 of 11 gaps filled and gap 5 (the lower wire shelf) still bare, which is true of the generated image | 70.6 s (Glimmer cold) |
| Ask over the stored checks: "Were any shelf gaps or floor hazards found today, and how fast were they fixed?" | answered from `retail_findings` and `retail_tasks` alone (1 confirmed spill, task open ~17 min, gaps open ~22 min) | 12.3 s |
| Spoken question, Glimmer cold | right words, right language, but 293 s: 4 Glimmer looks each waited out a 120 s timeout | fixed: after one failed look, Ask now skips looks for 5 minutes and tells Spark to use video or SAM |

## Latency summary (wall time per call, all live)

| Call | Typical | Range seen |
|---|---|---|
| Queue check (SAM 3.1 on 3 frames + Spark) | ~25 s | 20.6–56.6 s (hosted SAM varied from 2.7 s to 26 s) |
| Hazard check, hazard present | ~28 s with Glimmer warm | 27.7–79.1 s |
| Hazard check, clean floor | ~7 s | 6.1–8.6 s |
| Shelf check | ~30 s, +25 s when Glimmer is slow | 29.9–51.1 s |
| Task proof (Spark + Glimmer) | ~25 s | 21.5–75.7 s |
| Advice card | ~17 s | 15.4–19.5 s |
| Accept (pre-register + watch) | 0.01 s | |
| Brief | ~11 s | 10.8–11.1 s |
| Ask | 12 s (stored checks only) to 60 s (looks + video) | 12.3–147 s; 293 s before the Glimmer fix |
| Voice Transcribe | ~3 s | 2.99–3.15 s |

## What failed or is weak

- **Muse Glimmer's serverless endpoint was unreliable.** For the first 40 minutes every Glimmer
  call timed out (three 120 s probes in a row, captions too). Later it answered in 11-25 s,
  but under the parallel load some calls still timed out at 25 s (the shelf veto look and both
  shelf proofs in the final run). Every finding records which model answered and which timed
  out. Warm it (`scripts/ops/warm.sh on`) before the demo.
- **Counts from tracks are high on crowded views.** "22 customers in view" came from the
  tracker's `customer` boxes at one instant; the queue check's own reading of the same camera
  (8 waiting) is closer to the video.
- **The what-if depends on rough inputs.** Arrivals and service times came from a model watching
  a 20-60 s clip; the card shows them and says they were measured that way.
- **Hosted SAM 3.1 latency varies** (2.7 s to 26 s for the same three frames).
- **Shelf boxes are approximate.** Some gap boxes overlap product at their edges; SAM 3.1's
  product cover check and the Glimmer veto are there for that, but the veto needs Glimmer to
  answer.
- **Not done at the time of this run:** recording switchback blocks from the page (added after;
  one block recorded live at chain record #23); an incident PDF for a hazard; Chain Mode
  scorecard rows from these findings; a deli food-safety pack; "exit blocked for more than 5
  minutes" as a typed rule.

## Update: fixing the over-count (point-then-count)

**The problem.** In the end-to-end run Ask said "22 customers in view" on the checkout-queue
camera at 01:13:07 UTC. That number was the tracker's `customer` boxes at one keyframe; on this
busy view SAM put two boxes on some people. Counted by hand on the same frame: about 12 people
(about 13 on a second busy frame of the same clip on another camera id, 01:03:10, where the
tracker had 21).

**The fix.** A new Ask tool, `cross_count`, counts the same frame three ways at once, none
seeing the others: SAM 3.1 boxes, Muse Spark 1.3 pointing at each person on a 0-1000 grid, and
Muse Glimmer's own count. It adds the tracker's count at the nearest keyframe, says whether they
agree, and how SAM's boxes and Spark's points line up (boxes with no point inside, boxes with
several, points outside every box). Spark reconciles them and says why. The prompt's old claim
that "the count present at one ts is reliable" was wrong on busy views and is gone.

**Same frames, before and after (live, 4 Nov):**

| Frame | By hand | Tracker (Ask's answer before) | SAM 3.1 boxes | Spark points | Glimmer | Ask now |
|---|---|---|---|---|---|---|
| checkout queue, 01:13:07 | ~12 | 22 `customer` + 2 `cashier` | 15-17 | 9 | 10-11 | **10** ("SAM found 15 boxes but 9 had no person point, indicating duplicates") |
| checkout queue, 01:03:10 | ~13 | 21 `customer` + 5 `cashier` | 15-16 | 9-10 | 11-13 | **11** |

No single model was right: SAM over-counts busy views and Spark's points miss people half
hidden behind others; the three counts bracket the truth and the tracker's number falls outside
the bracket. Ask's error went from +10 and +8 to -2 and -2. Each counter took 1.2-1.8 s (SAM),
9-14 s (Spark) and 5-7 s (Glimmer), in parallel; the two Ask answers took 146 s and 56 s in all.

The first version still trusted the tracker: with the three counts in front of it Spark answered
22, because the prompt said a count at one instant is reliable. The line-up numbers and the
corrected prompt are what changed its answer.
