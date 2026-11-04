# Rulecam launch: X thread, LinkedIn post, 30-second vertical cut

For the **Most Viral** prize: one X post is linked in the submission form, and engagement is
counted until Nov 8 (from the team's notes; unverified). Post it right after submitting. This
replaces posts 2 and 3 in [`docs/social.md`](../social.md), whose drafts mention features that
are not live (Sapiens, the skeleton wipe, the interlock).

**Rules for every post:** every number links back to a committed report (sources listed under
each post). Never say "first". Never call a replay live. No real crowd disaster as a hook. No
emoji walls.

**Before posting, confirm:** the hackathon's official handle and hashtag on the Cerebral Valley
event page (the drafts below use `@AIatMeta` as a placeholder), the video link, and the live
site address that replaces `{{LIVE_UI_URL}}`.

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

> The rule was given in Spanish. Rule CI attacked it first: 16 of 16 edge cases, 6 of 6 broken copies caught.
>
> Live, Glimmer confirmed 6 of 6 near misses. Spark wrote the warning in Spanish, Meta's MMS-TTS spoke it, and Voice Transcribe listened back to check it.

**4/ Crowd crush early warning**

> In a crowd crush, nobody does anything wrong. People are packed too tightly to move.
>
> On real footage from a Jülich crowd experiment, counts stayed within 4% of the truth. When the plan said to send people to side door B, a route guard saw it filling, so Spark held the inflow.

**5/ Supermarket**

> On a busy checkout frame the tracker said 22 people.
>
> SAM 3.1, Muse Spark and Glimmer counted the same frame three ways, none seeing the others: 10. By hand: about 12.
>
> And a spill is closed only when a photo shows the floor is clean.

**6/ Trust**

> An inspector opens a proof pack, and their own browser re-checks 395 records and 97 numbers in under half a second. Edit one number and it turns red.
>
> Staff get alerts by role, never by name. No face recognition. Ever.

**7/ Built on Meta's models**

> Muse Spark plans. SAM 3.1 sees and counts. Glimmer and Spark Open, open weights on our own GPUs, check alerts. Llama Guard 4 refuses rules about who people are. Muse Code writes rule types; Z3 proves them.
>
> Built in 10 days for the @AIatMeta hackathon.

**First reply (links)**

> Try the recorded run (no login) and press "Verify it in your browser": {{LIVE_UI_URL}}/demo?demo=1
>
> Code and every measured number: https://github.com/jeevithg090/Axi0N_Meta
>
> 3-minute video: TODO

Sources: post 1, the [1 Nov campaign](../../reports/eval/2026-11-01-live-test-campaign.md) and
the T04 golden (`scenarios/driver_only_4m.expected.json`); post 3,
[warehouse, 4 Nov](../../reports/eval/2026-11-04-warehouse-live.md); post 4,
[crowd, 4 Nov](../../reports/eval/2026-11-04-crowd-live.md); post 5,
[supermarket, 4 Nov](../../reports/eval/2026-11-04-supermarket-live.md); post 6,
[proof portal](../../reports/eval/2026-11-04-proof-portal.md) and
[staff app](../../reports/eval/2026-11-04-staff-app.md); post 7,
[`docs/demo-day.md`](../demo-day.md) and the
[Muse Code run](../../reports/eval/2026-11-04-muse-code-live.md).

**Team quote-posts (within 30 minutes):** each person quote-posts post 1 with one sentence about
the part they built, in their own words. No numbers that are not in the thread.

**Replies during the vote window:**

- Asked for a custom rule: run it through the planner, reply with the rule card or the declined
  card, and say whether it was live or replayed.
- Asked about privacy: "No face recognition. Alerts go to roles, never names. Faces are blurred
  in evidence by default. Our hackathon deployment runs a less strict prototype mode, and the
  README says exactly what it changes."
- Asked whether it is staged: point to the verify page, the proof portal and the reports; say
  which footage is stock, research or generated (SAMPLE).
- Asked about real cameras or glasses: "Not yet on a real CCTV device or real glasses; the
  reports say what stood in."

---

## LinkedIn post

> **Cameras see everything. Nobody is watching.**
>
> For the Meta Global AI Developer Hackathon, our team of four built Rulecam: you tell your cameras what worries you, in plain words and in your own language, and Rulecam proves each rule before it trusts it.
>
> One engine, three worlds:
>
> - **Warehouse.** A forklift rule given in Spanish. Rule CI attacked it before anyone could approve it (16 of 16 edge cases, 6 of 6 broken copies caught). Live, Meta's open-weight Muse Glimmer confirmed 6 of 6 near misses, and earlier rejected the seated driver the rule engine had flagged. The Spanish warning, spoken by Meta's MMS-TTS, was heard back by Voice Transcribe to check it was said right.
> - **Crowd crush early warning.** On real footage from a Jülich crowd experiment, counts stayed within 0–4% of the truth. A route guard stopped a plan to send people toward a door that was already filling, and listening back caught a Hindi announcement that said "crowd" instead of "gate".
> - **Supermarket.** The owner asks anything, by voice, in their language. On a busy frame where the tracker said 22 people, three Meta models counting the same frame said 10; by hand, about 12. A spill is closed only when a photo shows the floor is clean.
>
> What we care about most is trust. Every alert is checked by a second model. An inspector's own browser re-checks every record and number in a proof pack. Staff get alerts by role, never by name. Workers can dispute any alert. No face recognition, ever.
>
> Try the recorded run, no login: {{LIVE_UI_URL}}/demo?demo=1
> Code and every measured number: https://github.com/jeevithg090/Axi0N_Meta
>
> Built with Muse Spark, SAM 3.1, Muse Glimmer, Muse Spark Open, Muse Code, Voice Transcribe, Llama Guard 4, Prompt Guard 2 and MMS-TTS.
>
> Team: Jeevith, Nava, Raunak, Aris (tag each person once names are confirmed).

---

## 30-second vertical cut (1080x1920, for X, Reels and Shorts)

A first draft is rendered at `docs/video/vertical-30s.mp4` (git-ignored, in the `p1/pitch`
worktree) from the recorded runs, the committed screens and the proof-portal captures. It has
no voice; add music before posting.

Most people watch muted, so the text carries the story. Big captions in the top third; the UI or
footage in the middle; the LIVE / REPLAY / RESEARCH FOOTAGE / SAMPLE tag in the top-right corner
of every shot.

| # | Time | On screen | Big caption | Tag | Source |
|---|---|---|---|---|---|
| 1 | 0:00–0:03 | T02 strip, vertical crop on the worker and the forklift, slow push-in | **Same rule. Two clips.** | REPLAY | T02 golden strip |
| 2 | 0:03–0:07 | The red "violation 0.99" verdict card slides in | **Worker inside 4 m. Glimmer: violation, 0.99** | REPLAY | 1 Nov campaign, T02 |
| 3 | 0:07–0:11 | T04 strip, crop on the seated driver; green card "0.05" | **Seated driver. Glimmer: "the operator". No alert.** | REPLAY | T04 golden |
| 4 | 0:11–0:15 | The crowd density chart, Rulecam against the truth | **Crowd at a gate: counts within 4% of the truth** | RESEARCH FOOTAGE | Crowd, 4 Nov |
| 5 | 0:15–0:20 | The "22 → 10 people" card | **The tracker said 22. Three models said 10. By hand: 12.** | REPLAY | Supermarket, 4 Nov |
| 6 | 0:20–0:24 | Staff phone: the verified alert card | **Alerts go to roles, never names** | LIVE screen | Staff app, 4 Nov |
| 7 | 0:24–0:27 | Proof portal green, then red after one number changes | **Edit one number. It turns red.** | REPLAY | Proof portal, 4 Nov |
| 8 | 0:27–0:30 | Logo, URL | **Rulecam. Plain words in. Verified alerts out.** `{{LIVE_UI_URL}}/demo` | — | End card |

Export: H.264 MP4, 1080x1920, 30 fps, under 30 s, captions burned in, safe margins of 150 px at
the top and bottom for app UI.
