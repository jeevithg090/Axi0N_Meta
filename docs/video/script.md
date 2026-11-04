# Rulecam: the 3-minute demo video

**Goal.** A judge feels the problem in 10 seconds and trusts the product in 60. The video is
built around the four judging criteria: **Impact** (three real worlds and the people in them),
**Meta tooling** (each model doing a job you can see, and checking another), **Demo** (it works,
on screen), **Technical depth** (Rule CI, verified alerts, evidence you can check).

**Length:** 3:00 exactly. **Voiceover:** 407 words, about 135 words a minute, with room to
breathe and to let the Spanish warning play. The lines alone are in
[`voiceover.txt`](voiceover.txt), timed captions in [`captions.srt`](captions.srt).

`https://preferred-florist-labeled-trace.trycloudflare.com` is the live site's address (a tunnel from the team's Mac; check it answers before recording).

**Rough cut (timing reference).** `docs/video/rough-cut.mp4` is a 3:00 animatic: every shot at
its time, built from the committed screens (`ui/public/screens/`), the proof-portal captures
(`evidence/screenshots/`), the recorded T02 and T04 strip frames and the crowd chart, with a
labelled "TO RECORD" card where a shot still has to be captured. Under it is a scratch voiceover
made with macOS text-to-speech (`docs/video/voiceover-scratch.m4a`). Both are git-ignored (over
the 500 KB pre-commit limit) and rebuilt from this script. Replace the scratch voice with a human
take before submitting.

## Where each judging criterion is scored

| Criterion (weight) | Shots | What the judge should come away with |
|---|---|---|
| Impact (30%) | 1–5, 12, 17, 21–23, 30 | Three real places with real stakes, and the people in them (staff, inspectors, workers), on cameras they already own, in their own language |
| Use of Meta AI tooling (25%) | 7–11, 13–16, 18–20, 23, 27 | Each model doing a visible job and checking another: Spark plans and picks tools, SAM 3.1 measures and counts, Glimmer says yes or no, three models count one frame, MMS-TTS speaks and Voice Transcribe listens back, Muse Code writes and Z3 proves |
| Demo (25%) | 6–11, 13–20, 21–25 | It runs: request to plan to verdict, crowd reading to stewards, question to answer, camera to phone |
| Technical depth (20%) | 7, 13, 15, 18, 24–27 | Rule CI with mutants, counts scored against ground truth, a route guard, a proof pack you can break, a solver proof |

## Honesty rules for the edit (read before cutting)

0. **The `/monitor` screen is SAMPLE DATA** (a scripted walkthrough; its banner says so, see
   `ui/public/screens/04-monitor-light.png`). Never show it as a result.
1. **Every screen shot shows its badge.** The UI header says LIVE or REPLAY. Keep it in frame,
   or burn a corner tag: `LIVE`, `REPLAY (recorded live run, <date>)`, `RESEARCH FOOTAGE`,
   `STOCK FOOTAGE`, `SAMPLE (generated image)` or `SIMULATED`.
2. **Speed-ups are labelled.** Any shot played faster than real time carries the factor ("8x")
   in the corner, and the caption gives the real time.
3. **Numbers on screen come from a report.** Each number below names its report under
   `reports/eval/`. Do not add one that is not there.
4. **Recorded is never called live.** If a live shot fails on the day, use the fallback and
   change its tag to REPLAY. Do not re-voice it as live.
5. **Stand-ins are named on screen.** Glasses mode used a phone page and stock stills, not real
   glasses; the camera connectors used streaming servers and a simulated ONVIF recorder, not a
   real CCTV camera; push was proved in Chromium, not on a real phone. The caption says so.
6. **Real crowd disasters:** no footage, names or images of real crushes or victims. The crowd
   segment uses only the Jülich research experiment, where volunteers walked through a
   bottleneck under controlled conditions.

## Shot list

Times are cumulative. "Do" is the exact on-screen action. Record at 1920x1080, 30 fps, browser
zoom 125%, cursor highlight on. Report links are relative to this file.

### Cold open: the problem (0:00 – 0:10)

| # | Time | Do (on screen) | Source and tag | Voiceover | Caption (burned in) | Fallback |
|---|---|---|---|---|---|---|
| 1 | 0:00 | Full-frame stock clip: the orange forklift drives past the worker in the pink shirt. No UI. | Pexels 32243654; `STOCK FOOTAGE` | "A forklift passes a worker." | — | A still of the same clip |
| 2 | 0:02 | Full-frame Jülich bottleneck clip, people in orange caps pressing toward the gate. | Jülich `030_c_56_h0`; `RESEARCH FOOTAGE` | "A crowd presses toward a gate." | "Jülich bottleneck experiment: volunteers, controlled conditions" | Still |
| 3 | 0:03 | Full-frame checkout clip, queue at the lanes. | Pexels 39221979; `STOCK FOOTAGE` | "A queue backs up." | — | Still |
| 4 | 0:05 | Hard cut to black. White text appears word by word. | Title card | "The cameras saw it all. Nobody was watching." | **The cameras saw it. Nobody was watching.** | — |
| 5 | 0:08 | Logo. | Title card | "This is Rulecam." | **Rulecam** | — |

