# `rulecam.rules`

The pure predicate and temporal engine: turns a Rule IR and a window of tracks into
`CandidateEvent`s. Owner: **P1** (task P1-06). If this engine is wrong, every rule is wrong, so
the semantics below are the contract that the tests pin down.

## Public API

| Name | What it does |
|---|---|
| `evaluate(rule, tracks, *, calibration, zones, t0, t1, hz=10.0, episode_gap_s=2.0, cooldown_s=0.0, plan_version_hash="")` | Pure: no I/O, no clock, no randomness. Identical inputs give byte-identical output; the order of `tracks` does not matter. Returns candidates sorted by `(ts_start, camera_id, event_id)` |
| `rule_ir_hash(rule)` | sha256 of the rule's canonical JSON (sorted keys) |
| `RuleEvaluationError` | Invalid window, unknown or incomplete zone, `distance_m` without a bound |
| `UnsupportedPredicateError` | An op this version does not evaluate yet |

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
| `dwell` | the running inside-time of the current visit has reached `total_s_gte`; runs separated by gaps ≤ `max_gap_s` form one visit and gaps are not counted; stays true until the visit's last inside sample | running total (`s`) |

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

Boundary tables (dwell 19.5/20.5 s, gaps 2/3/4 s, distance 1.95/2.05 m, `without` regions),
Hypothesis order invariance, and byte-identical determinism. mypy runs in strict mode on this
package, and its coverage floor is 85% (CI) with a 90% target.
