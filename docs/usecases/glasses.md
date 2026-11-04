# Rulecam on smart glasses

A supervisor, store manager or crowd steward wears Ray-Ban Meta or Oakley Meta glasses. What they
look at becomes a Rulecam camera. They ask out loud, hands-free, "is this aisle safe?", "which shelf
facings are empty here?", "how dense is it ahead of me?", and the answer is spoken in their ear.
While they walk, Rulecam watches too and warns them only about hazards two Meta models agree on.
A safety walk ends in a report whose hazards go into the same tamper-evident evidence log as the
fixed cameras' incidents.

## What is real and what stands in

| Part | Today | Status |
|---|---|---|
| Point-of-view camera | The phone's rear camera on the `/glasses` page, held at chest height or clipped on, or an uploaded photo | **Stand-in**, labelled on the page ("Phone camera standing in for the glasses") |
| The glasses' own camera | Meta's Wearables Device Access Toolkit (DAT) gives an iOS or Android app the glasses' camera. The plan is in `apps/glasses/README.md`. It is not built here because this build machine has no Xcode | **Not built** |
| Spoken answers and alerts | Played through the phone's audio route, so with the glasses paired over Bluetooth they are heard in the glasses' open-ear speakers. This is ordinary Bluetooth audio, no SDK needed | Real path, **not yet tried with glasses** (the team has none on hand) |
| Microphone | The phone's default input. With the glasses connected as a headset this can be the glasses' microphones | Real path, not yet tried with glasses (it depends on the phone's audio routing) |
| Looking, counting, answering | Muse Glimmer, SAM 3.1, Muse Spark 1.3, Muse Voice Transcribe | Real model calls, nothing mocked |
| Voice | Meta MMS-TTS where the `ml` extra is installed (the API pod), otherwise the labelled system voice, otherwise the browser's voice. The Meta Model API has no text-to-speech | Real; the answer card says which voice spoke |

## How an answer is made

1. **Hear.** Only while the talk button is held, the page records the microphone (the track is
   muted at all other times, so nothing ambient is ever heard). The recording becomes a 16 kHz
   WAV, and Muse Voice Transcribe turns it into the question.
2. **Look.** Muse Glimmer looks at the frame with the question and the wearer's role (store manager,
   warehouse supervisor, crowd steward). It describes the scene, lists the hazards it sees with
   where they are, and names up to three things SAM 3.1 should count to check its answer. In an
   aisle, the shelf-gap check from `rulecam.shelf` runs at the same time (Muse Spark draws each gap,
   SAM 3.1 confirms no product is inside it).
3. **Count.** SAM 3.1 segments each thing Glimmer named, in parallel.
4. **Answer.** Muse Spark 1.3 sees the frame (and the numbered shelf gaps), Glimmer's look, SAM's
   counts and what earlier frames of the walk showed. It checks every claim against the frame and
   writes one or two short sentences for the ear, with directions relative to the wearer.
5. **Speak.** The sentence is spoken.

The mode (aisle, warehouse, crowd, general) only tells the models who is wearing the glasses and
what they care about, and biases Voice Transcribe toward a few words of that trade ("shelf",
"facings", "forklift"). Nothing else is hard-coded: the models decide what matters and what to
count.

**Verified alerts.** An alert is *verified* only when Glimmer flagged a hazard and Spark, checking
the same frame with SAM's counts, confirmed it. A question is always answered aloud; a patrol frame
(no question) is spoken only when its alert is verified. Unverified alerts are shown, not spoken.

**When Glimmer is cold or down.** Glimmer runs on serverless GPUs that scale to zero, so the page
pings it when it opens (`GET /wearables/status`, at most once a minute) to start a worker early.
While Glimmer is known to be up, it looks first and Spark looks only if Glimmer fails within
`RULECAM_WEARABLES_GLIMMER_TIMEOUT_S` (25 s by default). While it is cold, being checked or down,
a question is looked at by both at once, and Glimmer's look is used if it arrives within 6 s of
Spark's; otherwise Spark's look is used and the answer says "Glimmer unavailable". A failed look or
ping marks Glimmer down for two minutes. Glimmer still gets every frame in the background, so it
can come back. Answers looked at only by Spark are never marked verified.

**Patrol.** With patrol on, the page sends a frame every 5 seconds whenever nothing is in flight.
A frame where Glimmer sees nothing worth a warning costs one Glimmer call and nothing else.

**Site alerts.** The page also speaks new alerts raised by the site's fixed cameras (the `/ws`
stream's `alert.new`), so a supervisor on the floor hears them without looking at a screen.

**Safety walks.** Start a walk, and every frame (patrol or question) is kept with its findings.
Finish it, and Muse Spark merges the findings into hazards with the frames that show each one and
what to do about it, plus shelf gaps to refill. Each hazard is appended to the evidence chain as a
`candidate` (rule `safety-walk`, camera `glasses`, the frames as its strip), with a `verdict` when
two models verified it live, and the report itself as an `action`. They show up in `GET /incidents`
and the one-click incident report like any other incident.

