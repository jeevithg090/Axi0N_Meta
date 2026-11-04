# Rulecam: the 3-minute demo video

**Goal.** A judge feels the problem in 10 seconds and trusts the product in 60. The video is
built around the four judging criteria: **Impact** (three real worlds), **Meta tooling** (each
model doing a job you can see, and what surprised us), **Demo** (it works, on screen),
**Technical depth** (Rule CI, verified alerts, evidence you can check).

**Length:** 3:00 exactly. **Voiceover:** 391 words, about 130 words a minute, with room to
breathe. The lines alone are in [`voiceover.txt`](voiceover.txt), timed captions in
[`captions.srt`](captions.srt).

**Rough cut (timing reference).** `docs/video/rough-cut.mp4` is a 3:00 animatic: every shot at
its time, the real T02 and T04 strip frames for shots 9 and 10, the crowd chart
([`../pitch/media/crowd-density-vs-truth.png`](../pitch/media/crowd-density-vs-truth.png),
drawn from the 3 Nov report) for shot 14, the old verify-page captures for 22 and 23, and a
labelled "TO RECORD" card for every shot still to capture. Under it is a scratch voiceover made
with macOS text-to-speech (`docs/video/voiceover-scratch.m4a`). Both files are git-ignored
(over the 500 KB pre-commit limit); they sit in the `p1/pitch` worktree and are rebuilt from the
script, not edited by hand. Replace the scratch voice with a human take before submitting.

## Where each judging criterion is scored

| Criterion (weight) | Shots | What the judge should come away with |
|---|---|---|
| Impact (30%) | 1–5, 12, 17, 26 | Three real places with real stakes, on cameras they already own, in their own language |
| Use of Meta AI tooling (25%) | 7, 9–10, 14–15, 18–20, 25 | Each model doing a visible job, and checking another: Spark plans and picks tools, SAM 3.1 measures and counts, Glimmer says no to the driver, MMS-TTS speaks, Muse Code writes and Z3 proves |
| Demo (25%) | 6–11, 13–16, 17–18, 22–24 | It runs: typed request to plan to verdict, crowd reading to alert, question to answer, evidence to verify |
| Technical depth (20%) | 8, 14, 22–23, 25 | Rule CI with mutants, numbers scored against ground truth, a hash chain you can break, a solver proof |

## Honesty rules for the edit (read before cutting)

1. **Every screen shot shows its badge.** The UI header says LIVE or REPLAY. Keep it in frame,
   or burn a corner tag: `LIVE`, `REPLAY (recorded live run, <date>)`, `RESEARCH FOOTAGE`,
   `GENERATED TEST IMAGE` or `SIMULATED`.
