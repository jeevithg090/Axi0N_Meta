# Crowd early warning, connected flow, live on Jülich footage (4 Nov 2026)

Branch `p1/uc-crowd`. The API ran locally (`uvicorn`, `RULECAM_MODE=live`, prototype mode,
`RULECAM_CROWD_EVERY_S=5`, `RULECAM_CROWD_ASSESS_EVERY_S=10`) against the live models: Meta
hosted SAM 3.1, Muse Spark 1.3 and Muse Voice Transcribe (Meta Model API), Muse Glimmer 30B on
the RunPod serverless endpoint `z000o8uch2wj4i`, MMS-TTS on the laptop's CPU. Notifications
went to the LAN alert store through the site's escalation chains (WhatsApp and Telegram off).
The live API pod was not touched.

Footage: the Jülich bottleneck experiment ("Crowds in front of bottlenecks", Forschungszentrum
Jülich 2018, doi 10.34735/ped.2018.1), overhead fisheye, 960×720. Run `030_c_56_h0` (64.8 s,
75 people pushing to a narrow gate) is the main gate; run `160_q_56_h-` (55.7 s, a queue) plays
side door B in the two-camera runs. Each clip was fed in real time as a paired phone camera by
`scripts/crowd/feed_clip.py` with `samples/crowd/*` (the operator's Hindi instruction, four
floor points, two zones, a SAMPLE venue layout, the ground truth).

Ground truth: `scripts/crowd/juelich_truth.py` counts the published head trajectories inside
each zone every 0.2 s, mapping pixels to metres with the homography fitted on 3 Nov (median
residual 2.3 cm). The same homography scores the 160 run as well (count ratio 0.98–1.04 below),
so both runs were filmed with the same camera set-up.

## Measurements against the truth

Every reading of each run, at its clip second (`GET /crowd/report`, truth registered with
`PUT /crowd/truth/{camera}`):

| Run | Zone | Readings | Count (system ÷ truth) | Mean count error | Density (system ÷ truth, median) | Peak system / true (per m²) |
|---|---|---|---|---|---|---|
| 2 (030) | gate approach, 2 × 2 m | 11 | 0.996 | 1.0 | 1.00 | 8.2 / 9.5 |
| 2 (030) | entry corridor | 11 | 0.997 | 1.0 | 0.78 | 2.0 / 2.5 |
| 4 (030) | gate approach | 11 | 1.004 | 0.8 | 1.00 | 8.2 / 9.5 |
| 4 (030) | entry corridor | 11 | 0.997 | 1.0 | 0.78 | 2.0 / 2.5 |
| 5 (030) | gate approach | 11 | 0.996 | 1.0 | 1.00 | 8.2 / 9.5 |
| 6 (030) | gate approach | 11 | 1.013 | 1.6 | 1.03 | 8.2 / 9.5 |
| 6 (030) | entry corridor | 11 | 1.037 | 1.6 | 0.81 | 2.0 / 2.5 |
| 3 (160) | side door approach | 10 | 1.01 | 0.8 | 1.00 | 4.0 / 4.3 |
| 5 (160) | side door approach | 10 | 0.98 | 0.9 | 1.00 | 4.0 / 4.3 |
| 5 (160) | side queue | 10 | 1.02 | 0.6 | 0.78 | 1.5 / 1.9 |

- Counts stay within 0–4% of the truth in every zone and run, including at 8–9 people per m²
  (mean error 0.6–1.6 people per reading).
- Gate densities match (median ratio 1.00). The true peak, 9.5 per m² at 7.4 s, falls between
  two readings 6 s apart; the highest reading was 8.2 (truth 9.0 at that second).
- The large zones read 22% low, as on 3 Nov: they reach past the four floor points, so their
  area is extrapolated. The report now says so under "what the system did not know".

