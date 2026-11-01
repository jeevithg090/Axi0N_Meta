# `rulecam.pose`

Pose keypoints for tracked people and the pure pose features the rule engine reads.
Owner: **P4** (task P4-04). Pose gives joints, never identity.

## Public API

| Name | What it does |
|---|---|
| `is_down(keypoints, box_xyxy)` | Torso more than 60° from vertical and hips close to the ground (box bottom). A person bending over is not "down" because their legs stay upright |
| `hands_above_shoulder(keypoints)` | Either wrist above its own shoulder. False only if both sides are known and neither is raised; one side known-false and the other unknown is unknown (`None`) |
| `ladder_three_point_lost(keypoints, ladder_box)` | Fewer than three of the two wrists and two ankles inside the ladder region |
| `bent_back_lift(keypoints, max_hip_angle_deg=120, min_torso_angle_deg=45)` | Hips flexed, torso inclined more than 45° from vertical, and hands below the knees. The torso test separates a stooped lift from a correct squat, where the back stays upright |
| `PoseStates(ladder_region=None)` | Adapter that satisfies `rulecam.rules.pose.PoseFeatures`; pass it as `evaluate(..., pose=PoseStates(...))` |
| `RtmposeOnnx(session, input_name)` / `RtmposeOnnx.load(path, sha256=...)` | RTMPose (256x192) estimator on an ONNX session, CPU. `load` requires the file's sha256 and refuses any other file. Frames are **BGR** (the pipeline's order) and are converted to RGB inside |
| `PoseEstimator` | Protocol `estimate(frame, box) -> 17 Keypoint`; the seam for a Sapiens GPU worker |
| `estimate_track(track, frame_at, estimator, stride=3, max_gap_s=1.5)` | Runs the model every `stride` points and fills the rest by interpolation; returns a new track. `frame_at` takes a **timestamp** |
| `sample_indices`, `fill_keypoints` | The sampling and interpolation steps on their own |

## Unknown keypoints

A keypoint scoring under 0.3 is unknown. A feature returns `None` when the keypoints it needs are
unknown, and the engine reads `None` as false with the unknown flag set. Two features reason about
what unknowns could still change: `ladder_three_point_lost` is True only if the limbs that are
inside, plus every unknown limb, still total under three.

## Interpolation

Between two estimated points, keypoints are blended linearly by **time** (`ts`), never `frame_idx`,
because frame indices restart in every 3-second chunk while a track window spans chunks. The weight
is kept between 0 and 1, and the score is the lower of the two. Points before the first or after the
last estimate, and points between estimates more than `max_gap_s` apart, get no keypoints.

## Models and licences

- **RTMPose** (OpenMMLab, Apache-2.0): export the body model at 256x192 to ONNX and pass the path
  to `RtmposeOnnx.load`. Needs the `ml` extra (`onnxruntime`). Weights are not in the repo.
- **Sapiens** (Meta FAIR, CC-BY-NC-4.0): not wired up yet. It plugs in behind `PoseEstimator`.
  Sapiens outputs a different keypoint set than COCO-17, so a mapping is needed.

## Tests

```bash
uv run pytest tests/pose
```

## Known gaps

- **Nothing calls this yet.** `estimate_track` and `PoseStates` are not wired into the runtime,
  Rule CI or the audit re-check, so pose rules still evaluate false there. The same adapter has to
  go to all three, or every pose event fails the audit.
- **The ladder state can't fire through the engine:** no rule field names the ladder and nothing
  supplies `ladder_region`. It needs a design decision, possibly a contract change.
- **No real weights have run.** `tests/pose/test_rtmpose_live.py` is an opt-in check (set
  `RULECAM_RTMPOSE_ONNX` and `RULECAM_RTMPOSE_SHA256`) that also records fps against the 5 to 10
  fps spec.
- The thresholds (60°, 0.8 torso lengths, 120°, 45°) are first guesses from synthetic skeletons.