### World 1: Warehouse (0:10 – 0:50)

| # | Time | Do (on screen) | Source and tag | Voiceover | Caption | Fallback |
|---|---|---|---|---|---|---|
| 6 | 0:10 | `https://preferred-florist-labeled-trace.trycloudflare.com/usecases/warehouse` (or `/planner`): type, or say into the mic, "Mantener a las personas a al menos 4 metros de una carretilla elevadora en movimiento." | LIVE | "A warehouse manager sets one rule, in Spanish: keep people four metres from a moving forklift." | "Any language. Llama Guard 4 screens it first." | `ui/public/screens/09-warehouse-journey.png`; REPLAY |
| 7 | 0:17 | The planner's steps tick by (survey reused, probe, ask, compile), then Rule CI turns green. Zoom on **16/16** and **6/6**. | LIVE, sped up; tag the factor | "Muse Spark plans it, SAM 3.1 measures the floor, and Rule CI attacks it. Sixteen edge cases, six broken copies. All caught." | "Plan ready in <real time>. Rule CI: 16/16 edge cases, 6/6 mutants" ([warehouse, 4 Nov](../../reports/eval/2026-11-04-warehouse-live.md): 42–45 s on a surveyed camera) | `/demo?demo=1` Rule CI section; REPLAY |
| 8 | 0:26 | `09-warehouse-journey` proof card: the verified alert strip, then the 4 Nov run's near misses with Glimmer's verdicts. | REPLAY (recorded live runs 1–4 Nov) | "On live video, Muse Glimmer, Meta's open model on our own GPU, confirmed six near misses out of six." | "4 Nov, live: Glimmer confirmed 6 of 6 near misses, 0.98–0.99" ([warehouse](../../reports/eval/2026-11-04-warehouse-live.md)) | [`hero-same-rule.jpg`](../pitch/media/hero-same-rule.jpg), left half |
| 9 | 0:32 | The "Operator excluded" card: the seated driver's strip and Glimmer's verdict. | REPLAY (T04 golden, 2 Nov) | "And it knew the seated driver was the operator, so nobody was bothered." | "Glimmer: the operator, seated in the cab. 0.05. No alert." ([1 Nov](../../reports/eval/2026-11-01-live-test-campaign.md)) | `hero-same-rule.jpg`, right half |
| 10 | 0:38 | The warning card: Spark's Spanish text; press play on the MMS-TTS WAV and let 4–5 s of it play; the heard-back line and its word-error badge appear. | LIVE or REPLAY, as recorded | "Spark writes the warning in Spanish. Meta's speech model says it. Voice Transcribe listens back, to check it was said right." | "Spoken by Meta MMS-TTS, heard back by Voice Transcribe: word error 0.09–0.18, passed" ([warehouse](../../reports/eval/2026-11-04-warehouse-live.md)) | The WAV over the card still |
| 11 | 0:46 | The OSHA duty card: "refresher training due", the clause, the forklift's unit "K 35" read off the frames. | Same | (the Spanish warning finishes playing) | "OSHA refresher training due: found by Spark's web search" | Cut; give the time to shot 10 |

### World 2: Crowd crush early warning (0:50 – 1:30)

| # | Time | Do (on screen) | Source and tag | Voiceover | Caption | Fallback |
|---|---|---|---|---|---|---|
| 12 | 0:50 | Slow, quiet shot of the Jülich clip at the gate. No UI. Music drops. | `RESEARCH FOOTAGE` | "In a crowd crush, nobody does anything wrong. People are packed too tightly to move." | — | Still |
| 13 | 0:56 | `https://preferred-florist-labeled-trace.trycloudflare.com/usecases/crowd` (`07-crowd-journey`): the Hindi instruction, the zones on the overhead view, the count against the experiment's tracked heads; cut to the density chart. | LIVE, or `REPLAY recorded 4 Nov` as the page shows | "This is real footage from a crowd experiment, with an instruction in Hindi. SAM 3.1's counts stayed within four percent of the truth." | "Counts within 0–4% of the published truth, every zone and run" ([crowd](../../reports/eval/2026-11-04-crowd-live.md)) | [`crowd-density-vs-truth.png`](../pitch/media/crowd-density-vs-truth.png) |
| 14 | 1:05 | Spark's assessment turns red, **critical**. Its first plan, "redirect Entry corridor → Side door B", is struck through by the route guard's reading of door B, and the new plan appears: hold the outer barrier, hold the corridor, stewards to the main gate. | Same (run 7, 4 Nov) | "Spark calls it critical. The plan was to send people to side door B, but the route guard saw that door was filling too, so Spark held the inflow instead." | "Side door B: 3.75 per m² and filling. Inflow held." | The run 7 alert row in the report |
| 15 | 1:17 | Glimmer's verdict chip and the alert timeline: reading, Spark, Glimmer, stewards told. | Same (run 6, 4 Nov) | "In one run, Glimmer confirmed in six seconds, and stewards were told twenty-six seconds after the reading." | "Glimmer 0.95 in 6.2 s · stewards told 26 s after the reading" | Still |
| 16 | 1:24 | The announcement panel: English passed; the Hindi line flagged "different meaning", with what was heard. | Same (run 4, 4 Nov) | "When a Hindi announcement came back saying "crowd" instead of "gate", it was held back." | "Heard back as 'crowd', not 'gate': not played" | Still |

