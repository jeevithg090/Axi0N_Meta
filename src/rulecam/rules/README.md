# `rulecam.rules`

The pure predicate and temporal engine: turns a Rule IR and a window of tracks into
`CandidateEvent`s. Owner: **P1** (task P1-06). If this engine is wrong, every rule is wrong, so
the semantics below are the contract that the tests pin down.

## Public API

| Name | What it does |
|---|---|
| `evaluate(rule, tracks, *, calibration, zones, t0, t1, hz=10.0, episode_gap_s=2.0, cooldown_s=0.0, plan_version_hash="", pose=None, operator_exclusion=True)` | Pure: no I/O, no clock, no randomness. Identical inputs give byte-identical output; the order of `tracks` does not matter. Returns candidates sorted by `(ts_start, camera_id, event_id)` |
| `rule_ir_hash(rule)` | sha256 of the rule's canonical JSON (sorted keys) |
| `backtest(rule, store, camera_id, t0, t1, *, calibration, zones, hz, baseline, pose) -> BacktestReport` | Re-runs the engine on `TrackStore.window` (no SAM calls): events, count, per-hour rate, `backtestable` (`yes`/`partial`/`no` from the concepts present in the stored tracks), and an optional v1-vs-v2 diff (`added`/`removed`, matched by subject and overlapping time). `report.headline()` gives "would have fired N× earlier today" |
| `DecisionContext`, `candidate_payload(candidate, context)` | What a decision depended on besides tracks (rule, calibration, zones, window, rates, `operator_exclusion`). P3-07 stores `candidate_payload(...)` as the `candidate` record's payload |
| `OperatorExclusion(disabled_rules=...)`, `.from_csv(text)`, `.enabled_for(rule_id)` | Engine configuration keyed by rule id: the rules whose owner turned operator exclusion off (`RULECAM_OPERATOR_EXCLUSION_OFF`) |
| `rederive(record, store, *, pose=None) -> bool` | E9 audit: checks the rule hash, re-evaluates the stored window and requires the recorded candidate to be reproduced exactly. Tampered payloads, swapped rules or changed tracks give False |
| `near_miss_at(tracks, *, camera_id, subject_concept, other_concept, at, calibration)` | Closest approach and time-to-collision per subject at one instant (e.g. person vs forklift) |
| `PoseFeatures` | Protocol `(state, point) -> bool \| None` that adapts P4-04's `rulecam.pose.features`; passed as `pose=` |
| `RuleEvaluationError` | Invalid window, unknown or incomplete zone, `distance_m` without a bound |

The rule's predicate being **true means "violation"**: one candidate is emitted per
(rule, subject, episode).

## Semantics

**Timeline.** All tracks are resampled onto `t0 + k / hz` (points outside `[t0, t1]` are
dropped). Between consecutive points at most 0.5 s apart the box and foot point are
interpolated linearly; after a point with no successor within 0.5 s its value is held for at
most 0.5 s, then the track is absent. A sample lasts `1 / hz` s: a run of `n` samples lasts
`n / hz` s, and a threshold of `s` seconds needs `ceil(s * hz)` samples.

**Cameras.** Each camera that has tracks, or that a zone of the predicate is on, is evaluated
separately. The calibration applies only to tracks of its own camera. A zone on another camera
is never entered.

**Concepts.** A rule concept matches tracks whose `concept` is the concept key or its SAM prompt
(`bucket` ↔ `"excavator bucket"`). Object references: `person` and `person.foot_point` use the
foot point, `person.mask` the box centre, `person.hands` the hands associated with the person.

**Ground positions.** `foot_m` when the track has it, otherwise the foot point projected with the
calibration, otherwise unknown (NaN).

**Association (hands, gloves, …).** A hands detection belongs to a person when its box centre is
inside the person's box, or within 0.15 m of one of the person's wrist keypoints (score ≥ 0.3).
0.15 m is converted to pixels with the person's box height as a 1.7 m reference, so no
calibration is needed. A detection inside two person boxes belongs to both.

**Subjects.** Atomic predicates give a boolean and a measured value per (time step, subject).
Per-subject predicates have one series per track of their first object; `count` and `state` are
scene-level (subject `scene`). `all`/`any` keep the subject of their first per-subject child;
children about another concept are reduced to "for some object" and broadcast, so
`all(distance(person, bucket) < 2, moving(bucket))` reads "this person is near a bucket and some
bucket is moving". `not` is true only while its subject is observed.

