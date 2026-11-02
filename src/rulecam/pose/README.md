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
| `load_pose_estimator(path, sha256=RTMPOSE_BODY7_ONNX_SHA256)` | The runtime's loader (`RULECAM_POSE_ONNX`): the estimator, or `None` (logged) when the file, `onnxruntime` or the hash is missing or wrong |
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

- **RTMPose-l body7** (OpenMMLab, Apache-2.0; `RTMPOSE_BODY7_NAME`, COCO val AP 76.7): OpenMMLab's
  own ONNX export, pinned by URL and sha256 in `keypoints.py`. `scripts/fetch_rtmpose.sh [dir]`
  downloads it, checks both hashes and writes `rtmpose-l-body7.onnx`; set `RULECAM_POSE_ONNX` to
  it. Needs `onnxruntime` (the `ml` extra, or `pip install onnxruntime`). About 20 ms per person
  on a laptop CPU. Weights are not in the repo.
- **Sapiens** (Meta FAIR, CC-BY-NC-4.0): not wired up yet. It plugs in behind `PoseEstimator`.
  Sapiens outputs a different keypoint set than COCO-17, so a mapping is needed.

## Tests

```bash
uv run pytest tests/pose
```

## Known gaps

- **Only evidence strips use pose** (add-on 3.4): `runtime.strips` estimates each person at each
  strip keyframe, so skeletons, head blur and the `hands` PPE region follow the real body.
  `estimate_track` and `PoseStates` are still not wired into the runtime's rule evaluation, Rule
  CI or the audit re-check, so pose rules still evaluate false there. The same adapter has to go
  to all three, or every pose event fails the audit.
- **The ladder state can't fire through the engine:** no rule field names the ladder and nothing
  supplies `ladder_region`. It needs a design decision, possibly a contract change.
- The body7 ONNX ran once through `RtmposeOnnx` on a laptop (17 plausible keypoints, about
  21 ms per crop); `tests/pose/test_rtmpose_live.py` is the opt-in check (set
  `RULECAM_RTMPOSE_ONNX` and `RULECAM_RTMPOSE_SHA256`) that also records fps.
- The thresholds (60°, 0.8 torso lengths, 120°, 45°) are first guesses from synthetic skeletons.