> Glimmer did not check the alerts of every crowd run (no GPU for part of the session). Show
> its verdict only from runs 6–7, as above.

### World 3: Supermarket (1:30 – 2:05)

| # | Time | Do (on screen) | Source and tag | Voiceover | Caption | Fallback |
|---|---|---|---|---|---|---|
| 17 | 1:30 | `https://preferred-florist-labeled-trace.trycloudflare.com/usecases/supermarket` (`11-supermarket-journey`): the owner's question. | LIVE or `REPLAY recorded 4 Nov`, as the page shows | "A shop owner asks: when was the queue longest?" | — | `11-supermarket-journey.png` |
| 18 | 1:34 | The "What one question proved" card: **22** struck through, **10 people**, the three counts side by side (SAM 3.1 15–17 boxes, Spark 9 points, Glimmer 10–11). | Same | "The tracker said twenty-two people. Rulecam counted the same frame three ways, with SAM, Spark and Glimmer, and said ten. By hand: about twelve." | "Tracker 22 · three-way count 10 · by hand about 12" ([supermarket](../../reports/eval/2026-11-04-supermarket-live.md)) | Still |
| 19 | 1:45 | The spill timeline: seen, staff alerted, cleared with a photo. Keep the SAMPLE banner in frame. | Same; `SAMPLE (generated image)` | "It spots a spill, tells the right person, and closes the task only when a photo shows the floor is clean." | "Spill image generated by Muse Image (SAMPLE). Seen to cleared: 1.9 min, checked by photo" | Still |
| 20 | 1:54 | Push-to-talk Ask: the Spanish question plays, the transcript appears word for word, the answer streams in Spanish. | LIVE or REPLAY | "Ask out loud in Spanish, and the answer comes back in Spanish." | "Heard word for word; answered in Spanish in 45 s" | Still |

### The platform and the trust beat (2:05 – 2:50)

| # | Time | Do (on screen) | Source and tag | Voiceover | Caption | Fallback |
|---|---|---|---|---|---|---|
| 21 | 2:05 | `https://preferred-florist-labeled-trace.trycloudflare.com/cameras`: paste an RTSP address (or scan the network), press Connect; the card fills in with Spark's name for the camera and its use-case fit. | LIVE | "Connect the cameras you already have. About thirty seconds to a named, surveyed camera." | "23–33 s from Connect. Tested on streaming servers and a simulated ONVIF recorder" ([connectors](../../reports/eval/2026-11-04-connectors.md)) | Cut to 22 |
| 22 | 2:11 | Phone: `08-staff-phone` (pick Hindi, a role) → `staff-verified-alert` → tap Done, attach a photo (`staff-done-sheet`). | LIVE on a phone, or the screens; tag accordingly | "Staff get the alert on their phone, by role, never by name, in their own language." | "Verified alert pushed 8.9 s after the camera chunk (tested in Chromium)" ([staff app](../../reports/eval/2026-11-04-staff-app.md)) | `ui/public/screens/staff-*.png` |
| 23 | 2:17 | Phone: `10-glasses-phone`, hold to talk, "is this aisle safe to walk down?", the spoken answer plays. | LIVE or REPLAY; caption names the stand-in | "In glasses mode, ask what you're looking at, and hear the answer." | "Phone standing in for the glasses. First sentence ready in a median 8.6 s" ([glasses](../../reports/eval/2026-11-04-glasses.md)) | `10-glasses-phone.png` |
| 24 | 2:22 | Proof portal: open the share link; checks tick green: records, Merkle proofs, numbers, rule gates. | REPLAY (pack of 1 Nov) | "Why trust any of this? You don't have to trust us. An inspector opens a proof pack, and their own browser re-checks every record and every number." | "395 records, 97 numbers, re-checked in the browser in 0.3 s" ([proof portal](../../reports/eval/2026-11-04-proof-portal.md)) | [`evidence/screenshots/proof-portal-verified.jpg`](../../evidence/screenshots/proof-portal-verified.jpg) |
| 25 | 2:33 | The same pack with one number edited on the server: the page turns red. | REPLAY | "Change one number, and it turns red." | "Glimmer rejections: recomputed 33, pack says 23" | [`proof-portal-tampered.jpg`](../../evidence/screenshots/proof-portal-tampered.jpg) |
| 26 | 2:36 | Worker QR page on a phone: the rule card; tap **Dispute**; cut to Rule CI with the new regression test. | LIVE or REPLAY | "Workers can dispute any alert, and a false alarm becomes a permanent test. No face recognition. Ever." | "No face recognition. Ever." | Telegram "False alarm" button flow |
| 27 | 2:43 | `https://preferred-florist-labeled-trace.trycloudflare.com/extensions`: "Alert if a pallet is stacked above the yellow line": the check, four green Z3 proofs, then the alert frame. | REPLAY (4 Nov run) | "Need a new kind of rule? Muse Code writes it, and a solver proves it." | "Z3: 4 of 4 proved · mutants 25 of 26 caught" ([Muse Code](../../reports/eval/2026-11-04-muse-code-live.md)) | [`alert-frame-14.jpg`](../../reports/eval/2026-11-04-muse-code-live/alert-frame-14.jpg) |