| Op | True when | Value (unit) |
|---|---|---|
| `distance_m` | min ground distance from `a`'s point to any `b` satisfies `lt` / `gt` (`b` = the projected bottom edge of its box with a calibration, unless `.foot_point`) | metres (`m`); `inf` when no `b` is present, unknown when unmeasurable (never true) |
| `inside` | the reference point is in the zone (image polygon; or, for a buffer zone without a polygon, within `buffer_m` of the concept's footprint) | 0/1 |
| `overlap` | box IoU with some `b` is > 0 and ≥ `min_iou` | IoU (`iou`) |
| `without` | no `item` box centre in `region`: `full` box, `top_25pct` top quarter, `hands` within 0.15 m of a wrist or the lower half of the box without pose | items found (`count`) |
| `moving` | centred 5-sample median of ground speed > `speed_mps_gt` | `m/s` |
| `count` | objects (in the zone) `> gt` and `< lt`; with no bounds, at least one | `count` |
| `state` | a track of the state concept is present: `door`/`open` → `"open door"` or `"door/open"`; `present` → the object itself | `count` |
| `pose` | the injected `PoseFeatures` says so for the latest point; without an adapter, or when it returns None, the state is false (`pose_unavailable` / `<path>.unknown` in the trace) | 0/1 |
| `dwell` | the running inside-time of the current visit has reached `total_s_gte`; runs separated by gaps ≤ `max_gap_s` form one visit and gaps are not counted; stays true until the visit's last inside sample | running total (`s`) |
| `persist` | the child has been true for at least `seconds` without interruption | run so far (`s`) |
| `absence` | scene-level: no `object` in `zone` for at least `seconds` (the window start counts as the start of an empty stretch) | time empty (`s`) |
| `rate` | scene-level: rising edges of the child, summed over all subjects, in the trailing 60 s exceed `per_minute_gt` | edges per minute (`1/min`) |
| `sequence` (past) | at a rising edge of `trigger` for a subject, `requirement` did not hold for that subject at any sample in the previous `require_within_s` (inclusive); fires at the edge | the requirement's best value in the window, e.g. the longest wash (`s`) |
| `sequence` (future) | `requirement` does not hold within `require_within_s` after the edge; fires at the deadline; edges whose deadline is past `t1` are undecided and never fire | as above |

**Edges.** A subject that is already true at the window start is not a rising edge: its history
is unknown. A track that appears later already true is an edge. Evaluate sequences over a window
that starts at least `require_within_s` before the triggers of interest.

**Cameras with no tracks.** A camera that a zone of the rule is on is evaluated even with no
tracks, so `absence` fires on an empty scene. Whether the camera was actually watching is the
coverage watchdog's job (P1-08).

**Near-miss metrics.** For every `distance_m` node, the trace also holds `<path>.separation_m`,
`<path>.closest_m` and `<path>.closest_in_s` (closest approach within a 1 s horizon), and
`<path>.ttc_s` (time until they come within 0.5 m, or `"none"`). Both objects are extrapolated at
constant velocity; velocities are least-squares slopes of the ground foot point over the last 5
samples (an object seen once counts as standing still). The metrics measure the same geometry as
the node (WS1d): to the other object's footprint when the node does (calibration, no
`.foot_point`), the footprint moving rigidly with the object, and without the vehicle the subject
operates. So `separation_m` equals the node's `value` at the firing time, and a candidate of
`distance_m(lt=d)` always has `separation_m < d`. These are part of the candidate, so `rederive`
checks them too.

**Operator exclusion** (`operators.py`, WS1c). In `distance_m`, buffer-zone `inside` (and
`count` in a buffer) and the near-miss metrics between a person-like concept (person, worker,
pedestrian, ...) and a vehicle-like one (forklift, pallet jack, cart, vehicle, truck, ...), a
vehicle is ignored for the person riding it. A person rides a vehicle during a *boarding*: a run
of samples where at least 40% of the person's box lies in the vehicle's box (gaps up to 0.5 s
bridged) in which, on the samples where the vehicle moves (5% of its box height per second in the
image, or 0.1 m/s on the ground with a calibration) and its box keeps its shape, the pair
co-moves at least 80% of the time, for at least 1 s. Co-moving is either the strict test (cosine
>= 0.9, speeds within 30%) or a relative image velocity under 5% of the vehicle's box height per
second, so a slow close-up forklift and its seated driver link (live P1-07). A boarding still
running at the window's end with less evidence is pending (linked if it co-moves so far). A
person who was outside the box beside the moving vehicle in the second before the boarding, and
not moving with it, is a pedestrian for the whole boarding: pedestrians a vehicle drives up to,
or who walk in front of it, still fire. A driver on a vehicle that does not move is never linked
(the conservative side). On
by default; `evaluate(..., operator_exclusion=False)` turns it off, which the runtime does for the
rule ids in `RULECAM_OPERATOR_EXCLUSION_OFF` (owner override, no Rule IR change).

**Episodes.** True samples of one subject merge into an episode across gaps shorter than
`episode_gap_s` (2 s). With `cooldown_s`, an episode starting within that long after the
previous reported one is dropped.

**Candidates.**
- `ts_start` is the firing sample, `ts_end` the episode's last true sample.
- `measurement` is the firing-time value of the first measuring node in pre-order (skipping
  `all`/`any`/`not`/`persist`), if finite.
- `predicate_trace` holds `subject` (opaque track id or `scene`), `episode_s`, and for every node
  its truth and value at the firing time under its path (`all.0.distance_m`,
  `all.0.distance_m.value`). Non-finite values are written as `"inf"` or `"unknown"`.
- `event_id` = hash of (rule hash, camera, subject, `ts_start`), so the same episode seen in
  overlapping windows keeps its id.
- `track_window_hash` = sha256 of a canonical serialisation of the window, camera and every
  point of the tracks the rule reads.
- `rule_ir_hash` = `rule_ir_hash(rule)`.
- `strip_paths=()`: the pipeline fills them later (P4-05).

## Tests

```bash
uv run pytest tests/rules
```

Boundary tables (dwell 19.5/20.5 s, gaps 2/3/4 s, persist 0.9/1.1 s, distance 1.95/2.05 m,
`without` regions), the past/future sequence cases, absence and rate, every example rule in
`tests/fixtures/rules/` (handwash, excavator, fire exit, person down, forklift proximity),
Hypothesis order invariance, and byte-identical determinism. mypy runs in strict mode on this
package, and its coverage floor is 85% (CI) with a 90% target.
