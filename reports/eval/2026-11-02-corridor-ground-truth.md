# Store camera accuracy against human labels: CAVIAR shopping-centre corridor (2 Nov 2026)

The supermarket tests so far used short stock clips looped by the test harness, and no one had labelled them, so their accuracy could only be eyeballed. This test uses **real CCTV with hand-made ground truth**, fed once with no loop, and scores both the system's own data and the agent's answers against the labels. Raw data: `2026-11-02-corridor-ground-truth.json`.

## The footage

No stock site has long fixed-camera supermarket footage (every Pexels checkout clip was 9–33 s). I checked these datasets:

| Dataset | Used? | Why |
|---|---|---|
| [CAVIAR](https://homepages.inf.ed.ac.uk/rbf/CAVIAR/) (University of Edinburgh, EC project IST 2001 37540) | **yes** | Real CCTV in a **shopping centre in Lisbon**: a fixed camera along a corridor of shop fronts. Every person's box and ID in every frame is hand-labelled, with activity labels (walking, window-shopping, browsing, shop enter, shop exit, meeting). Publicly available, CC BY-SA |
| [Sunmi in-store tracking](https://github.com/Sunmi-AI-Lab/head-detection-and-tracking) ([paper](https://ar5iv.labs.arxiv.org/html/2005.08009)) | no | The released videos were filmed in offices; the supermarket part is tracks and heatmaps, not video |
| [MMPTrack](https://paperswithcode.com/dataset/mmptrack) | no | A mock store; multi-camera |

It is a shopping-centre corridor, not a supermarket checkout, but it is the closest real, labelled store footage that is free to use.

**The stream:** 7 recordings from the **same corridor camera** (WalkByShop1, TwoEnterShop1, ShopAssistant2, ThreePastShop1, OneStopEnter2, OneShopOneWait1, EnterExitCrossingPaths1), chained into one **9 min 14 s** stream at 25 fps. The frame counts match the label files exactly. The original resolution is 384×288; I upscaled it 2× to 768×576. It was fed once, in real time, as a paired phone camera (`phone-68`), starting 09:44:12 UTC.

**Ground truth (from the labels):** 80 people (visits), 24 went into a shop, 13 window-shopped or browsed, 15 pairs walked together, time in view mean 25.1 s (median 16.7 s, longest 93.4 s), at most 8 people at once (first at 09:49:28), 3.6 people in view on average.

## What happened

1. **First run: no tracks.** The runtime only segments cameras an active plan's rules cover, and none covered `phone-68`. The agent fell back to captions and spot checks (results in the table below).
2. **Backfill.** `scripts/ops/backfill_tracks.py` runs the runtime's own image-mode tracker (SAM 3.1 keyframes on our GPU pod + ByteTrack) over a camera's recorded chunks. It tracked `person` on all 185 chunks (5,301 points) in 409 s, faster than real time. The identity job then embedded and linked the tracks with DINOv2.
3. **Second run.** Same 10 questions, now with tracks, identities and captions.

Infrastructure note: during the test the SAM GPU pod restarted on RunPod's side and hung at "awaiting container". One replacement host stalled on downloads (13 minutes to fetch the SAM 3 code from GitHub); a second host set everything up in 2 minutes. This is the case for moving the GPU services to RunPod Serverless (see the end).

## The system's own data against the labels

| Measure | System | Truth |
|---|---|---|
| People in view, per second (537 seconds compared) | exact 23% of seconds, within ±1 53%, mean error **+1.1** (over-counts) | — |
| Average in view (over the seconds compared) | 4.9 | 3.8 |
| Peak | 10 at +4:53 | 8 at +5:16 |
| At +1:00 / +3:20 / +5:16 / +8:20 | 6 / 3 / 9 / 2 | 5 / 1 / 8 / 4 |
| Person tracks → identities | 416 → 39 | 80 visits |

## The agent's answers against the labels

| # | Owner's question | Truth | Without tracks | With tracks + identities |
|---|---|---|---|---|
| C01 | How many people walked through, 9:44–9:54? | 80 visits | "can't give an exact total" | 39 people |
| C02 | How many went into a shop? | **24** | 0 | **0** |
| C03 | Average time in view, who stayed longest? | 25 s mean, longest 93 s | can't measure | 276 s, longest 563 s |
| C04 | When busiest, how many? | 8 at 09:49:28 | ~09:46:50 | **10 at 09:49:05–11** |
| C05 | Average people at once | 3.6 | ~5 | 4.8 |
| C06 | Stopped to look or browse | 13 | 0 | "~27 lingered" |
| C07 | People at 9:45:11 / 9:47:31 / 9:52:31 | 5 / 1 / 4 | 5 / 3 / 2 | 5–6 / 3 / 4 |
| C08 | Describe people who went into shops | 24 entrants | "no one" | "could not confirm anyone" |
| C09 | What happened around 9:49? | the busiest moment (8 people) | "quiet" | "very quiet, 3–6 people" |
| C10 | Alone or in groups, how many groups? | 15 pairs | some groups | "mostly alone, 2–3 groups" |

Answer time: median 37 s without tracks (10–43 s), median 34 s with them (7–78 s).

**Verdict: counting people at a moment works; behaviour does not yet.**

- **Close to the truth:** the busiest moment (time within 25 s, count +2), people at given instants (2 of 3 within 1), and people in view on average (+1).
- **Wrong:**
  - Shop entries: 0 against 24.
  - Browsing and window-shopping: none against 13.
  - Groups: 2–3 against 15.
  - Time per visit: 276 s against 25 s.
  - "What happened at 9:49": the agent said "quiet" at the busiest moment of the recording.
- **The agent did not make things up.** Every answer says what it checked and how sure it is ("could not confirm anyone stepping into a shop"). But on shop entries and groups it confidently reported zero instead of saying it could not tell.

## Why, and what would fix it (without hand-coding)

1. **Shop entries and browsing are events in time.** Glimmer looked at 6 frames spread across 9 minutes, at 384×288, where doorways are a few pixels wide. The signal is in the tracks: a person whose track ends at a shop doorway entered the shop. The agent has the tools to find it (SAM can locate "shop entrance", SQL has every track's last foot point), but it never combined them. Next: give the agent a **track-event view** (where each track starts and ends, and how long it stood still) and denser frame sampling around those moments, as Deep Video Discovery does with clip-level search.
2. **Identities merge visits.** CAVIAR's 7 recordings use a small group of actors, so DINOv2 correctly links the same actor across recordings. An identity's first-to-last span then covers several visits (276 s). Next: **visits** = an identity's continuous presence segments, split at absences, so time per visit comes from those.
3. **Over-counting by about 1 person.** SAM's `person` finds people the labels leave out (likely people inside the shops behind glass, or reflections on the shiny floor). Next: let the agent check counts with `look` more often, or filter by where feet are on the floor.
4. **Groups.** Pairs walk close together and move together. A co-movement table (pairs of identities that stay near each other) would let the agent count groups with SQL.
5. **Resolution.** 384×288 is far below a modern store camera. These clips are a hard test.

## Next steps

- **Move the GPU services to RunPod Serverless** so a stuck host is replaced automatically, with a warm worker for live cameras and scale-to-zero for Ask, captions and backfills (prices to be confirmed before creating anything).
- **Backfill as a product feature:** let an owner upload past footage and ask about it (today it is a script).
- **The four harness additions above:** track events, visits, co-movement, and checking counts against frames. Then run this same scored test again.