### Close (2:50 – 3:00)

| # | Time | Do (on screen) | Source and tag | Voiceover | Caption | Fallback |
|---|---|---|---|---|---|---|
| 28 | 2:50 | Three quick tiles: the forklift verdict, the crowd chart, the 22 → 10 card. Then the landing page. | Title card | "Rulecam. Plain words in. Verified alerts out. On the cameras you already own, in any language." | **Plain words in. Verified alerts out.** | `01-landing-light.png` |
| 29 | 2:57 | URL and model credits. Hold. | Title card | (silence) | **github.com/jeevithg090/Axi0N_Meta · https://preferred-florist-labeled-trace.trycloudflare.com** · "Muse Spark · SAM 3.1 · Muse Glimmer · Muse Spark Open · Muse Code · Voice Transcribe · Llama Guard 4 · MMS-TTS" | — |

## What to record before editing (checklist)

- [ ] **Stock and research clips** (shots 1–3, 12): Pexels 32243654, Pexels 39221979, Jülich
      `small-030_c_56_h0.mp4`. Confirm the Jülich archive's licence allows video use and add the
      attribution it asks for in the end card (the crowd page credits "Forschungszentrum Jülich
      data archive").
- [ ] **Warm the stack first:** `docs/demo-day.md` T-60 (`scripts/ops/demo_warm.sh`); Spark Open
      needs about 18 minutes, so start it early if it appears in the shot.
- [ ] **Warehouse** (6–11): plan once on the stage camera beforehand so "Plan this rule" reuses
      the survey (about 40–50 s), then record the real plan and speed it up in the edit.
- [ ] **Crowd** (13–16): feed the Jülich clips with `scripts/crowd/feed_clip.py` and record
      `/usecases/crowd`; or use the recorded 4 Nov runs the page replays, tagged REPLAY.
- [ ] **Supermarket** (17–20): the journey page, the Ask timeline and the push-to-talk question.
- [ ] **Platform** (21–27): `/cameras` with a test RTSP stream; a phone for `/staff` and
      `/glasses`; the proof portal share link; the worker page; `/extensions`.
- [ ] **Voiceover**: one take per segment from [`voiceover.txt`](voiceover.txt); captions from
      [`captions.srt`](captions.srt) (retime after the final take).
- [ ] **Music**: quiet, no lyrics; drop it under the crowd segment (shots 12–16).

## If something fails on recording day

| Problem | Do |
|---|---|
| Planner slow or a hosted model errors | Use `09-warehouse-journey` and `/demo?demo=1`, tag REPLAY |
| Glimmer cold or down | Use the recorded verdicts on the journey pages (REPLAY); never stage a verdict |
| No crowd GPU | Use the recorded 4 Nov runs on `/usecases/crowd`, tagged REPLAY |
| Ask gives a wrong or slow answer | Show the recorded 4 Nov answer (REPLAY), or show the live one as it is if it is honest about its limits |
| Spark Open cold | Leave it out of the shot; the video does not need it on screen |
| Live site not reachable | Record from `npm run dev` against the live API; the header badge still tells the truth |

## Export

- H.264 MP4, 1920x1080, 30 fps, AAC 48 kHz, burned-in captions (English; the Spanish and Hindi
  lines are captioned in English too).
- Exactly 3:00 or under. Upload where it plays without a login; check it in a private window.
- A 30-second vertical cut for X and Reels is in [`../pitch/launch.md`](../pitch/launch.md).
