# Rulecam launch: X thread, LinkedIn post, 30-second vertical cut

For the **Most Viral** prize: one X post is linked in the submission form, and engagement is
counted until Nov 8 (from the team's notes; unverified). Post it right after submitting. This
replaces posts 2 and 3 in [`docs/social.md`](../social.md), whose drafts mention features that
are not live (Sapiens, the skeleton wipe, the interlock).

**Rules for every post:** every number links back to a committed report (sources listed under
each post). Never say "first". Never call a replay live. No real crowd disaster as a hook. No
emoji walls.

**Before posting, confirm:** the hackathon's official handle and hashtag on the Cerebral Valley
event page (the drafts below use `@AIatMeta` as a placeholder), the video link, and that
rulecam.vercel.app is redeployed.

---

## X thread

Attach the **30-second vertical cut** (below) as native video to post 1. Put links in the first
reply, not in post 1, so the post itself travels.

**1/ (the post linked in the form)**

> Same forklift rule. Two clips.
>
> Left: a worker walks inside 4 m. Meta's Muse Glimmer checks the frames: violation, 0.99.
>
> Right: the rule flags the seated driver. Glimmer: "the forklift operator seated inside the cabin." 0.05. No alert.
>
> This is Rulecam. Thread:

**2/**

> Rulecam turns the cameras you already own into a helper you set up in plain words, in any language.
>
> It says what the camera can't check. It attacks every rule before it goes live. A second Meta model checks every alert. And you can verify the evidence in your own browser.

**3/ Warehouse**

> The rule was typed in Spanish.
>
> Before anyone could approve it, Rule CI attacked it: 16 of 16 edge cases passed, 6 of 6 broken copies caught.
>
> Live on real video: 0.99 on the worker, 0.05 on the driver, 19 s from event to verified decision.

**4/ Crowd crush early warning**

> In a crowd crush, nobody does anything wrong. People are packed too tightly to move.
>
> On real footage from a Jülich crowd experiment, SAM 3.1 counted 95–99% of the true heads. Spark called it critical and wrote calm announcements in Hindi and English. Alert in about 40 s.

**5/ Supermarket**

> A shop owner asks: "Which checkout lane was busier?"
>
> Muse Spark picks its own tools (SQL over SAM 3.1 tracks, SAM counts, Glimmer looks) and answers.
>
> On real checkout video: 13 of 13 answered, 10 fully right. On hand-labelled CCTV its counts are weak: 7 of 36. We publish both.

**6/ Trust**

> Every event, verdict and action goes into a hash chain. Change one byte and the verify page breaks.
>
> Workers see every rule and can dispute any alert. A false alarm becomes a permanent test.
>
> No face recognition. Ever.

**7/ Built on Meta's models**

> Muse Spark plans and reasons. SAM 3.1 sees and counts. Muse Glimmer, open weights on our own GPU, checks every alert. Voice Transcribe listens, MMS-TTS speaks. Muse Code writes new rule types, and a Z3 solver proves them.
>
> Built in 10 days for the @AIatMeta hackathon.

**First reply (links)**

> Try the recorded run (no login) and press "Verify it in your browser": https://rulecam.vercel.app/demo?demo=1
>
> Code and every measured number: https://github.com/jeevithg090/Axi0N_Meta
>
> 3-minute video: TODO

Sources: posts 1 and 3, [live campaign](../../reports/eval/2026-11-01-live-test-campaign.md)
and [`scenarios/warehouse_driver.expected.json`](../../scenarios/warehouse_driver.expected.json);
post 4, [crowd run](../../reports/eval/2026-11-03-crowd-live.md); post 5,
[supermarket run](../../reports/eval/2026-11-01-supermarket-ask.md) and the
[Ask benchmark](../../reports/eval/ask/2026-11-02T230520Z-corridor.json); post 1's quote is from
the T04 golden on branch `p1/uc-warehouse` (`scenarios/driver_only_4m.expected.json`).

**Team quote-posts (within 30 minutes):** each person quote-posts post 1 with one sentence about
the part they built, in their own words. No numbers that are not in the thread.

**Replies during the vote window:**

- Asked for a custom rule: run it through the planner, reply with the rule card or the declined
  card, and say whether it was live or replayed.
- Asked about privacy: "No face recognition. Faces are blurred in evidence by default. Our
  hackathon deployment runs a less strict prototype mode, and the README says exactly what it
  changes."
- Asked whether it is staged: point to the verify page and the reports; say which footage is
  stock, research or generated.

---

## LinkedIn post

> **Cameras see everything. Nobody is watching.**
>
> For the Meta Global AI Developer Hackathon, our team of four built Rulecam: you tell your cameras what worries you, in plain words and in your own language, and Rulecam proves each rule before it trusts it.
>
> One engine, three worlds:
>
> - **Warehouse.** A forklift rule typed in Spanish. Rule CI attacked it before anyone could approve it (16 of 16 edge cases, 6 of 6 broken copies caught). On real video, Meta's open-weight Muse Glimmer confirmed a worker inside 4 m (0.99) and rejected the seated driver the rule engine had flagged (0.05).
> - **Crowd crush early warning.** On real footage from a Jülich crowd experiment, SAM 3.1 counted 95–99% of the true heads, and an alert with calm announcements in Hindi and English went out about 40 seconds after the reading.
> - **Supermarket.** The owner asks anything. Muse Spark picks its own tools and answers: 13 of 13 questions on real checkout video, 10 fully right. On hand-labelled CCTV its counts are still weak (7 of 36), and we publish that too.
>
> What we care about most is trust. Every alert is checked by a second model. Every event goes into a hash chain that you can verify in your own browser. Workers can read every rule and dispute any alert. No face recognition, ever.
>
> Try the recorded run, no login: https://rulecam.vercel.app/demo?demo=1
> Code and every measured number: https://github.com/jeevithg090/Axi0N_Meta
>
> Built with Muse Spark, SAM 3.1, Muse Glimmer, Muse Code, Voice Transcribe and MMS-TTS.
>
> Team: Jeevith, Nava, Raunak, Aris (tag each person once names are confirmed).

---

## 30-second vertical cut (1080x1920, for X, Reels and Shorts)

A first draft is rendered at `docs/video/vertical-30s.mp4` (git-ignored, in the `p1/pitch`
worktree): shots 1–5 and 7–8 from the recorded runs, the crowd chart and the verify page; shot 6
is a text card of the 1 Nov Ask answer until a screen recording of `/ask` exists. It has no
voice; add music before posting.

Most people watch muted, so the text carries the story. Big captions in the top third; the UI or
footage in the middle; the LIVE / REPLAY / RESEARCH FOOTAGE tag in the top-right corner of every
shot. Music only, no voiceover (or the voiceover lines in brackets, if recorded).

| # | Time | On screen | Big caption | Tag | Source |
|---|---|---|---|---|---|
| 1 | 0:00–0:03 | T02 strip, vertical crop on the worker and the forklift, slow push-in | **Same rule. Two clips.** | REPLAY | T02 golden strip (`docs/pitch/media/hero-same-rule.jpg`, left) |
| 2 | 0:03–0:07 | The red "violation 0.99" verdict card slides in | **Worker inside 4 m. Glimmer: violation, 0.99** | REPLAY | Live campaign T02 |
| 3 | 0:07–0:11 | T04 strip, crop on the seated driver; green card "0.05" | **Seated driver. Glimmer: "the operator". No alert.** | REPLAY | T04 golden |
| 4 | 0:11–0:15 | Rule CI panel, zoom on 16/16 and 6/6 | **Before it goes live, we attack the rule** | REPLAY | `/demo?demo=1` |
| 5 | 0:15–0:20 | Jülich clip with the density heatmap; the counter climbs | **Crowd at a gate: 95–99% of heads counted** | RESEARCH FOOTAGE | Crowd run, 3 Nov |
| 6 | 0:20–0:24 | `/ask` timeline streaming tools, then the answer card | **"Which lane was busier?" It picks its own tools** | LIVE or REPLAY, as recorded | Supermarket run |
| 7 | 0:24–0:27 | Verify page green, then red after one byte changes | **Change one byte. It breaks.** | REPLAY | `/verify` |
| 8 | 0:27–0:30 | Logo, URL | **Rulecam. Plain words in. Verified alerts out.** rulecam.vercel.app/demo | — | End card |

Export: H.264 MP4, 1080x1920, 30 fps, under 30 s, captions burned in, safe margins of 150 px at
the top and bottom for app UI.
