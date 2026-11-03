# Eval set and the 9.5 scorecard (WS0)

This folder holds the labelled clips that every PLAN-9.5 claim is measured against, and the
checks the harness cannot measure itself. The code is `src/rulecam/eval/`; the commands are
`rulecam eval …` and `rulecam scorecard`. Results go to `reports/eval/<date>.md` (the PLAN §2
table) and `reports/eval/<date>.json`.

```
eval/
  clips/<name>/clip.yaml   one clip: source, camera, requests, labels (format below)
  clips/<name>/gt.txt      optional MOT box tracks for tracking metrics
  checks.yaml              criteria checked by hand or by other tools (login, deploy, demo …)
  baseline/                recorded runs (replayable offline, no network)
```

## Commands

```bash
uv run rulecam eval validate                 # schema-check every clip.yaml
uv run rulecam eval fetch                    # download + sha256-check URL clips into data/eval_cache/
uv run rulecam eval run                      # play the set into a running API, score, write the scorecard
uv run rulecam eval score eval/baseline/2026-10-31-live   # replay: re-score recorded runs, no network
uv run rulecam scorecard --report reports/eval/2026-10-31.json --live-api http://localhost:8000
uv run rulecam scorecard --checks auto --api https://<api-pod>-8000.proxy.runpod.net \
  --restart-evidence restart.json --budget-evidence budget.json --out reports/eval/card.md
```

`--checks auto` runs the checks a script can make (`src/rulecam/eval/autochecks.py`) on top of
`checks.yaml` and writes the merged checks, each with what was observed, to
`checks-<date>.yaml` next to the markdown. A check passes only on observed evidence; one that
could not run, or that waits on a decision (the owner's passphrase, GHCR visibility for the real
deploy), stays NOT MEASURED with its reason. The two that change the deployment run on the API
pod and hand back a JSON line: `scripts/ops/restart_check.sh` (restart through `start.sh`, the
approved plan restored) and `scripts/ops/budget_alarm_check.sh <feeding command>` (a low budget
raises `cost.alarm`, then the `.env` is restored and checked).