2. **Speed-ups are labelled.** Any shot played faster than real time carries "8x" (or the real
   factor) in the corner, and the caption gives the real time (for example "plan ready in
   2 min 14 s").
3. **Numbers on screen come from a report.** Each number below names its source. Do not add one
   that is not in `reports/eval/` or `eval/RESULTS.md`.
4. **Recorded is never called live.** If a live shot fails on the day, use the fallback and
   change its tag to REPLAY. Do not re-voice it as live.
5. **Real crowd disasters:** no footage, names or images of real crushes or victims. The crowd
   segment uses only the Jülich research experiment, where volunteers walked through a
   bottleneck under controlled conditions.

## Shot list

Times are cumulative. "Do" is the exact on-screen action. Record at 1920x1080, 30 fps, browser
zoom 125%, cursor highlight on.

### Cold open: the problem (0:00 – 0:12)

| # | Time | Do (on screen) | Source and tag | Voiceover | Caption (burned in) | Fallback |
|---|---|---|---|---|---|---|
| 1 | 0:00 | Full-frame stock clip: the orange forklift drives past the worker in the pink shirt. No UI. | Pexels 32243654 (`eval/clips/pexels_32243654`); tag `STOCK FOOTAGE` | "A forklift passes a worker." | — | Any frame of the same clip |
| 2 | 0:02 | Full-frame Jülich bottleneck clip, people in orange caps pressing toward the gate. | Jülich `030_c_56_h0` (research experiment); tag `RESEARCH FOOTAGE` | "A crowd presses toward a gate." | "Jülich bottleneck experiment: volunteers, controlled conditions" | A still from the same clip |
| 3 | 0:04 | Full-frame checkout clip, queue at the lanes. | Pexels 39221979; tag `STOCK FOOTAGE` | "A queue backs up." | — | Still |
| 4 | 0:06 | Hard cut to black. White text appears word by word. | Title card | "The cameras saw it all. Nobody was watching." | **The cameras saw it. Nobody was watching.** | — |
| 5 | 0:10 | Logo, then the promise. | Title card | "This is Rulecam." | **Rulecam: tell your cameras what worries you. Then check its work.** | — |

### World 1: Warehouse (0:12 – 0:55)

| # | Time | Do (on screen) | Source and tag | Voiceover | Caption | Fallback |
|---|---|---|---|---|---|---|
| 6 | 0:12 | `/planner`. Type, in Spanish: "Mantener a las personas a al menos 4 metros de una carretilla elevadora en movimiento." Press Enter. (If voice works, say it into the mic instead.) | LIVE (header badge) | "A warehouse manager sets one rule, in Spanish: keep people four metres from a moving forklift." | "Typed in Spanish. Any language works." | `/demo?demo=1`, request panel; tag REPLAY |
| 7 | 0:19 | The planner steps tick by: survey, probe, calibrate, compile. Hover the feasibility line. | LIVE, sped up; tag `8x` | "Muse Spark plans it. SAM 3.1 finds the forklift and measures the floor in metres. What it can't see, it says." | "Plan ready in <real time> (sped up 8x)". Source: [campaign](../../reports/eval/2026-11-01-live-test-campaign.md), request to plan 112–224 s | `/demo?demo=1` plan section; REPLAY |
| 8 | 0:28 | Rule CI panel: boundary traces and mutants turn green. Zoom on **16/16** and **6/6**. | REPLAY of the T02 golden (`worker_passes_4m`), so the rule on screen is the same 4 m rule; it reports the same boundary 16/16, mutations 6/6 as `warehouse_driver` (`/demo?demo=1` shows the 2 m rule) | "Before anyone approves, Rule CI attacks the rule. Sixteen edge cases, six broken copies. All caught." | "Rule CI: boundary 16/16, mutants 6/6". Source: [`scenarios/warehouse_driver.expected.json`](../../scenarios/warehouse_driver.expected.json) | Still of the same panel |
| 9 | 0:35 | Split screen: T02 strip on the left, T04 strip on the right (the README hero). Left verdict card animates in: **violation 0.99**. | REPLAY (T02 golden, recorded live 2 Nov) | "Now it fires twice. A worker walks inside four metres. Muse Glimmer, Meta's open model on our own GPU, checks the frames: violation." | "Muse Glimmer: violation, 0.99". Source: [campaign](../../reports/eval/2026-11-01-live-test-campaign.md) | [`docs/pitch/media/hero-same-rule.jpg`](../pitch/media/hero-same-rule.jpg) with a slow push-in |
| 10 | 0:45 | Right verdict card: **no violation 0.05**, with Glimmer's reason highlighted: "the forklift operator seated inside the cabin". | REPLAY (T04 golden, recorded live 2 Nov) | "Then the rule flags the driver. Glimmer: that's the operator, in the cab. No alert." | "Glimmer: the operator, seated in the cab. 0.05. Nobody bothered." | Same still |
| 11 | 0:51 | `/monitor` action log: "would play the warning (es)", then the Spanish text of the warning. | LIVE or REPLAY, tag as shown | "The warning goes out in Spanish." | "Warning queued in Spanish (dry-run in this deployment)" | `/demo` action row; REPLAY |

> If the Spanish warning is wired to a speaker by recording day, play the real audio in shot 11
> and drop "(dry-run in this deployment)". Otherwise keep it.

### World 2: Crowd crush early warning (0:55 – 1:38)

| # | Time | Do (on screen) | Source and tag | Voiceover | Caption | Fallback |
|---|---|---|---|---|---|---|
| 12 | 0:55 | Slow, quiet shot of the Jülich clip at the gate, no UI. | `RESEARCH FOOTAGE` | "In a crowd crush, nobody does anything wrong. People are packed too tightly to move. From the ground, no one sees it building." | — | Still |
| 13 | 1:05 | The crowd watch setup: the Hindi instruction in the watch form, the four floor points and the two zones on the frame. | LIVE (`/rulecam-api/crowd/live`) or the 3 Nov run's screenshots; tag accordingly | "This is real footage from a crowd experiment, played live into Rulecam, with an instruction in Hindi." | "Instruction in Hindi. Zones: gate approach, entry corridor." | Screenshot from the 3 Nov run; tag `REPLAY (recorded live run, 3 Nov)` |
| 14 | 1:12 | Heatmap over the frame; the gate-zone counter climbs; the density chart rises past 7 people per m². | Same | "SAM 3.1 counts heads in each zone: ninety-five to ninety-nine percent of the true count. At the gate, eight people per square metre. The truth: eight." | "SAM 3.1: 95–99% of true heads. Gate: 8.0 per m² (truth 8.0)". Source: [crowd report](../../reports/eval/2026-11-03-crowd-live.md) | [`crowd-density-vs-truth.png`](../pitch/media/crowd-density-vs-truth.png), drawn from the report's table |
| 15 | 1:23 | Spark's assessment card turns red: **critical**. Its staff actions appear: stop the inflow, close the gate. The Hindi and English announcement texts appear, and the Hindi WAV plays for 3 seconds. | Same | "Muse Spark calls it critical, tells stewards to stop the inflow, and writes a calm announcement in Hindi and English." | "Spark: critical. Announcements spoken by Meta's MMS-TTS." | The WAV from `/crowd/files/<name>.wav` over the still |
| 16 | 1:32 | Alert row with its timestamps: reading, assessment, alert sent. | Same | "Reading to alert: about forty seconds. A slow check never holds a warning back." | "Reading to alert: about 40 s. Glimmer gets 20 s to check; past that the warning goes anyway." | Still |

> In the 3 Nov run Glimmer had no GPU, so the alerts went out unchecked after 20 s. Do not show
> a Glimmer verdict in this segment unless a new crowd run with a live Glimmer check is
> recorded and reported.

### World 3: Supermarket (1:38 – 2:12)

| # | Time | Do (on screen) | Source and tag | Voiceover | Caption | Fallback |
|---|---|---|---|---|---|---|
| 17 | 1:38 | `/ask`. Type: "Which checkout lane was busier, left or right?" Press Enter. | LIVE | "A shop owner doesn't want rules. They want answers." | — | Ask timeline screenshot from the 1 Nov run; REPLAY |
| 18 | 1:42 | The Ask timeline streams: `sql`, `count_objects`, `look` with numbered boxes on the frame, then the answer card. | LIVE, sped up if needed; tag the factor | "Muse Spark picks its own tools: SQL over SAM's tracks, SAM counts, Glimmer looking at frames. It splits the view into two lanes by itself, and answers." | "Spark chose: SQL, SAM 3.1 count, Glimmer look". Source: [supermarket report](../../reports/eval/2026-11-01-supermarket-ask.md) | Same as 17 |
| 19 | 1:54 | `/site`, shelf scan: the aisle image with Spark's gap boxes, then SAM's product check keeping 5 of them. | `GENERATED TEST IMAGE` (Muse Image); the scan itself ran live | "On shelves, Spark spots the gaps and SAM checks no product is there." | "Test image generated with Muse Image" | Screenshot from the 3 Nov report |
| 20 | 2:00 | `/site`, context: the food-recall items with their source links. | LIVE or REPLAY, tag as shown | "It reads local food recalls, and keeps only items it can link to a real page." | "Every item links to a page the search returned" | Screenshot |
| 21 | 2:07 | Terminal: `uv run rulecam chain demo chains/kitchen-basics`, the store scorecard prints with SIMULATED. | `SIMULATED` | "And one rule pack can run in every store." | "Chain Mode. Numbers here are simulated." | Still of the output |

### The trust beat (2:12 – 2:50)

| # | Time | Do (on screen) | Source and tag | Voiceover | Caption | Fallback |
|---|---|---|---|---|---|---|
| 22 | 2:12 | `/demo?demo=1`: click **Verify it in your browser**. The page shows the chain intact and the Merkle root. | REPLAY | "Why trust any of this? You don't have to trust us. Every event, verdict and action goes into a hash chain, and your own browser recomputes every fingerprint." | "Your browser recomputes every SHA-256" | [`evidence/screenshots/P3-07b-verify-intact.jpg`](../../evidence/screenshots/P3-07b-verify-intact.jpg) |
| 23 | 2:24 | Tamper: change one record, reload; the page turns red and names the broken record. | REPLAY | "Change one byte, and it breaks." | "Tampered record found" | [`evidence/screenshots/P3-07b-verify-broken-117.jpg`](../../evidence/screenshots/P3-07b-verify-broken-117.jpg) |
| 24 | 2:28 | Worker QR page on a phone: the rule card; tap **Dispute**; cut to Rule CI with a new regression test added. | LIVE or REPLAY, tag as shown | "Workers see every rule on a poster. One tap disputes an alert, and a false alarm becomes a permanent test. No face recognition. Ever." | "No face recognition. Ever." | Telegram "False alarm" button flow |
| 25 | 2:40 | `/extensions`: a Muse Code check with its Z3 proof results (four green properties). | LIVE or REPLAY, tag as shown | "Need a rule type we don't have? Muse Code writes it, and a solver proves it never alerts without its target." | "Muse Code writes the check; the Z3 solver proves it" | Cut this shot; give its time to shot 24 |

### Close (2:50 – 3:00)

| # | Time | Do (on screen) | Source and tag | Voiceover | Caption | Fallback |
|---|---|---|---|---|---|---|
| 26 | 2:50 | Three quick tiles: the forklift verdict, the crowd density chart, the Ask answer. Then the logo. | Title card | "Rulecam. Plain words in. Proof out. On the cameras you already own, in any language." | **Plain words in. Proof out.** | — |
| 27 | 2:57 | URL and model credits. Hold. | Title card | (silence) | **github.com/jeevithg090/Axi0N_Meta · rulecam.vercel.app/demo** · "Muse Spark · SAM 3.1 · Muse Glimmer · Muse Code · Voice Transcribe · MMS-TTS" | — |

## What to record before editing (checklist)

- [ ] **Stock and research clips** (shots 1–3, 12): Pexels 32243654, Pexels 39221979, Jülich
      `small-030_c_56_h0.mp4`. Confirm the Jülich archive's licence allows video use and add the
      attribution it asks for in the end card.
- [ ] **Planner, live** (shots 6–7): warm the GPUs first (`scripts/ops/warm.sh on`, see
      `docs/DEMO.md` T-60). Record the full planner run once at real time; speed it up in the edit.
- [ ] **Golden replays** (shots 8–10, 22–23): `/demo?demo=1` and the T02 and T04 goldens. If the
      T02/T04 goldens are not on the deployed site yet, record them from `npm run dev` on the
      branch that has them (`p1/uc-warehouse`), and keep the REPLAY badge in frame.
- [ ] **Crowd** (shots 13–16): a fresh live run on the Jülich clip if the GPUs are up
      (`/workspace/crowd/live_run.sh` on the API pod, absolute paths), recording `/crowd/live`.
      Otherwise use the 3 Nov run's heatmaps and WAVs, tagged REPLAY.
- [ ] **Ask** (shots 17–18): the checkout camera must have recorded footage; ask the question
      twice and keep the better-looking run, but caption the answer it actually gave.
- [ ] **Site tools** (shots 19–20): needs the redeployed UI (`/site` was 404 at 01:00 UTC 4 Nov).
- [ ] **Worker dispute and Muse Code** (shots 24–25): needs `/worker` and `/extensions` deployed.
- [ ] **Voiceover**: one take per segment from [`voiceover.txt`](voiceover.txt); burned-in captions from [`captions.srt`](captions.srt) (retime after the final VO). A scratch track
      can be made with `say -v Samantha -f docs/video/voiceover.txt -o vo.aiff`.
- [ ] **Music**: quiet, no lyrics; drop it under the crowd segment (shots 12–16).

## If something fails on recording day

| Problem | Do |
|---|---|
| Planner slow or a hosted model errors | Use the `/demo?demo=1` plan section, tag REPLAY |
| Glimmer cold or down | Use the T02/T04 golden replays (shots 9–10 are REPLAY anyway) |
| No crowd GPU | Use the 3 Nov run's heatmaps, alert rows and WAVs, tag `REPLAY (recorded live run, 3 Nov)` |
| Ask gives a wrong or slow answer | Show it as it is if it is honest about its limits; otherwise use the 1 Nov run's screenshots, tagged REPLAY |
| Vercel still missing `/site`, `/worker`, `/extensions` | Record from `npm run dev` against the live API; the header badge still tells the truth |

## Export

- H.264 MP4, 1920x1080, 30 fps, AAC 48 kHz, burned-in captions (English; the Spanish and Hindi
  lines are captioned in English too).
- Exactly 3:00 or under. Upload where it plays without a login; check it in a private window.
- A 30-second vertical cut for X and Reels is in [`../pitch/launch.md`](../pitch/launch.md).