**Edge flow (the fill clock's input)**, against true crossings of the zone edges in a 2 s window
around each reading (`score_flow.py` in the run's scratch folder, not committed):

| Zone | Net flow per minute, system mean / true mean | Mean error per reading | Same sign |
|---|---|---|---|
| gate approach | −14 / +5 | 74 people/min | 64% |
| entry corridor | −60 / −63 | 35 people/min | 73% |

At the first reading the system saw the surge into the gate (270 in, 30 out per minute; truth
450 in, 90 out), and Spark's forecast from it ("5 per m² in about 1 s, 6 in about 2 s" at
4.0 per m²) was right in kind: the gate went from 3.8 to 9.0 per m² within 6 s. Over the
whole clip the per-reading flow is noisy: optical flow over half a second, projected two
seconds ahead, in a crowd that barely moves. It is an input for Spark, not a trigger, and it
should be read as an order of magnitude.

## The connected flow, run 4 (01:04 UTC, one camera)

| Step | When (UTC) | From the reading |
|---|---|---|
| Spark's setup proposal from the Hindi instruction and the frame (`--setup`) | 20.0 s call | |
| First reading (clip second 1): gate 4.0 per m², edge flow 270 in / 30 out per min | 01:04:05 | 0 s |
| Spark: `critical`, actions tied to the venue (hold *Outer queue barrier*; open *Side door B* → *Hall*; send stewards to *Main gate*) | 01:04:23 | 18.5 s (Spark 12.5 s) |
| Route guard: *Hall* not seen by any camera, flagged on the alert | 01:04:23 | |
| Glimmer had no warm worker: not waited for; stewards notified (escalation chain) | 01:04:24 | 19 s |
| Hindi and English announcements spoken (MMS-TTS), heard back (Voice Transcribe), judged (Spark) | 01:04:41 | 36.8 s |
| Nobody tapped within 45 s: escalated to `safety_officer` | 01:05:10 | |
| `safety_officer` tapped *On my way* (sent through `POST /crowd/alerts/{id}/ack`) | 01:05:12 | |
| Glimmer's late verdict (a worker had cold-started): not confirmed, 0.62, chained as late | 01:06:17 | |

- **Hear-back caught a real error.** The English announcement came back as said (WER 0.08,
  "same meaning"). The Hindi one, "…गेट पर धक्का न दें" (do not push at the gate), was heard
  as "…भीड़ पर धक्का ना दें" (do not push at the crowd), WER 0.31, and Spark judged it a different
  meaning ("Gate changed to crowd"). The page does not play it by itself and the report lists it.
- **Escalation works live**: notify, 46 s of silence, escalate, tap, chain. All seven records of
  the alert (notify, candidate, speak ×2, escalate, ack, and the late verdict) are on the
  evidence chain, which verifies; each line of the report's timeline carries its record number
  and hash.
- **Steward on camera** started looking after the tap, but the clip ended a second later, so
  there was no newer frame to look at (0 looks). Not verified live.
- **Spark's after-action summary** (`narrate=true`) cited chain records for the sentences about
  the alert, the escalation and the announcements, and none for the measurements (they are not
  chain records). It repeated "stewards (acknowledged)" from a report line that showed the shared
  alert status; the report now names who was told and who tapped separately (fixed in `a6e9b34`).

Run 2 (00:45, same clip, earlier build) gave the same picture: `critical` at the first
reading, staff notified 15.4 s after it, announcements ready at 19.5 s (voices preloaded),
escalated to the safety officer after 45 s.

## Glimmer, live (runs 4–6)

Glimmer had no GPU for most of the session: at 00:47 UTC RunPod's capacity read showed the RTX
4090 at "Low" stock with only CUDA 13.2 hosts available, and the endpoint (pinned to US-IL-1,
`ADA_24`, CUDA ≥ 12.8) started no worker in nine minutes of pings. Alerts then went out at once
under the new rule (a cold start outlasts the 20 s budget), each saying so. A worker started at
about 01:06, and from then on every alert was checked:

- Run 4's late verdict and run 5's first gate alert: Glimmer described "very tightly packed
  shoulder-to-shoulder … individual movement is severely restricted … consistent with a
  high-density crush condition", yet answered *not confirmed* (0.62 and 0.68) because it "cannot
  verify more than 5 per m² from the image alone". In run 5 that **held a true critical alert**
  (truth: 3.8 per m² rising to 9.0 within 6 s). The heatmap it was shown carried the grid cells
  and the whole view's density (0.97 per m²) but not the gate zone's.
- Run 5's second gate alert (clip second 31, 6.7 per m²): Glimmer confirmed (0.78); staff told
  34 s after the reading (Spark 20 s, Glimmer 14 s).
- Glimmer took 9–20 s per check (430–570 output tokens at 28–48 tokens/s); one check on the
  side door ran past the 20 s budget, the alert went out unchecked and the verdict was chained
  when it came.

Fixed in `8ae9e60`: a check that does not confirm goes back to Spark with Glimmer's description
and the measurements, and Spark decides to send or hold (a missed crush costs lives, a false
alarm costs trust); the heatmap now outlines each named zone with its people and density.
Run 6 below exercises it.

**Run 6 (01:13 UTC, one camera, build `8ae9e60`, Glimmer warm).** First reading 01:13:55
(gate 4.0 per m²) → Spark `critical` at 01:14:13 (18 s) → Glimmer **confirmed, 0.95, in 6.2 s**
→ stewards notified at 01:14:21, **26 s after the reading** → announcements spoken and heard back
at 01:14:40. Glimmer's answer began "the yellow named zones are visible and the on-image data is
readable": with the zone labels on the heatmap it may be confirming the printed numbers as much
as the people, so the labels help it and also lean on it. The send-or-hold second look did not
come up (Glimmer agreed); it is covered by `tests/crowd/test_warden.py`. Both announcements came
back with WER 0.06 and were judged a different meaning because the door's letter "B" was lost in
speech ("open side door B"): strict, and arguably right for a door name.

## Route guard and two cameras (runs 3 and 5)

Both clips played at once, the venue layout naming *Side door B* as the queue camera's gate.

- Spark planned across the cameras: at the main gate it held the outer barrier and redirected
  the entry corridor to *Side door B*. The route guard read side door B's camera before anything
  went out: 0.0 per m², falling 3.4 per minute, "has room and is not filling" (the side queue
  had emptied by then), so the action stood. *Hall* was marked unmeasured on every alert.
- In runs 3 and 5 Spark never chose a full destination (it saw both cameras' numbers in its
  first prompt). Run 7 below made it happen live.
- Run 3 found starvation: one assessment runs at a time and the side door camera kept the slot,
  so the main gate's first assessment came 47 s after its first reading. Fixed in `41ad757`
  (the camera waiting longest goes first); in run 5 the two cameras alternated, 16–22 s from a
  reading to its assessment.

## Second session: route guard, three-way count, phase meter, steward on camera (runs 7–8)

Build `dfd9aa2`, 01:35–01:43 UTC, Glimmer warm. Run 7 played the side door clip (Jülich 160) and
the main gate clip (Jülich 030) at once, both twice back to back; the main gate's Hindi
instruction said the plan is to divert the corridor to side door B when the gate fills.

**Route guard, live.** At the main gate's first assessment (reading 01:35:28, clip second 1)
Spark followed the operator's plan: *redirect Entry corridor → Side door B*. Before anything went
out, the guard read side door B's camera: 3.75 per m², +2.75 per m² per minute, 50 people a
minute crossing in, "getting denser". Spark was asked again with those numbers and changed its
plan to *hold Outer queue barrier*, *hold Entry corridor*, *send stewards to Main gate*, saying
"Side door B at 3.75 ppm2 and filling at +2.75 per min cannot take redirected flow, so inflow
must be held instead." The same happened at the start of the second pass (01:36:55). The alert
carries the replaced route check. Reading to Spark's final answer: 26 s (two Spark calls);
Glimmer confirmed (0.78); stewards told 35 s after the reading.

**Three-way count of the hottest zone** (SAM 3.1 from the reading; Muse Spark pointing on a
0–1000 grid over the zone cut out of the frame; Muse Glimmer counting the same cut-out; 14–24 s
per count, on its own thread), against the trajectories, first pass only:

| Camera, zone | Clip s | True | SAM 3.1 | Spark points (Spark's own total) | Glimmer |
|---|---|---|---|---|---|
| main gate, gate approach | 13.1 | 32 | 33 | 37 (43) | 33 |
| main gate, gate approach | 55.2 | 6 | 7 | 6 (6) | 6 |
| side door, approach | 1.0 | 10 | 9 | 9 (10) | 9 |
| side door, approach | 43.2 | 7 | 7 | 9 (9) | 6 |

Mean error: SAM 0.8, Glimmer 0.8, Spark 2.0 people (count ratio 1.02, 0.98, 1.11). At the dense
gate Spark's points overshot (37 inside the zone, and it wrote 43); SAM and Glimmer were within
one. The spread (11% at the gate, 33% at the side door at 43 s) goes into Spark's next
assessment as the count's uncertainty, and the page shows Spark's points on the cut-out. Four
scored counts are too few for more than a first impression.

**Phase meter.** Speed over the last three minutes, its stop-and-go swings and the pressure go
to Spark, which named the phase: `turbulence` for the main gate at the critical alert (8.2 per
m², pressure 0.094, nearly stationary), `dense flow` for the side door, `free flow` once the gate
cleared. With readings 6 s apart the swing count is noisy (10 swings in 127 s on the whole view
at the end); it is a context number for Spark, not a measurement of stop-and-go waves.

**Steward on camera.** The Jülich staff wear blue vests: on a frame SAM 3.1 found three "blue
vest" and no "high-visibility vest", so the sample watch looks for `blue vest` in a "gate staff
side" area beside the gate. The main gate alert escalated after 45 s to the safety officer, who
tapped *On my way* at 01:36:52 (scripted through the API); the first look, on the next reading
of the second pass, found 2 blue vests there and chained it. These staff stand at the gate
throughout the experiment, so this shows the camera check working, not a steward arriving.

**Send-or-hold second look: still not seen live.** Glimmer confirmed every alert it checked in
time in runs 6–7. In run 8 (the side door with a deliberately cautious instruction) Spark sent a
`warning`; Glimmer, busy with the three-way counts, answered after 22 s, past the 20 s budget,
so the alert had already gone out unchecked and its late *not confirmed* was chained. Fixed in
`4d1f8ab`: three-way counts wait while an alert's frame check is in flight. Run 9 repeated it
with the fix: Glimmer answered in about 4 s and confirmed (0.85, "tight bunching at the side
door approach"), so the second look did not come up. It is covered by `tests/crowd/test_warden.py`.

**Spark hearing the crowd** (audio) was not attempted: no clip with a real crowd soundtrack was
available, and fetching one from a stock site needs the user's go-ahead.

## Setup from plain words

`POST /crowd/setup` with the Hindi instruction and the newest frame (20–23 s): Spark answered in
Hindi, saw the view as "from above", chose `head` to count, 3 × 3 tiles, Hindi and English,
`stewards` first, and asked for four floor points with their distances because the view is
overhead fisheye. Its two zones were "गेट के सामने भीड़" (the crowd in front of the gate, a
polygon around the packed area) and "गेट का मुंह" (the gate mouth, x 675–840, y 243–410 px),
which is where the gate is. The runs used the sample's measured zones and floor points so that
they could be scored; the proposed zones were not scored.

## Ask over the crowd tables

`POST /ask` "On camera phone-8, when was the gate approach most crowded, how dense did it get,
and for how long was it above 6 people per square metre?" (43 s): Spark queried
`crowd_readings`, re-counted the peak with `crowd_scan`, and answered 8.25 per m² (33 people)
at 01:14:02–01:14:08, above 6 per m² for five readings, about 24–30 s. Truth for the same
seconds: 9.0 per m² at that second (9.5 at the true peak), above 6 per m² from clip second 1.6
to 35.8, 33 s: the 6 s readings start late and end early on it.

## Live page and stream

`/usecases/crowd` showed each run as it happened over `GET /crowd/stream`. The Next.js rewrite
gzipped and buffered the stream until the API sent `Cache-Control: no-transform`; the page also
falls back to polling `GET /crowd/status` when no event arrives for 8 s.

## What is not verified

- A steward arriving on camera: run 7 saw staff who were there all along.
- Spark's send-or-hold second look after Glimmer disagrees in time (unit-tested only).
- Spark hearing the crowd's audio.
- The live API pod: this branch was not deployed.
- The fill clock in a crowd that fills slowly: the Jülich gate run starts dense.
