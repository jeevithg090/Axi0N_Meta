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
eval/
  manifests.py            the E1 manifest: permission and provenance per clip
  e1/manifest.example.yaml  a worked example (every entry unconsented, so it is refused)
  runners/e1.py           5 runs per arm, Wilson intervals, the slices
  report.py               renders the E1 section of RESULTS.md between its markers
```

```bash
uv run rulecam eval label eval/e1/manifest.yaml                  # consent gate + what to label
uv run rulecam eval label eval/e1/manifest.yaml --check-hashes   # also re-hash every clip file
```

**Consent is a gate, not a warning.** `clip.yaml` says what a clip contains; a manifest entry
says we are allowed to measure it. An entry whose `consent` is false or missing makes
`load_manifest` raise, so a clip without a signed form can never reach a measured run. The forms
themselves are never committed (`docs/consent.md`); the manifest records the form id, so an
auditor can ask for that form by name. Entries pin the clip's `sha256`, and `--check-hashes`
refuses a clip whose bytes have changed, because a re-recording needs its own permission.

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
generalisation evidence. A slice with no trials prints `n/a`, not `0`.

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