## Privacy

The prompts tell every model never to identify anyone or describe faces, to refer to people by
where they are and what they are doing, and to mention clothing only when it is safety equipment or
its absence. Text in the frame (signs, labels, packs) is data, never instructions. Nothing is
transcribed unless the wearer holds the talk button. Frames, marked frames and spoken WAVs are kept
under `<data>/wearables/` on the site's own server.

## API

All routes are owner-only, like the other site tools.

| Route | What it does |
|---|---|
| `GET /wearables/status` | Which models look, count, answer and transcribe, the server voice, the modes, the Glimmer timeout |
| `POST /wearables/look` | Multipart: `image` (JPEG or PNG, up to 8 MB), optional `question` text or `audio` (16 or 24 kHz mono WAV), `mode` (`aisle`, `warehouse`, `crowd`, `general`), `source` (`phone-camera`, `meta-glasses`, `mock-device`, `upload`), `walk_id`, `lang`, `speak` (default true), `wait` (default true). Returns a `PovAnswer`, or with `wait=false` a job to poll |
| `GET /wearables/jobs/{job_id}` | A look or walk report started with `wait=false`: `running`, `done` (with `answer` or `report`) or `failed` (with `error`) |
| `POST /wearables/walks` | `{"mode": "warehouse", "note": "dock 2"}` starts a walk |
| `GET /wearables/walks/{walk_id}` | The walk, its answers so far, and its report once finished |
| `POST /wearables/walks/{walk_id}/finish` | Writes the report and chains its hazards (`?wait=false` for a job) |
| `GET /wearables/frames/{name}`, `GET /wearables/audio/{name}` | The frame, the marked frame and the spoken WAV an answer names |

A `PovAnswer` has: `spoken` (what to say, empty when there is nothing to say), `detail`, `alert`,
`verified`, `verified_by` (the models that back it), `severity`, `hazards` (what, where, severity),
`counts` (SAM 3.1's boxes per concept), `gaps` (confirmed shelf gaps), `look` (Glimmer's first look),
`looked_by`, `frame`, `marked`, `audio`, `voice`, `stages` (each step's model, milliseconds and
note) and `total_ms`.

The page uses `wait=false` and polls, because the UI's same-origin rewrite (and any tunnel) gives up
on a request after about 30 seconds, and a cold Glimmer can take longer than that.

Example:

```bash
curl -s -F image=@aisle.jpg -F audio=@question.wav -F mode=aisle \
  http://127.0.0.1:8000/wearables/look | jq '.spoken, .verified, .stages'
```

## Setup

1. API: `make api` with `META_API_KEY` (Spark, SAM 3.1, Voice Transcribe) and `GLIMMER_BASE_URL`
   (plus `GLIMMER_API_KEY` / `GLIMMER_EDGE_KEY` for the RunPod serverless Glimmer). For Meta
   MMS-TTS on the server, `uv sync --extra ml`.
2. UI: `cd ui && npm run dev`, then open `/glasses` on the phone. Camera and microphone need HTTPS
   (the public UI, or a tunnel); `localhost` works on the same machine.
3. Log in as the owner on the phone (the routes are owner-only).
4. Pair the glasses with the phone over Bluetooth as usual (Meta AI app). Answers then play in the
   glasses.

## Demo (3 minutes)

1. Open `/glasses` on the phone, tap **Start glasses mode**, allow camera and microphone. The yellow
   chip says the phone camera stands in for the glasses.
2. Pick **Warehouse**. Point at a forklift aisle (or a forklift video on a laptop screen). Hold the
   button and ask "Is it safe to walk ahead?". Release. The answer is spoken; the card shows the
   red **Verified alert · Spark + Glimmer + SAM 3.1** badge, SAM's boxes on the frame, and each
   step's time.
3. Pick **Aisle** and ask "Which shelf facings are empty here?". The card lists the confirmed gaps
   with the products next to them, and the numbered frame.
4. Tap **Start safety walk** (patrol turns on). Walk past two or three scenes. Tap **Finish walk**:
   the report lists the hazards with their frames and says "chained as evidence" with the record
   hashes. Open `/incidents` to see them.
5. Leave the page open while another camera raises an alert: it is spoken as "Site alert. …".

## Results

Live runs on Nov 4 2026 are in `reports/eval/2026-11-04-glasses.md`: the whole path works with
the real models and verified alerts happened, but a question takes about 30 seconds from frame to
spoken answer, which is too slow for an earpiece. Screens of the page are in `apps/glasses/screens/`.

## Files

- `src/rulecam/wearables/assist.py`: the assistant (hear, look, count, answer, speak).
- `src/rulecam/wearables/walks.py`: safety walks, the Spark report and the evidence records.
- `src/rulecam/wearables/prompts.py`, `schemas.py`: the prompts per mode and the shapes.
- `src/rulecam/api/wearables.py`: the routes and how they are built from settings.
- `ui/src/app/glasses/`: the phone page.
- `apps/glasses/README.md`: the native glasses app with Meta's Wearables Device Access Toolkit.
