# Supermarket "Ask": an owner's free-form questions answered by Meta models (live, 1 Nov 2026)

A store owner asks anything about what the cameras saw ("how many customers at 11 pm?", "how long do people stay?", "which lane is busier?") and the system works out how to answer. Nothing about the questions is coded: **Muse Spark 1.3** reads the question and all the context, picks its own tools step by step, and writes the answer. Prototype mode is on: no guardrails. Everything ran live on the deployed stack. The raw data (every answer, every step, the captions) is in `2026-11-01-supermarket-ask.json`.

## How the harness works (and where the ideas come from)

Before building the second version I looked at how the strongest open video question-answering systems are put together:

| Source | Idea | What Rulecam does with it |
|---|---|---|
| [NVIDIA Video Search and Summarization blueprint](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization) ([Context-Aware RAG](https://nvidia.github.io/context-aware-rag/vss.html)) | A VLM writes timestamped **dense captions** of the video as it arrives; questions are answered over that caption memory; a `scenario` and `objects_of_interest` give the domain context | **Caption memory**: Muse Glimmer describes each camera every 10 s into a `captions` table. The owner's **site profile** and **camera names** go into every prompt |
| [VideoAgent (ECCV 2024)](https://www.ecva.net/papers/eccv_2024/papers_ECCV/html/3241_ECCV_2024_paper.php) | Two memories: temporal (event descriptions) and **object memory** (tracked objects in a SQL database), queried by an LLM agent with tools | The SAM 3.1 tracks are the object memory (`track_points`, `track_spans`); the captions are the temporal memory; Spark queries both with SQL |
| [DoraemonGPT (ICML 2024)](https://arxiv.org/abs/2401.08392v2) | Space-dominant and time-dominant symbolic memory, queried with SQL, with the planner kept on a short, task-relevant context | Spark gets **per-camera data statistics** up front (time range, concepts, most present at once, typical track length, keyframe rate) and knows the data's known flaws |
| [SAM 3 and SAM 3 Agent](https://arxiv.org/pdf/2511.16719) ([repo](https://github.com/facebookresearch/sam3)) | An MLLM calls SAM 3 as a tool with short noun phrases and inspects **numbered mask overlays** (set-of-marks) before trusting them | `count_objects` sends any noun phrase to SAM 3.1 and returns boxes. `look` draws the tracked objects as **numbered boxes** and returns number → track_id, so what Glimmer sees joins the SQL tables |
| [Roboflow dwell time and zone analytics](https://blog.roboflow.com/dwell-time-and-zone-analytics/) | Dwell time = time inside a zone, measured at the foot point | Spark gets boxes and foot points and defines areas itself (Q13 split the view into the two lanes on its own) |

**Tools Spark can call, any number of times, in any order (up to 8 steps):**

1. `sql`: one read-only DuckDB query over `track_points`, `track_spans` and `captions` (a fresh snapshot per question; no file access, no writes).
2. `count_objects`: SAM 3.1 (Meta's hosted API) finds any noun phrase Spark chooses on the recorded frames at up to 6 instants; returns counts and boxes.
3. `look`: Muse Glimmer (2 × L40S, self-hosted) looks at the recorded frames at up to 6 instants with numbered boxes on the tracked objects, and answers Spark's question.
4. `answer`: the reply, in the owner's language, with the time range covered and how sure it is.

**Context Spark gets with every question:** current time and site time zone, the owner's site profile, the owner's camera names, what the planner's survey saw on each camera, the tracked objects and data statistics per camera, the recorded video range per camera, the 4 newest captions per camera, the table schemas, and the known data flaws (one person can be split into several tracks; a cashier can also be tracked as a person).

**Owner settings used in this test** (`.env`; plain words, no answers in them):

```
RULECAM_SITE_PROFILE="A supermarket (grocery store). The owner wants to understand customers: how many, when it is busy, how long they stay, queues at the checkout and the entrance, carts and baskets, and what staff are doing."
RULECAM_CAMERA_NAMES="phone-64=entrance queue (outside the store),phone-65=checkout lanes"
RULECAM_ASK_CAPTION_EVERY_S=10
RULECAM_PROTOTYPE_MODE=true
```

## The test

**Two store cameras**, fed live as paired phone cameras (free Pexels stock footage):

| Camera | Clip | What is in it |
|---|---|---|
| `phone-65` "checkout lanes" | [Pexels 39221979](https://www.pexels.com/video/busy-supermarket-checkout-in-auckland-39221979/), 35 s, fixed camera | a busy checkout in a New Zealand supermarket: customers with trolleys, one cashier clearly serving, a woman with a red handbag |
| `phone-64` "entrance queue" | [Pexels 4775505](https://www.pexels.com/video/people-practicing-social-distancing-4775505/), 9 s, fixed camera | about 5 people queueing outside a store with blue shopping carts, spaced apart |

The owner first typed one setup request on each camera, "Keep track of every customer, shopping cart and cashier in the store". Spark compiled it into rules that track `person`/`customer`, `shopping cart` and `cashier`, so the runtime tracked those with self-hosted SAM 3.1. Then the owner asked 13 questions through `POST /ask`.

**Important caveat:** each clip is short, and the test harness loops it while the planner works, so the "4 minutes" of checkout recording are the same 35 seconds repeated about 7 times. Questions about totals over time, dwell time and timelines therefore describe repeats of the same people. That is a limit of the test footage, not something the agent could know.

## Results

| # | Owner's question | Answer (short) | Checked against the video | Tools Spark chose | Time |
|---|---|---|---|---|---|
| Q01 | How many customers were at the checkout at 11:01 pm? | ~7–8 people in view (6–7 customers + 1–2 cashiers) | ✅ about 8–9 people in view, 1 cashier clearly serving | sql ×2, look | 65.7 s |
| Q02 | On average, how long did a customer spend at the checkout? | Tracks average 3.9 s, which it calls a lower bound because people are split into many tracks; from frames a minute apart it estimates 1–2 minutes per customer, confidence medium | 🟡 honest about the tracker, but on a looped 35 s clip the "same" customers at 23:01 and 23:02 are repeats of the loop, so the 1–2 minute estimate is not reliable | sql ×2, look | 70.6 s |
| Q03 | When was the checkout busiest, and how many people? | 10 people at once, repeatedly (23:00:04, 23:00:11, …); cross-checked with SAM: 8–9 | ✅ 10 is the tracker's peak, SAM's 8–9 matches the frames | sql, count_objects | 17.9 s |
| Q04 | How many carts around 11:01 pm, how many full? | 2–4 carts (typically 3); 2 full, 1 empty | ✅ plausible: full trolleys at both lanes | count_objects, look | 29.1 s |
| Q05 | How many cashiers, doing what? | 2 cashiers, one per open lane, serving customers | 🟡 1 cashier clearly serving; a second at the far lane is possible but not clear | sql ×2, look | 29.2 s |
| Q06 | How many waiting outside the entrance, keeping distance? | 5 people, each with a blue cart, 1–2 cart-widths apart: yes | ✅ | look | 23.2 s |
| Q07 | How many different customers today, both cameras? | Only ~8 minutes recorded; raw tracks (427) are an upper bound; real number much lower (~5 at the entrance, ~7 at checkout seen at once) | ✅ honest, refuses to give the inflated track count as the answer | sql ×2, look | 37.7 s |
| Q08 | How many customers at 1 pm? | Not recorded at 1 pm, so unknown; says what is covered | ✅ | sql ×3 | 16.4 s |
| Q09 | Anyone using a phone or carrying a red bag? | No phones; one woman with a red handbag on her trolley, pink-and-white striped top, black trousers | ✅ exactly right | look ×2 | 44.1 s |
| Q10 | (Spanish) ¿Cuántos clientes había en la caja a las 23:01 y cuánto tiempo se quedaron? | Answered in Spanish: 7–8 people in view, 3–4 in the queue; explains the track-length limit | ✅ | sql ×4, look | 35.7 s |
| Q11 | What happened at the checkout in the last few minutes? Timeline | A minute-by-minute timeline of who was at which lane | 🟡 rich and mostly right, but it says the red-bag woman was "on phone", which contradicts Q09 and the frames | sql, look | 28.6 s |
| Q12 | Was any customer left waiting without being served? | No: a short queue, served continuously | ✅ | sql ×5, look | 29.4 s |
| Q13 | Which checkout lane was busier, left or right? | Right: located both counters, split the view at x = 400 px, compared tracked people per side, cross-checked visually | ✅ the right lane is the busy one in the clip | sql ×3, look ×2, count_objects | 64.1 s |

**Summary:** 13 of 13 questions answered; 10 right, 3 partly right (Q02 fooled by the looped footage, Q05 not verifiable from the frames, Q11 one wrong detail). Nothing was refused. Every answer states the time range it covers and how it was worked out. Median 29 s per question (16–71 s), 3.2 tool steps on average.

### Before and after the harness upgrade

The same 10 questions were asked with the first version of the harness on an earlier recording of the same clips (answers are in each question's details below).

| | First version | Upgraded harness |
|---|---|---|
| Questions answered | 10 of 10 (7 of 10 failed in its very first run, before a fix for Spark replies that wrap JSON in prose) | 13 of 13 |
| Owner had to name cameras in each question ("phone-62 = checkout") | yes | no: camera names come from the site settings |
| "What happened" / timeline questions | no memory to answer from | answered from the caption memory |
| Area questions (which lane) | no boxes to work with | Spark defines the areas from boxes itself (Q13) |
| Visual answers joined to the tracks | no | numbered boxes map Glimmer's "number 3" to a track_id |
| Median time per question | 24 s | 29 s (more cross-checks per answer) |

## Glimmer speed-up (asked for alongside)

| Setup | 8 verifications at once (8 frames each) | Notes |
|---|---|---|
| 1 × L40S, 2 slots (before) | 60.1 s wall, each 15.9–60.1 s | |
| 1 × A100 80 GB, 4 slots (tried) | 91.9 s wall | slower: with this llama.cpp build the A100 read the frames at ~430–790 tokens/s vs ~1,440 on the L40S and generated at ~16 vs 21–36 tokens/s; deleted |
| 1 × H200 (tried) | – | RunPod never started the container (45 min "awaiting container"); deleted |
| 1 × H100 (tried earlier) | – | about half the requests failed at RunPod's proxy; deleted |
| **2 × L40S pool (now)** | **31.7 s wall, each 16.9–31.7 s** | the client sends each call to the least busy server and fails over (`GLIMMER_EXTRA_BASE_URLS`) |

On the busy kitchen test with the pool, decisions came 21–27 s after each event (that run raised 4 candidates; the earlier run raised 20 and its last decisions came up to 179 s late). The pool also serves Ask's `look` calls and the caption memory.

## Limits and next steps

- **One person, many tracks.** The keyframe tracker split the checkout's ~10 people into 412 tracks over the looped recording. The agent knows this and says so, but dwell time and unique-visitor counts need re-identification. VideoAgent merges track fragments by appearance features (CLIP); the same could be done here, or by asking SAM 3.1's video mode to track across chunks.
- **Test footage is a loop.** A real store camera (or a longer clip) is needed to judge totals, dwell and timelines properly.
- **Captions can be thin.** 1 of 19 captions came back nearly empty, and captions add about 22 s of Glimmer time per camera per 10 s of video. On many cameras they need their own GPU or a lower rate.
- **Consistency across answers.** Q09 and Q11 disagreed about a phone. Running the final answer past a second `look` for each concrete visual claim would catch this.
- **Restarts clear the video index.** After an API restart the recorded video of earlier cameras cannot be looked at (the chunk index lives in memory); the tracks and captions remain.
- `/ask` is served only with `RULECAM_PROTOTYPE_MODE=true`, and `src/rulecam/ask/data.py` is exempted from the team's `track_id` guardrail tests on the team lead's decision (dwell time needs one row per track).

## Per-question details

### Q01: How many customers were at the checkout at 11:01 pm?

**Answer** (65.7 s; models: muse-spark-1.3, muse-glimmer-30b):

> At 11:01 pm at the checkout lanes there were about 7-8 people visible at any instant (range 4-10 across the minute, typically 7-8) – about 6-7 customers plus 1-2 cashiers serving 2 active lanes, with the main lane 2-deep behind the person being served. Tracked-person counts for 23:01:00-23:01:30 on phone-65 cross-checked by visual review; tracking flicker means treat as ~7-8, high confidence.

**Steps the model chose**

| # | Tool | What it asked | What came back | Time |
|---|---|---|---|---|
| 1 | sql | SELECT ts, COUNT(DISTINCT track_id) AS n FROM track_points WHERE camera_id='phone-65' AND concept='person' AND ts BETWEEN TIMESTAMP '2026-11-01 23:00:30' AND TIMESTAMP '2026-11-01 23:01:30' GROUP BY ts ORDER BY ts | 60 rows: [["2026-11-01T23:00:30.412522Z", 7], ["2026-11-01T23:00:30.912522Z", 8], ["2026-11-01T23:00:31.412522Z", 8]] | 0.0 s |
| 2 | sql | SELECT ts, COUNT(DISTINCT track_id) AS n FROM track_points WHERE camera_id='phone-65' AND concept='person' AND ts BETWEEN TIMESTAMP '2026-11-01 23:01:00' AND TIMESTAMP '2026-11-01 23:01:30' GROUP BY ts ORDER BY ts | 60 rows: [["2026-11-01T23:01:00.412522Z", 6], ["2026-11-01T23:01:00.912522Z", 6], ["2026-11-01T23:01:01.412522Z", 5]] | 0.0 s |
| 3 | look | How many people are visible? How many look like staff/cashiers vs customers? Describe the queue. (phone-65 at 23:01:05, 23:01:20) | From the two frames a total of about nine distinct individuals can be identified. Staff/cashiers: 1 clearly identifiable cashier at the checkout (dark polo with name badge); a second staff member is … (count None, 21 numbered boxes) | 53.7 s |

*Before the harness upgrade* (30.5 s): At 10:38 pm on Nov 1 (22:38 UTC) there were 4 customers at the checkout on camera phone-62, plus 1 cashier staffing the lane. This covers the last recorded frames from 22:37:50 to 22:38:15, as recording ends at 22:38:16. I got this by AI person-counting and visual inspection of the checkout frames.


### Q02: On average, how long did a customer spend at the checkout?

**Answer** (70.6 s; models: muse-spark-1.3, muse-glimmer-30b):

> On the checkout lanes camera (phone-65) from 22:59:21 to 23:03:26 UTC on 1 Nov 2026 (~4 min covered):
>
> Tracked `person` dwell per track_id is very short because one real shopper is split into many track_ids:
> - 412 person tracks, mean 3.9 sec, median 2.0 sec, max 20.3 sec
> - 27 shopping-cart tracks, mean 2.4 sec, median 1.0 sec
> - 9 store-cashier tracks, mean 6.4 sec
>
> That is a lower bound, not the true shopping time.
>
> Visually the same customers dwell much longer: at 23:01, 23:02 and 23:03 the central lane has 1 customer being served + 1 queuing behind, the left lane has 1 customer being served. The left-lane man in the blue-white striped polo is at the counter at 23:01 and 23:02 and leaves with his cart by 23:03 - about ~2 minutes at the checkout. The two women at the central lane are present in all three 1-min-apart frames.
>
> So: per-track average ~4 sec, but a typical customer you see spends on the order of 1-2 minutes at the checkout lane (queuing + being served). Confidence medium: numbers are exact from track_spans, true dwell is visual estimate because tracks fragment on occlusion/re-entering.

**Steps the model chose**

| # | Tool | What it asked | What came back | Time |
|---|---|---|---|---|
| 1 | sql | SELECT concept, COUNT(*) AS tracks, AVG(seconds) AS avg_s, MEDIAN(seconds) AS med_s, MIN(seconds) AS min_s, MAX(seconds) AS max_s FROM track_spans WHERE camera_id='phone-65' GROUP BY concept | 3 rows: [["shopping cart", 27, 2.4461, 1.0, 0.0, 11.5], ["store cashier", 9, 6.3986, 1.0, 0.0, 27.0436], ["person", 412, 3.8911, 2.0, 0.0, 20.3447]] | 0.0 s |
| 2 | sql | SELECT seconds, points, first_seen, last_seen FROM track_spans WHERE camera_id='phone-65' AND concept='person' ORDER BY seconds DESC LIMIT 30 | 30 rows: [[20.3447, 41, "2026-11-01T23:01:54.011995Z", "2026-11-01T23:02:14.356661Z"], [20.1369, 41, "2026-11-01T23:02:30.256976Z", "2026-11-01T23:02:50.393879Z"], [20.… | 0.0 s |
| 3 | look | Describe the checkout lanes: how many customers are queuing vs being served, and how long does a customer transaction appear to take? Are people dwelling or just passing? (phone-65 at 23:01:00, 23:02:00, 23:03:00) | From the three 1-minute-spaced frames (23:01:00, 23:02:00, 23:03:00) two checkout positions are visible. The central lane shows a cashier serving a woman with a blonde ponytail at the counter with an… (count 1, 36 numbered boxes) | 52.9 s |

*Before the harness upgrade* (10.7 s): On phone-62 (checkout) on 1 Nov 22:37:43-22:38:18 UTC - only ~35 sec of recording is available, so this is time visible in view, not a full end-to-end checkout: - 65 person tracks: 3.8 sec average overall, 7.0 sec average for the 32 tracks lasting >=2 sec (longest 20.0, 17.0, 16.0 sec). - 27 shopping-cart tracks (best customer proxy): 2.4 sec average overall, 5.7 sec average for the 10 tracks las…


### Q03: When was the checkout busiest, and how many people were there at that moment?

**Answer** (17.9 s; models: muse-spark-1.3, sam-3.1):

> The checkout lanes (phone-65) were busiest with 10 people present at once. This peak was reached repeatedly during the recording, first at about 23:00:04 UTC and again at e.g. 23:00:11, 23:00:39, 23:00:47, 23:01:15, 23:01:22, 23:01:50, 23:01:58, 23:02:27 and 23:02:34 UTC. Recording only covers 22:59:21 to 23:03:24 UTC on 1 Nov. I counted distinct tracked persons per timestamp from the tracking data, cross-checked with a visual detector which found 8-9 people in the same frames, so 10 is reliable as an upper bound.

**Steps the model chose**

| # | Tool | What it asked | What came back | Time |
|---|---|---|---|---|
| 1 | sql | SELECT ts, COUNT(DISTINCT track_id) AS n FROM track_points WHERE camera_id = 'phone-65' AND concept = 'person' GROUP BY ts ORDER BY n DESC, ts LIMIT 10 | 10 rows: [["2026-11-01T23:00:04.412522Z", 10], ["2026-11-01T23:00:11.912522Z", 10], ["2026-11-01T23:00:39.912522Z", 10]] | 0.0 s |
| 2 | count_objects | `person` on phone-65 at 23:00:04, 23:01:58 | 23:00:04: 8; 23:01:58: 9 | 9.3 s |

*Before the harness upgrade* (15.3 s): The checkout (phone-62) was busiest at 22:37:49 UTC and 22:37:56 UTC on Nov 1, with 10 people present at that moment. Recording covers 22:34:24 to 22:38:18 UTC – those two instants were the peak, staying at 9 people for the next ~20 seconds until 22:38:17 UTC. How I got it: counted distinct tracked persons per instant (peak = 10), then double-checked with visual counts (8-9 visible due to occlusi…


### Q04: How many shopping carts were at the checkout around 11:01 pm, and how many of them were full?

**Answer** (29.1 s; models: muse-spark-1.3, sam-3.1, muse-glimmer-30b):

> Around 11:01 pm at the checkout lanes (phone-65, recording 10:59 pm - 11:03 pm UTC):
>
> - Shopping carts visible: 2-4 depending on the exact second - 1 at 11:01:00, 4 at 11:01:30 (2 high-confidence + 2 low-confidence fragments), 2 at 11:02:00. Typically 3 carts in view.
> - Full vs empty (visual check at 11:01:30-11:02:00): 3 carts clearly visible - 2 full / in use with groceries and shopping bags, and 1 empty / essentially empty.
>
> How I got it: SAM detector counted 'shopping cart' on 3 frames around 11:01-11:02 pm, then Glimmer visually inspected the same frames to judge groceries inside. Confidence medium-high - carts move in and out of frame quickly so the exact second matters, but 3 carts with 2 full is representative for 11:01-11:02 pm.

**Steps the model chose**

| # | Tool | What it asked | What came back | Time |
|---|---|---|---|---|
| 1 | count_objects | `shopping cart` on phone-65 at 23:01:00, 23:01:30, 23:02:00 | 23:01:00: 1; 23:01:30: 4; 23:02:00: 2 | 4.9 s |
| 2 | look | At this checkout view, how many shopping carts are visible and how many of them are full of groceries vs empty? Describe each cart. (phone-65 at 23:01:30, 23:02:00) | There are three shopping carts visible in the checkout view. * Cart A – the metal trolley immediately behind the woman in the zebra-print dress (label 2 person / 15 person). It is an empty / essentia… (count None, 21 numbered boxes) | 15.4 s |

*Before the harness upgrade* (19.3 s): At the checkout (phone-62) around 22:38:00 UTC there were 2 shopping carts visible: 1 full of goods at the active lane and 1 empty behind the left-hand lane. I got this by visually inspecting the recorded checkout frames around 22:37:50-22:38:10 UTC.


### Q05: How many cashiers were working at the checkout, and what were they doing?

**Answer** (29.2 s; models: muse-spark-1.3, muse-glimmer-30b):

> At the checkout lanes (phone-65, 22:59-23:03 UTC on 1 Nov):
>
> 2 cashiers were working – one at each open checkout counter:
> - Right-hand lane (centre-right of image): staffed continuously, long track 23:02:59-23:03:26 + fragments before it
> - Left-hand lane (far left edge of image): staffed continuously, track 23:02:51-23:03:11 continuing as 23:03:20-23:03:26
>
> What they were doing: both were at their registers serving customers – lane in use with a small queue forming, customers with 2-3 shopping trolleys moving through and interacting with staff. Captions for 23:02:23-23:03:06 describe "a small queue is forming at one lane and customers are interacting with a staff member" and "the lane is in use with a small queue forming".
>
> How I got it: 9 'store cashier' track fragments cluster in x-pixel into 2 stations (~770px and ~70-110px) that are occupied for the whole 23:02:51-23:03:26 window, plus captions describing active checkout. Medium-high confidence – track IDs fragment so count is by position, not by ID, and the visual check confirmed cashier boxes at two counters.

**Steps the model chose**

| # | Tool | What it asked | What came back | Time |
|---|---|---|---|---|
| 1 | sql | SELECT concept, track_id, first_seen, last_seen, seconds FROM track_spans WHERE camera_id='phone-65' AND concept ILIKE '%cashier%' ORDER BY first_seen | 9 rows: [["store cashier", "phone-65:store cashier:fd50d379", "2026-11-01T23:02:51.893879Z", "2026-11-01T23:03:11.893879Z", 20.0], ["store cashier", "phone-65:store ca… | 0.0 s |
| 2 | look | How many store cashiers / staff working at checkout counters are visible? For each, describe what they are doing (scanning, bagging, talking to customer, etc) and what they look like / where they stand. (phone-65 at 23:02:56, 23:… | count 1 (count None, 25 numbered boxes) | 15.2 s |
| 3 | sql | SELECT track_id, COUNT(*) n, MIN(ts) t0, MAX(ts) t1, AVG(foot_m_x) mx, AVG(foot_m_y) my, AVG((x1+x2)/2) px FROM track_points WHERE camera_id='phone-65' AND concept='store cashier' GROUP BY track_id ORDER BY t0 | 9 rows: [["phone-65:store cashier:fd50d379", 39, "2026-11-01T23:02:51.893879Z", "2026-11-01T23:03:11.893879Z", null, null, 73.2056], ["phone-65:store cashier:65127fe7"… | 0.0 s |

*Before the harness upgrade* (22.0 s): 1 cashier was working at the checkout on phone-62 (coverage 22:34:24-22:38:18 UTC on 2026-11-01, checked 22:37:45-22:38:10). What she was doing: woman in dark grey polo with green collar and name badge, behind the counter on the right side, smiling and facing 2 customers at the counter (blonde ponytail in grey top/leggings and short grey hair in zebra-print top), hands near scanner/bagging area -…


### Q06: How many people were waiting in the line outside the entrance, and were they keeping their distance from each other?

**Answer** (23.2 s; models: muse-spark-1.3, muse-glimmer-30b):

> 5 people were waiting in the line outside the entrance (22:56-22:58 UTC on the entrance queue camera). They were spread along the storefront, each with a blue shopping cart, with ~1-2 cart-widths gap between neighbours - so yes, they were keeping distance and not standing in close contact. From visual inspection of the entrance frames, cross-checked with captions reporting 5 people; spacing estimate is approximate.

**Steps the model chose**

| # | Tool | What it asked | What came back | Time |
|---|---|---|---|---|
| 1 | look | How many people are waiting in line outside the entrance? Are they keeping distance from each other? Describe positions and spacing. (phone-64 at 22:56:30, 22:57:30, 22:58:20) | There are five people visible waiting outside the entrance in the frames that are shown. From left to right the line is: 12 person with a blue shopping cart at the far left, 7 person standing a short… (count None, 12 numbered boxes) | 17.5 s |

*Before the harness upgrade* (26.3 s): About 5-6 people were waiting in line outside the entrance on phone-61 during the recording 22:30:56 - 22:33:41 UTC (Nov 1). - Count: SAM counts found 6 people at 4 checks (22:31:10, 22:32:00, 22:32:50, 22:33:35). Visual review shows 5 people clearly lined up (plus 1 partially visible man on the far left) - each with a blue shopping cart, facing the entrance. - Distancing: Yes, they were keeping …


### Q07: How many different customers did we see in total today across both cameras?

**Answer** (37.7 s; models: muse-spark-1.3, muse-glimmer-30b):

> Today we only have recording for ~8 minutes, not the whole day:
> - entrance queue (outside): 2026-11-01 22:55:45 to 22:58:30 UTC
> - checkout lanes: 2026-11-01 22:59:21 to 23:03:24 UTC
>
> Raw tracker counts (upper bound, because one real person gets split into several track_ids when occluded):
> - entrance: 15 person track_ids, max 7 at once, average ~5.9
> - checkout: 412 person track_ids + 9 store-cashier track_ids + 27 shopping-cart track_ids, max 10 persons at once, average ~7.4
> - Combined raw: 15 + 412 = 427 person track_ids.
>
> Real different customers are much lower. Captions report 5 people at the entrance and 7 people at checkout. Visual check at checkout 23:02:30 shows ~4 customers + 1 cashier on duty (2 cashier stations exist in store). So in the recorded window we saw roughly 5-7 people at the entrance and 7-10 people at checkout at any instant, i.e. on the order of 10-15 different customers in total, many seen on both cameras as they moved inside (entrance 22:58 -> checkout 22:59-23:03), so you cannot add the two cameras.
>
> How obtained: counted distinct track_id in track_spans per camera, checked max/average present at one timestamp in track_points (reliable), and cross-checked with captions + a visual look. High confidence that 427 is a strict upper bound; medium confidence that true distinct customers in the 8-min coverage is ~10-15.

**Steps the model chose**

| # | Tool | What it asked | What came back | Time |
|---|---|---|---|---|
| 1 | sql | SELECT camera_id, concept, COUNT(*) AS tracks, COUNT(DISTINCT track_id) AS distinct_ids FROM track_spans WHERE camera_id IN ('phone-64','phone-65') GROUP BY camera_id, concept ORDER BY camera_id, concept | 5 rows: [["phone-64", "blue shopping cart", 15, 15], ["phone-64", "person", 15, 15], ["phone-65", "person", 412, 412]] | 0.0 s |
| 2 | sql | WITH per_ts AS (SELECT camera_id, ts, COUNT(DISTINCT track_id) AS n FROM track_points WHERE camera_id IN ('phone-64','phone-65') AND concept='person' GROUP BY camera_id, ts) SELECT camera_id, MAX(n) AS max_at_once, AVG(n) AS avg_… | 2 rows: [["phone-64", 7, 5.9474, 19], ["phone-65", 10, 7.4408, 456]] | 0.0 s |
| 3 | look | How many people are customers vs staff (cashiers)? Describe what each numbered person is doing. (phone-65 at 23:02:30) | From the single visible frame there are four distinct customers at / near the checkout and one identifiable staff member working the checkout. The yellow tracker boxes are overlapping / duplicated on… (count 5, 10 numbered boxes) | 17.5 s |

*Before the harness upgrade* (96.3 s): Today we only have very short recordings, not full-day coverage: - phone-61 entrance: video 22:30:56-22:33:41 UTC, tracked people 22:33:29-22:33:41 UTC (~12 sec) - phone-62 checkout: video 22:34:24-22:38:16 UTC, tracked people 22:37:43-22:38:18 UTC (~35 sec) Counts in what is covered: - Entrance: 6 people visible at one time (SAM count 6 and 6 on 2 frames, tracker 5-7 present). No staff uniform s…


### Q08: How many customers were in the store at 1 pm?

**Answer** (16.4 s; models: muse-spark-1.3):

> I can't tell you how many customers were in the store at 1 pm (13:00 UTC on 2026-11-01) — the recording does not cover that time, so the count is unknown.
>
> What I checked: I looked for any tracked objects overlapping TIMESTAMP '2026-11-01 13:00:00' and found 0 tracks. The recording around that time has a gap: coverage jumps from ~03:35 UTC to ~16:49 UTC on Nov 1, with the two supermarket views only covering:
> - entrance queue (outside the store): 22:55:45 to 22:58:30 UTC
> - checkout lanes: 22:59:21 to 23:03:24 UTC
>
> How sure: very sure there is no data for 1 pm — both the track table and the recorded-video ranges show no coverage. If you want, I can count customers for the covered period tonight ~23:00 instead.

**Steps the model chose**

| # | Tool | What it asked | What came back | Time |
|---|---|---|---|---|
| 1 | sql | SELECT camera_id, concept, COUNT(DISTINCT track_id) AS tracks_overlapping_13h FROM track_spans WHERE first_seen <= TIMESTAMP '2026-11-01 13:00:00' AND last_seen >= TIMESTAMP '2026-11-01 13:00:00' GROUP BY camera_id, concept ORDER… | 0 rows: [] | 0.0 s |
| 2 | sql | SELECT camera_id, MIN(first_seen) AS first_track, MAX(last_seen) AS last_track, COUNT(*) AS spans FROM track_spans GROUP BY camera_id ORDER BY first_track | 44 rows: [["phone-5", "2026-10-31T17:47:27.526364Z", "2026-10-31T17:47:35.918209Z", 18], ["phone-6", "2026-10-31T17:47:27.987902Z", "2026-10-31T17:47:30.887902Z", 2], [… | 0.0 s |
| 3 | sql | SELECT camera_id, concept, COUNT(*) AS tracks_total, COUNT(DISTINCT track_id) AS ids FROM track_spans WHERE camera_id IN ('phone-64','phone-65') GROUP BY camera_id, concept ORDER BY camera_id, concept | 5 rows: [["phone-64", "blue shopping cart", 15, 15], ["phone-64", "person", 15, 15], ["phone-65", "person", 412, 412]] | 0.0 s |

*Before the harness upgrade* (11.2 s): No recording covers 1 pm UTC on Nov 1, so I can't count customers at that time — I checked track data from 12:55-1:05 pm and found 0 points. Coverage on Nov 1 jumps from 03:34-03:35 UTC (phone-28) to 16:49 UTC (phone-29), with no data in between.


### Q09: Is anyone at the checkout using a phone or carrying a red bag? Describe them.

**Answer** (44.1 s; models: muse-spark-1.3, muse-glimmer-30b):

> At the checkout lanes (22:59-23:03 UTC on Nov 1):
>
> - Using a phone: No. No person was seen holding or looking at a mobile phone in the frames checked. Customers were interacting with the checkout counter / trolley and the cashier.
>
> - Carrying a red bag: Yes - 1 person:
>   Woman seen from the back on the far right of the checkout area, at the right-most checkout lane shown, facing the counter/cashier. Wearing a short-sleeved pink-and-white / mauve striped T-shirt and black trousers, with her right hand on the green handle of a shopping trolley. A bright red handbag with a circular dark clasp is hanging with its handles looped over the trolley handle, at her side.
>
> How checked: visually reviewed checkout camera frames across the recorded period (about 4 minutes) with tracked-box overlay; high confidence for no-phone, high confidence for red bag description.

**Steps the model chose**

| # | Tool | What it asked | What came back | Time |
|---|---|---|---|---|
| 1 | look | Is anyone at the checkout using a phone (looking at / holding mobile phone) or carrying a red bag / red handbag? For each such person describe them: clothing, position in checkout area, what they are doing with phone or bag. (pho… | No person at the checkout is seen looking at / holding a mobile phone in the five frames that are visible. A red handbag / red bag is being carried at the checkout. (count 1, 41 numbered boxes) | 16.7 s |
| 2 | look | Which numbered person box is carrying / holding the red handbag / red bag? Describe that person in detail: clothing, position in the checkout lanes, and where the red bag is (shoulder, hand, cart?). Also double-check if any perso… | The red handbag is with the person on the far right of the checkout area, the person whose back is to the camera. In frame 1 phone-65 at 2026-11-01T23:01:57+00:00 the tracker labels that person as 5 … (count None, 18 numbered boxes) | 18.1 s |

*Before the harness upgrade* (52.0 s): At the checkout (phone-62, 22:34-22:38 UTC on Nov 1): - Using a phone: No - no one was visibly using a phone in the frames checked. - Carrying a red bag: Yes - 1 person: Older adult woman with short curly grey-brown hair, wearing a pink-and-white horizontally striped short-sleeve top and black trousers. She is on the right side at the checkout queue behind the woman in grey, standing with/pushing…


### Q10: ¿Cuántos clientes había en la caja a las 23:01 y cuánto tiempo se quedaron?

**Answer** (35.7 s; models: muse-spark-1.3, muse-glimmer-30b):

> A las 23:01 en la caja (checkout lanes, cámara phone-65) había unas 7-8 personas visibles a la vez.
>
> - Conteo por tracking: entre las 23:00:30 y 23:01:00 hay 6-10 boxes de `person` por keyframe, lo normal 7-8. En ±5 s alrededor de las 23:01:00 salen 24 track_id distintos, pero es un límite alto porque un mismo cliente se fragmenta en varios track_id.
> - Verificación visual y captions: Glimmer ve a las 23:01 unos 3-4 clientes claros en la cola (hombre de polo a rayas, mujer de vestido, mujer rubia siendo atendida + persona en primer plano) más cajera y cabezas de la caja vecina; los captions de 23:01:56-23:03:06 anotan `people: 7`. Por eso digo 7-8 personas en cuadro, de ellas 3-4 en cola principal + 1-2 cajeras + resto parciales.
>
> ¿Cuánto tiempo se quedaron? Con lo grabado no se puede medir la estancia completa:
> - La grabación de phone-65 solo cubre 22:59:21 a 23:03:26 UTC (~4 min).
> - Los 38 tracks de `person` activos entre 23:00:50 y 23:01:10 duran dentro de lo grabado: media 5,1 s, mediana 2,5 s, min 0 s, max 20 s. Son fragmentos cortos por oclusión y re-entrada, no la visita total.
>
> Cómo lo obtuve y confianza: conteo por keyframe de track_points + revisión visual con look a las 23:01:00 + captions. Confianza media-alta en el 7-8 simultáneos; baja para el tiempo total de permanencia porque la ventana es muy corta y los track_id se cortan.

**Steps the model chose**

| # | Tool | What it asked | What came back | Time |
|---|---|---|---|---|
| 1 | sql | SELECT camera_id, concept, COUNT(*) AS tracks, MIN(first_seen) AS first_seen, MAX(last_seen) AS last_seen FROM track_spans WHERE camera_id='phone-65' GROUP BY camera_id, concept | 3 rows: [["phone-65", "person", 412, "2026-11-01T22:59:21.156752Z", "2026-11-01T23:03:26.937450Z"], ["phone-65", "store cashier", 9, "2026-11-01T23:02:51.893879Z", "20… | 0.0 s |
| 2 | sql | WITH ts AS (SELECT TIMESTAMP '2026-11-01 23:01:00' AS t) SELECT (SELECT COUNT(DISTINCT track_id) FROM track_points, ts WHERE camera_id='phone-65' AND concept='person' AND ts BETWEEN TIMESTAMP '2026-11-01 23:01:00' - INTERVAL '5 s… | 1 rows: [[24, 0, "2026-11-01T22:59:21.156752Z", "2026-11-01T23:03:26.937450Z"]] | 0.0 s |
| 3 | sql | SELECT ts, COUNT(DISTINCT track_id) AS n_person_tracks FROM track_points WHERE camera_id='phone-65' AND concept='person' AND ts BETWEEN TIMESTAMP '2026-11-01 23:00:30' AND TIMESTAMP '2026-11-01 23:01:30' GROUP BY ts ORDER BY ts | 60 rows: [["2026-11-01T23:00:30.412522Z", 7], ["2026-11-01T23:00:30.912522Z", 8], ["2026-11-01T23:00:31.412522Z", 8]] | 0.0 s |
| 4 | sql | SELECT COUNT(*) AS n_tracks_activos, AVG(seconds) AS media_s, MEDIAN(seconds) AS mediana_s, MIN(seconds) AS min_s, MAX(seconds) AS max_s FROM track_spans WHERE camera_id='phone-65' AND concept='person' AND first_seen <= TIMESTAMP… | 1 rows: [[38, 5.1053, 2.5, 0.0, 20.0]] | 0.0 s |
| 5 | look | How many customers/people are visible at the checkout at this instant? Briefly describe queue. (phone-65 at 23:01:00) | There are three customers clearly in line at the main checkout lane that is in view and a fourth customer is partially visible in the foreground on the right. The lane is being served by one cashier.… (count None, 9 numbered boxes) | 16.6 s |

*Before the harness upgrade* (37.7 s): A las 22:38 en phone-62 (caja) había 4 clientes + 1 cajera atendiendo: - Cajera: 1 mujer con uniforme gris oscuro y polo verde, detrás del mostrador atendiendo. - Clientes: 2 hombres mayores a la izquierda (uno de camisa a rayas blancas/azules en la cinta, otro de camiseta roja parcialmente visible), 2 mujeres mayores frente al mostrador (una de vestido cebra, otra de camiseta gris con coleta) y …


### Q11: What happened at the checkout in the last few minutes? Give me a short timeline.

**Answer** (28.6 s; models: muse-spark-1.3, muse-glimmer-30b):

> Checkout lanes (camera: checkout lanes) – last ~4 min covered: 22:59:21 to 23:03:24 UTC:
>
> • 22:59:21-22:59:58 - Lane in use by 2 customers with trolleys: woman in striped top with red handbag pushing trolley + older man in white/navy striped polo with trolley with produce. Staff member in dark grey uniform with name badge serving, smiling. Man in red T-shirt at adjacent checkout by Alcohol Policy sign. ~5-7 people visible, no long queue.
> • 23:00:27-23:00:55 - Both adjacent checkouts in use. Staff serving at right lane, 2 older male customers at left lane. Woman with red handbag to right of lane on phone. One full trolley at lane + second trolley being brought up. ~7 people, calm.
> • 23:01:32-23:01:42 - Single staff serving small queue of older customers. Trolley at lane, red handbag customer present. Calm.
> • 23:02:23-23:03:06 - Small queue forming at one lane, customers interacting with staff. 2 trolleys in foreground/mid-ground, candy display left. ~7 people present.
>
> Overall: busy but orderly, 1-2 lanes active with 2-3 customers queuing at main lane, no incident.
>
> How I got it: read all 10 captions for checkout lanes in that window and visually checked 3 frames (22:59, 23:00, 23:02). High confidence - consistent across captions and visual check.

**Steps the model chose**

| # | Tool | What it asked | What came back | Time |
|---|---|---|---|---|
| 1 | sql | SELECT camera_id, t0, t1, caption, people FROM captions WHERE camera_id='phone-65' ORDER BY t0 DESC LIMIT 25 | 10 rows: [["phone-65", "2026-11-01T23:03:14.937450Z", "2026-11-01T23:03:24.937450Z", "Checkout lane phone-65, 2026-11-01T23:03:14.937450+00:00 to 2026-11-01T23:03:24.93… | 0.0 s |
| 2 | look | Describe what is happening at the checkout lanes in these frames: how many lanes in use, queue length, staff and customers activity. (phone-65 at 22:59:25, 23:00:50, 23:02:30) | Across the three frames the camera is looking at a supermarket checkout area. One checkout counter is clearly in use in the centre of the view. A staff member – the person labelled 3 in frame 2 and 1… (count None, 20 numbered boxes) | 19.8 s |

### Q12: Was any customer at the checkout left waiting without being served?

**Answer** (29.4 s; models: muse-spark-1.3, muse-glimmer-30b):

> No - on the checkout lanes camera (22:59:21 to 23:03:24 UTC, about 4 minutes) no customer was left waiting without being served.
>
> What was seen: a small queue formed at the centre lane - typically 1 customer being served and 1-2 waiting behind with trolleys - and captions describe "customers interacting with a staff member" and "lane in use with a small queue". Person tracks average ~3.9s and max ~20s, showing continuous flow through the lane, not long abandoned waits. Cashier tracks are detected at the end of the clip (23:02:51-23:03:26), consistent with active service.
>
> So: customers did wait briefly in queue, but all visible waiting was normal queuing while service continued. How checked: person/store-cashier track durations + visual check of 3 instants around 23:01:50-23:02:50 and captions. Confidence medium-high - coverage is only ~4 min.

**Steps the model chose**

| # | Tool | What it asked | What came back | Time |
|---|---|---|---|---|
| 1 | sql | SELECT track_id, first_seen, last_seen, seconds, points FROM track_spans WHERE camera_id='phone-65' AND concept='person' ORDER BY first_seen | 60 rows: [["phone-65:person:8f6b8058", "2026-11-01T22:59:21.156752Z", "2026-11-01T22:59:23.156752Z", 2.0, 5], ["phone-65:person:7036bda0", "2026-11-01T22:59:21.156752Z"… | 0.0 s |
| 2 | sql | SELECT concept, COUNT(DISTINCT track_id) AS tracks, MAX(seconds) AS max_s, AVG(seconds) AS avg_s FROM track_spans WHERE camera_id='phone-65' GROUP BY concept | 3 rows: [["person", 412, 20.3447, 3.8911], ["shopping cart", 27, 11.5, 2.4461], ["store cashier", 9, 27.0436, 6.3986]] | 0.0 s |
| 3 | sql | SELECT track_id, first_seen, last_seen, seconds FROM track_spans WHERE camera_id='phone-65' AND concept='person' ORDER BY seconds DESC LIMIT 15 | 15 rows: [["phone-65:person:36b46442", "2026-11-01T23:01:54.011995Z", "2026-11-01T23:02:14.356661Z", 20.3447], ["phone-65:person:82a7ce0f", "2026-11-01T23:02:30.256976Z… | 0.0 s |
| 4 | look | Was any customer left waiting without being served? Describe queues, staff activity, and whether customers appear to be served or waiting. (phone-65 at 23:01:50, 23:02:30, 23:02:50) | Yes. A queue is visible at the centre checkout and a customer is waiting behind the one being served. (count None, 28 numbered boxes) | 14.8 s |
| 5 | sql | SELECT track_id, first_seen, last_seen, seconds FROM track_spans WHERE camera_id='phone-65' AND concept='store cashier' ORDER BY first_seen; SELECT t0, t1, caption, people FROM captions WHERE camera_id='phone-65' ORDER BY t0 | {"error": "only one SELECT or WITH statement is allowed"} | 0.0 s |
| 6 | sql | SELECT track_id, first_seen, last_seen, seconds FROM track_spans WHERE camera_id='phone-65' AND concept='store cashier' ORDER BY first_seen | 9 rows: [["phone-65:store cashier:fd50d379", "2026-11-01T23:02:51.893879Z", "2026-11-01T23:03:11.893879Z", 20.0], ["phone-65:store cashier:65127fe7", "2026-11-01T23:02… | 0.0 s |

### Q13: Which checkout lane was busier, the left one or the right one?

**Answer** (64.1 s; models: muse-spark-1.3, muse-glimmer-30b, sam-3.1):

> The RIGHT lane (centre-right counter) was busier than the LEFT lane over 22:59-23:03 UTC on the checkout-lanes camera.
>
> - Tracked person activity: RIGHT ~2,639 detections / 324 track IDs vs LEFT ~723 detections / 105 track IDs (split at x=400px, left counter at 0-278px, right counter at 420-1092px).
> - Spot checks: ~23:02 right lane had 3 customers (2 at counter + 1 queuing with trolley) vs left lane 2 customers; other checks showed right 2 vs left 1.
>
> How I got it: located the 2 checkout counters, split the view left/right and compared person track points per side, cross-checked with visual counts of queues.

**Steps the model chose**

| # | Tool | What it asked | What came back | Time |
|---|---|---|---|---|
| 1 | look | Describe the checkout lanes visible. How many lanes are there, where is the left lane vs the right lane in the image, and how many customers are queuing or at each lane right now? (phone-65 at 23:02:00) | From the single frame that is visible two checkout counters / lanes can be seen in the field of view. The lane that is on the left side of the image is the left-most counter in the view and the lane … (count 2, 12 numbered boxes) | 22.2 s |
| 2 | sql | SELECT camera_id, concept, MIN(foot_px_x) as min_x, MAX(foot_px_x) as max_x, MIN(foot_m_x) as min_mx, MAX(foot_m_x) as max_mx, COUNT(*) as n FROM track_points WHERE camera_id='phone-65' AND concept='person' GROUP BY camera_id, co… | 1 rows: [["phone-65", "person", 8.4638, 1224.8359, null, null, 3393]] | 0.0 s |
| 3 | sql | SELECT track_id, ts, foot_px_x, foot_px_y, x1, y1, x2, y2 FROM track_points WHERE camera_id='phone-65' AND concept='person' AND ts BETWEEN TIMESTAMP '2026-11-01 23:01:50' AND TIMESTAMP '2026-11-01 23:02:10' ORDER BY ts, foot_px_x… | 60 rows: [["phone-65:person:0565d5cb", "2026-11-01T23:01:50.485694Z", 75.8841, 325.9041, 5.8283, 50.5019, 145.9398, 325.9041], ["phone-65:person:f02d5972", "2026-11-01T… | 0.0 s |
| 4 | count_objects | `checkout counter` on phone-65 at 23:02:00 | 23:02:00: 2 | 1.6 s |
| 5 | sql | WITH pts AS (SELECT track_id, foot_px_x, ts FROM track_points WHERE camera_id='phone-65' AND concept='person' AND ts BETWEEN TIMESTAMP '2026-11-01 22:59:21' AND TIMESTAMP '2026-11-01 23:03:25') SELECT CASE WHEN foot_px_x < 400 TH… | 2 rows: [["left", 723, 105], ["right", 2639, 324]] | 0.0 s |
| 6 | look | For the left checkout counter on the left side of the image versus the right checkout counter in the centre-right, how many customers are at or queuing at each lane right now? Give left count and right count separately. (phone-65… | left count 1, right count 2 (count None, 32 numbered boxes) | 18.9 s |