`eval run` must run **on the API pod** (owner calls only work over loopback), against
`http://localhost:8000`, with `RULECAM_RUNTIME=true`. Approving a plan replaces the runtime's
active plan, so run the eval when nobody is demoing, and re-approve the demo plan afterwards.
Useful options: `--clip NAME` (repeatable), `--speed` (1 = real time; up to about 2.8x, limited by
the pairing endpoints' one request per second; latency and lag only count at 1x),
`--survey-s` (seconds fed before the request, default 80), `--track-store /workspace/data/tracks`
(tracking metrics), `--gpu-usd-per-hour` and `--cameras-per-gpu` (cost), `--include-unlabelled`.

What one run does, per clip and rule: split the clip into 3 s 10 fps MP4 chunks, pair as a phone,
feed the clip in a loop for the survey, submit the request, keep feeding until the plan is ready,
approve it (a 409 is recorded as a refusal), feed the clip **once more from the start** (the
scored pass), wait until nothing is in flight, then collect this camera's candidates, verdicts and
actions from the evidence chain, the `alert.new` frames from `/ws` and `/runtime/metrics`
samples. Each run is saved as `reports/eval/runs/<timestamp>/<clip>__<rule>.run.json`;
`eval score` re-scores those files offline, which is the CI replay path (the recorded chain holds
every model reply). Copy a run folder into `eval/baseline/` to keep it.

### Ask benchmark (`rulecam eval ask`, add-on 2.6)

```bash
uv run rulecam eval ask eval/ask/corridor.yaml --runs 3            # on the API pod (loopback)
RULECAM_OWNER_PASSPHRASE=... uv run rulecam eval ask eval/ask/corridor.yaml \
  --api https://rulecam.vercel.app/rulecam-api --origin https://rulecam.vercel.app
```

Asks each question of the set `--runs` times through `POST /ask/stream`, naming the numbers it
wants; the agent returns them in the answer's `numbers` field, so nothing is parsed from prose.
Writes `reports/eval/ask/<timestamp>-<set>.json` and `.md`: pass rate per question, each run's
values (stability), median seconds and each answer's run-log receipt. Every answer the API gives
is also appended to the hash-chained run log `<data>/ask/runs.jsonl` (prompt SHA-256, git SHA,
models, step timings and results); `rulecam.ask.runlog.verify_run_log` checks the chain.
`--question C07` (repeatable) runs only some questions.

### The 31 Oct baseline (`baseline/2026-10-31-live/`)

The first recorded run, taken from the live deployment's evidence chain before this harness
existed: the warehouse clip (driver only, a negative) looped by the old feeder into camera
`phone-18`, plan `plan-ffe868b5cb41`. Its 10 candidates and 10 Tier 1 verdicts are the real chain
records; the chunk timeline was reconstructed (3 s slots from the approval to the last
candidate), so clip seconds there are seconds since approval, which a negative clip does not
need. No `/runtime/metrics` samples were recorded, so lag and cost are not measured from it. It
replays to: 10 candidates, 0 verified alerts, Glimmer decisive on 0 of 10 (every verdict
`unsure`: the strip renderer is a placeholder, PLAN-9.5 §1 root cause 1). CI re-scores it on
every push (`make eval-replay`).

## E1: consent-gated manifests, ablations and baselines (P4-06)

E1 is the detection-accuracy evaluation. It sits on top of the clip set above and adds the three
things the clip set does not carry: permission, arms and intervals.

```
src/rulecam/eval/manifests.py  the E1 manifest: permission and provenance per clip
eval/
  e1/manifest.example.yaml  a worked example (every entry unconsented, so it is refused)
  runners/e1.py           5 runs per arm, Wilson intervals, the slices
  report.py               renders the E1 section of RESULTS.md between its markers
```

The manifest layer lives in the installed package because `rulecam eval label` uses it; the
top-level `eval/` folder is not installed, so nothing under `src/` imports from it.

```bash
uv run rulecam eval label eval/e1/manifest.yaml                  # consent gate + what to label
uv run rulecam eval label eval/e1/manifest.yaml --check-hashes   # also re-hash every clip file
```

**Consent is a gate, not a warning.** `clip.yaml` says what a clip contains; a manifest entry
says we are allowed to measure it. An entry whose `consent` is false or missing makes
`load_manifest` raise, so a clip without a signed form can never reach a measured run. The forms
themselves are never committed (`docs/consent.md`); the manifest records the form id, so an
auditor can ask for that form by name. An entry whose rule is no longer in the clip's
`clip.yaml`, or whose label contradicts it, is refused, so a relabelled clip gets its entry
reviewed. Entries pin the clip's `sha256`, and `--check-hashes` refuses a clip whose bytes have
changed, because a re-recording needs its own permission.

**Arms.** Three ablations (`predicates`, `glimmer`, `spark_open`) and two baselines
(`yolov8_ppe`, `frame_vlm`, the model asked the rule every 5 s on single frames). YOLOv8
(ultralytics) is AGPL-3.0 and lives in the `eval` extra only: no runtime code imports it, and the
runner only ever receives its outputs.

**What the numbers mean.** Intervals are Wilson 95% over labelled episodes, so they account for
how few clips there are and for nothing else: not for the clips being ours, not for staging, not
for one site. Each arm runs the set five times, and that spread is **stability, not accuracy** —
repeats of the same clip say whether the pipeline is deterministic, so counts come from the first
scored run of each clip and are never summed over repeats. Clips staged by an **outsider** are
reported as their own slice and never pooled with ours, because they are the only honest
generalisation evidence. A slice with no trials prints `n/a`, not `0`. Outcomes for a clip/rule
pair outside the manifest are dropped before counting, and a summary is marked **simulated**
unless the caller passes `simulated=False` for a run through the real pipeline.

## How the metrics are computed

- **Alert**: a candidate whose chained decision says `alert`, or whose last verifier verdict
  (Tier 2 over Tier 1) is `violation`, or that produced an `alert.new` frame. Candidates that are
  not verified are counted, but they are not alerts.
- **Matching**: alerts of one rule on one clip closer than 3 s merge into one episode; episodes
  shorter than 1 s are widened to 1 s; episodes match labelled events greedily by temporal IoU
  ≥ 0.3, one to one. Precision and recall are summed per rule over clips. Every alert episode on a
  `negative` clip is a false alert.
- **Alert latency**: from the event's start (camera time of that clip second) to the verified
  alert (the `/ws` frame if seen, else the chain record).
- **Glimmer decisiveness**: Tier 1 verdicts that are not `unsure`, over all candidates.
  **Agreement**: decisive verdicts that match the label (violation if the candidate overlaps an
  event).
- **Lag**: `lag_s` from `/runtime/metrics`, sampled after every chunk and while draining.
- **Cost per camera-hour**: hosted SAM billed frames during the run × $0.20/1,000
  (`clients/meta_hosted/sam_cost.py`) + GPU $/h ÷ cameras sharing the GPUs. Clips without
  positive events count as idle, clips with events as active.
- **Tracking**: HOTA (TrackEval's algorithm), IDF1, ID switches (py-motmetrics) per concept, on
  the frames the runtime tracked. ID switches per minute divide by the real time those frames
  span (first to last, plus one sampling step), not by their count over the label frame rate,
  which overstated the rate about 10x at keyframe rates (3 Hz samples on 30 fps labels).
- **Which candidates count**: those whose episode overlaps the scored pass, clamped to it. The
  runtime can stamp a candidate a few milliseconds before the pass's first chunk (the feeder and
  the API stamp chunks separately); before 1 Nov such a candidate was dropped, which hid the
  driver false alert of the 1 Nov live run.

## Clip format (`clips/<name>/clip.yaml`)

```yaml
name: owner_fl_pos_1m              # equals the folder name
description: Pedestrian passes the moving forklift at about 1 m.
camera_id: owner-warehouse         # logical camera; sent as the phone label (≤ 40 chars)
source:
  path: clip.mp4                   # a local file next to clip.yaml (git-ignored if large) …
  # url: https://…  + sha256: …    # … or a URL pinned by its sha256, cached in data/eval_cache/
duration_s: 42.0
license: recorded by the owner, consent from everyone filmed
rules:
  - id: forklift_2m                # scoring key: the same id on several clips is one rule
    request: Keep people at least 2 metres away from a moving forklift.
    lang: en
    status: labelled               # labelled | negative | unlabelled
    expect: activatable            # or refusal (the planner should refuse honestly)
    events:
      - {start_s: 12.4, end_s: 15.1, kind: positive, note: "pedestrian within 1 m"}
tracks:                            # optional, for tracking metrics
  file: gt.txt
  fps: 30
  width: 1920                      # frame size the boxes were drawn on
  height: 1080
  classes: {1: person, 2: forklift}
```

Never commit a video larger than a few MB: use a URL + sha256, or keep the file next to
`clip.yaml` locally and share it outside git (`*.mp4` under `eval/clips/` is git-ignored).

**What an event is.** Label the seconds during which the rule is actually broken, not the whole
approach: for the forklift rule, from the moment the pedestrian is closer than 2 m to a *moving*
forklift until they are 2 m away again or it stops. For hand washing (at least 20 s), a positive
event is a wash that ends too early: from the start of that wash until the person leaves the sink.
A compliant wash is not an event.

## Labelling with CVAT or Label Studio

Temporal labels are enough for most clips; box tracks are needed on 3 clips (marked in their
`clip.yaml`) for HOTA / IDF1 / ID switches.

**CVAT** (self-host with `docker compose up -d` from github.com/cvat-ai/cvat, MIT):

1. Create a project with labels `person` and `forklift` (or `sink`), both rectangles. Create a task
   from the clip (keep the original frame rate; note it).
2. Events: play the clip, and for each violation note its first and last frame number from the
   frame counter. `start_s = first_frame / fps`, `end_s = last_frame / fps`. Write them into
   `events:`.
3. Tracks: draw each object in *Track* mode (press `N`, draw, then move through the frames and
   adjust; CVAT interpolates between keyframes). Every person and vehicle in view gets one
   track for as long as it is visible.
4. Export the task as **MOT 1.1**. From the zip, copy `gt/gt.txt` to `clips/<name>/gt.txt`. The
   class column follows the order of `gt/labels.txt` (1-based): put that order in `classes:`.

**Label Studio** (`pip install label-studio`, Apache-2.0):

1. Events: use the *Video Timeline Segmentation* template with one label `violation`. Draw a
   segment over each violation. Export JSON: each region's `ranges` give start and end frames;
   divide by the video's frame rate for `start_s` / `end_s`.
2. Tracks: the *Video Object Tracking* template draws boxes with keyframes, but its export is
   JSON in percentages, not MOT. Prefer CVAT for the 3 tracked clips.

Run `uv run rulecam eval validate` after editing; it catches overlapping or out-of-range events,
a missing tracks file, and a URL without a sha256.

## Recording the owner's clips

The 12 placeholder clips under `clips/owner_*` are what PLAN-9.5 §3 asks for. Record each one,
then fill in its `clip.yaml` (source, duration, `placeholder: false`, status, events).

**Set-up for every clip**

- Phone in landscape, **fixed** (tripod or taped to a shelf), 2.5 to 3 m high, looking down at
  30 to 45°, the whole working area in view. Do not move it between the clips of one set: they
  share one camera and one calibration.
- 1080p or 720p at 30 fps, video only. Keep each clip **30 to 90 s**; start and end with about 5 s
  of the empty scene.
- Calibration: keep a standard pallet (48 x 40 in) or a 1 m x 1 m square of floor tape fully in
  view and flat on the floor in every clip.
- Distances: mark the forklift's path with tape, and mark lines on the floor at **1 m, 1.5 m, 1.9 m,
  3 m and 6 m** from it, so the pedestrian can hit them and the labeller can check them.
- A toy forklift, a cart or a trolley pushed by someone can stand in for the forklift: use the same
  stand-in in every forklift clip, and make sure it really moves (walking pace) when the rule
  needs it moving.
- People: only adults who agreed to be filmed; faces are not needed (walking side-on or away is
  fine). Never film in a bathroom, bedroom or break room. For hand washing, use a **kitchen or
  utility sink**, never a bathroom sink.
- Note the times of each event on a stopwatch while filming; it makes labelling quick.

**Forklift (rule `forklift_2m`)**

| Clip | What to film | Label |
|---|---|---|
| `owner_fl_pos_1m` | Forklift drives along its path; a pedestrian walks past it at about 1 m, then away. Box tracks too. | positive: the seconds under 2 m |
| `owner_fl_pos_1_5m` | Same, at about 1.5 m. | positive |
| `owner_fl_pos_1_9m` | Same, at about 1.9 m (just inside). | positive |
| `owner_fl_pos_two_passes` | Two separate passes at about 1 m, at least 15 s apart. | positive: two events |
| `owner_fl_neg_driver_only` | Only the driver on (or pushing) the moving forklift; nobody else. Box tracks too. | negative |
| `owner_fl_neg_far` | Moving forklift; the pedestrian stays 3 to 6 m away the whole time. | negative |
| `owner_fl_neg_parked` | Forklift parked, engine off, never moves; the pedestrian walks right next to it (under 1 m). | negative |
| `owner_fl_neg_lookalike` | A pallet jack or trolley (not the stand-in) moves; the pedestrian passes it at 1 m. | negative |

**Hand washing (rule `handwash_20s`, at least 20 s)**

| Clip | What to film | Label |
|---|---|---|
| `owner_hw_pos_10s` | One person washes for about 10 s, then leaves. Box tracks too. | positive: the wash |
| `owner_hw_pos_two_short` | Two washes of about 8 s with a 10 s gap away from the sink. | positive: two events |
| `owner_hw_neg_full` | One person washes for 25 to 30 s. | negative |
| `owner_hw_neg_empty` | Nobody at the sink (people may pass behind). | negative |

Extra negatives from stock footage (Mixkit, Pexels) are welcome where the licence allows: add
them with a URL + sha256 like `warehouse_45848` and `kitchen_15875`.

## Runners E3-E11 (P4-07)

Each runner answers one question, writes `eval/results/<id>.json`, and renders one table into
`eval/RESULTS.md` between its own markers. `eval/runners/common.py` holds the shared result
record, the JSON writer, the table renderer and the splice.

| Runner | Question | Needs |
|---|---|---|
| E1 | How often does a rule fire when it should? | Recordings, consented manifest |
| E3 | Does the Planner handle held-out requests? | **Simulated** today: it does not call the Planner (W-00) |
| E4 | Does Rule CI catch bugs and attacks? | Rule CI reports for the approved rules |
| E5-lite | What does Anonymous Mode cost in accuracy? | The same clips scored raw and as skeletons |
| E7 | What does it cost, and how fast is it? | A real-time run's `LoopMetrics` and its camera-hours |
| E8 | Person-down on a dataset we did not make | UR Fall Detection, downloaded by hand under its terms |
| E9 | Does the evidence chain re-derive? | `rulecam audit` over a day's chain |
| E11 | Did the Site Advisor find the sealed cause? | A sealed session **and** a null session |

Two rules every runner follows:

- **A result that did not come from the real system is marked `simulated` and says why**, and
  the rendered table says so in bold. E3 is marked this way today, and E7 marks itself when the
  run was not fed at real time, because latency from a sped-up run is not latency.
- **"Not measured" is a result.** A runner with no input writes a result with no rows and a
  caveat naming what is missing, so `RESULTS.md` says what was not measured instead of staying
  quiet about it. Every runner but E3 is in that state right now.

Three of them refuse to produce figures rather than produce weak ones:

- **E8** reports nothing until the dataset manifest records that a person read and accepted the
  dataset's terms, and names the sequences used. It never downloads anything: accepting terms is
  a person's decision, not a script's. It also scores **per sequence, not per frame**, because a
  fall is one event and frame scoring would turn it into hundreds of correlated trials.
- **E11** treats a session with no recorded seal hash as unsealed, and says plainly when the
  **null session is missing** — without that control, "found the sealed cause" cannot be told
  apart from "reports something confident every time".
- **E5-lite** compares only clip/rule pairs that *both* arms scored, so the difference is
  paired; an arm that could not score a clip is reported as unpaired, not as a wrong answer.

## Tracking on stored tracks: CAVIAR and MOT17 (add-on 1.5)

`rulecam eval run` scores the tracks of clips it plays. These commands score tracks already in a
track store, such as the 2 Nov corridor test (`phone-68`: seven CAVIAR corridor clips fed once as
one 9 min 14 s stream from 09:44:11.875 UTC, upscaled 2x, then backfilled).

```bash
# CAVIAR XML -> MOT gt.txt (several files are chained into one stream, in the order given)
uv run rulecam eval caviar-mot WalkByShop1cor.xml TwoEnterShop1cor.xml ... --out gt.txt

# HOTA / IDF1 / MOTA of the stored tracks: raw ByteTrack ids, and (with --identities) the
# DINOv2 identities the Ask identity job linked them into, reported as separate rows
ulimit -n 65536   # the live store has many small Parquet files
uv run rulecam eval track --store data/tracks --camera phone-68 \
  --start 2026-11-02T09:44:11.875Z --scale 0.5 \
  --caviar WalkByShop1cor.xml --caviar TwoEnterShop1cor.xml ... \
  --identities data/ask/identity/track_identity.jsonl --out reports/eval/corridor-tracking.json
```

A point's stream second is its time since `--start`, so it lands on MOT frame
`round(s * 25) + 1`; `--scale 0.5` maps the 768x576 stored boxes back onto CAVIAR's 384x288.
Only frames the runtime tracked are scored (as in `eval run`). Tracks the identity job has not
linked keep their own id in the re-ID row, and the report says how many there were.

**Cross-check against TrackEval** (the MOTChallenge reference implementation) on one clip of
the stream, on the same frames; the two should agree to rounding:

```bash
uv run --with git+https://github.com/JonathonLuiten/TrackEval \
  python scripts/eval/trackeval_crosscheck.py --store data/tracks --camera phone-68 \
  --start 2026-11-02T09:44:11.875Z --scale 0.5 --segment TwoEnterShop1cor \
  --caviar WalkByShop1cor.xml TwoEnterShop1cor.xml ... --out reports/eval/trackeval-check.json
```

**MOT17 train** (`scripts/eval/mot17.py`): downloads MOT17 (about 5.5 GB) once, tracks the seven
training sequences with the runtime's own image tracker (SAM 3.1 keyframes + ByteTrack) into a
separate store, and scores them with our scorer and, when installed, TrackEval. It needs the SAM
endpoint, so it runs on the API pod. Only keyframes are scored, so use `--keyframe-fps 10` and
read the numbers as keyframe-rate tracking, not as a full-frame MOT17 submission.

## CAVIAR events: rule accuracy on someone else's labels (add-on 1.3)

`eval/clips/caviar_*` turns CAVIAR's own activity labels into events for four plain-language
rules (`src/rulecam/eval/caviar_events.py`); `scripts/eval/caviar_clips.py` writes the entries:

| Rule id | Request | Labels | Clips |
|---|---|---|---|
| `caviar_fall` | Tell me if anyone falls down or ends up lying on the floor. | "fall", "fell", "down" | Rest_FallOnFloor, Fight_OneManDown; negative: Rest_InChair, Browse1 |
| `caviar_fight` | Alert me if people start fighting or someone is being attacked. | "fight" | Fight_RunAway1/2, Fight_OneManDown, Fight_Chase; negative: Meet_WalkTogether1, Browse1 |
| `caviar_left_bag` | Warn me when a bag or box is left behind with nobody looking after it. | left / abandoned object | LeftBag, LeftBag_AtChair, LeftBag_BehindChair, LeftBox, LeftBag_PickedUp; negative: Browse1 |
| `caviar_shop_enter` | Let me know each time someone walks into one of the shops. | situation "shop enter" | the 7 corridor clips of the 2 Nov test (19 events) |

An event is the span during which some person or group carries a matching label (gaps under
1 s closed, people merged, at least 1 s long). The 7 corridor clips are labelled and runnable now
(video pinned by URL + sha256, each with a MOT `gt.txt` so `--track-store` also gives HOTA/IDF1).
The 13 INRIA lobby clips are placeholders until their labels and video are downloaded:

```bash
uv run python scripts/eval/caviar_clips.py inria --src data/caviar/inria --fetch  # labels + video
uv run rulecam eval validate                                     # every caviar_* clip validates
make eval-caviar TRACK_STORE=/workspace/rulecam/data/tracks      # on the API pod: all caviar_* clips
```

A label vocabulary that does not match (the script refuses a positive clip with no matching
label, and a negative control with any) is checked with
`scripts/eval/caviar_clips.py inventory <file.xml>`; change the event type's needles, not the
labels.

## Labelled verifier set (add-on 1.4)

The 1 Nov campaign has 29 Glimmer verdicts (27 from run 2, 2 from run 1); their strips are on the
API pod under `data/strips/<camera>/<event>/`. Nobody has labelled them, so Tier 1 accuracy
(target ≥ 90 %) is unmeasured.

```bash
# on the API pod: copy the strips into a blind sheet (shuffled, neutral ids v01..v29)
uv run rulecam eval verifier-sheet --strips data/strips --out data/verifier-sheet
# hand data/verifier-sheet/ (minus key.json) to two people; each opens index.html, labels every
# item violation / no violation / unsure from the frames alone, and sends back labels-<name>.csv
uv run rulecam eval verifier-score labels-alice.csv labels-bob.csv \
  --key data/verifier-sheet/key.json --out reports/eval/verifier-labels.json
# 6 blurred frames at 640 px vs 8 raw at 960 px on the same strips (calls Glimmer, ~58 calls)
uv run python scripts/eval/verifier_replay.py --key data/verifier-sheet/key.json \
  --strips data/strips --out reports/eval/verifier-replay.jsonl --labels labels-alice.csv labels-bob.csv
```

The viewer shows the request and the frames only; Glimmer's verdict, score and reason are in
`key.json`. The score reports Glimmer's accuracy against each labeller and against their
consensus (items both labelled the same decisive way; a Glimmer `unsure` there counts as not
correct, and accuracy among its decisive answers is reported too), with a Wilson 95 % interval,
the `unsure` rates, raw agreement and Cohen's kappa. The `blur6_640` strips are built from the
raw ones (6 of 8 frames, scaled to 640 px with the annotation band, faces blurred with YuNet when
`RULECAM_FACE_MODEL` is set), not re-rendered from the chunks.

## Serverless latency and cold starts (add-on 1.6)

Every latency figure in the 1 Nov reports predates the move of SAM 3.1 and Glimmer to RunPod
serverless load balancers. `scripts/eval/serverless_load.py` replays recorded inputs in bursts of
1, 5 and 20 alert jobs, **cold** (both endpoints scaled to zero first) and **warm** (straight
after): an alert job is a chunk's 6 keyframes to SAM's `/segment_image`, then an evidence strip
to Glimmer's `/chat/completions`. It reports p50 / p95 per stage (`sam`, `glimmer`, `total`) and
dollars per verified alert at $1.34/h per running RTX 4090 worker.

```bash
# on the API pod (same data center as the endpoints); URLs and keys from the environment only
set -a; . ./.env; set +a
export SAM_EDGE_KEY=$(cat .sam_edge_key) GLIMMER_EDGE_KEY=$(cat .glimmer_edge_key)
export RUNPOD_API_KEY=...   # optional: measured worker time, and waiting until truly cold
uv run python scripts/eval/serverless_load.py --strips data/strips --chunks data/chunks \
  --out reports/eval/$(date +%F)-serverless-load
```

With `RUNPOD_API_KEY` the script samples each endpoint's worker list
(`/v2/serverless/<id>/workers`, which does not wake workers) every 5 s and bills the workers
with a container up (an `uptimeSeconds`). The legacy `/v2/<id>/health` is not used: it counts
parked workers (scaled down, image cached) as `idle`/`ready`, so it never reaches zero on a cold
endpoint, and the status alone does not tell either (parked workers are listed as `IDLE` or
`INITIALIZING` with no uptime); without the key,
worker time is a lower bound (the union of request time per endpoint plus one 300 s idle tail)
and the report says `estimate`. A cold burst waits until both endpoints report no worker (or
330 s without the key). Expect 20 to 30 minutes and a few dollars for the full run.

## README and RESULTS numbers (add-on 1.1)

The results tables in `README.md` and `eval/RESULTS.md` are generated: each row is a fact in
`eval/results.yaml` (a result file under `reports/eval/` and a path to the number in it), shown
with its run, date and the commit that added the file. After a new result file lands, add facts
for it and run `uv run rulecam eval results` (`--check` only reports staleness).
`tests/eval/test_results_doc.py` fails when a number in either table is not in the file its row
cites, or when the tables no longer match the facts.
