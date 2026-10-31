# `rulecam.privacy`

Anonymous Mode: turns people into skeletons on a clean background, masks faces in standard mode,
renders the six-frame evidence strip for a privacy mode, and makes the skeleton wipe.
Owner: **P4** (task P4-05). Design: `docs/v2-design.md` Pillar 7b, MP §7.5.
Frames are `uint8` BGR images (OpenCV order), height x width x 3.

## Public API

| Name | What it does |
|---|---|
| `BackgroundPlate(shape, window=15, dilate_px=5)` | Running per-pixel median of person-free pixels. `update(frame, person_masks)` adds a frame, `seed_empty(frame)` pins a known-empty frame, `render()` returns the plate, `seen_empty()` the pixels that have been seen empty. Pixels never seen empty are filled from a wide blur of the plate around them (grey if nothing is known); no generative inpainting |
| `render_skeletons(plate, people, mode)` | Draws bones and joints on a copy of the plate, in a colour hashed from the track id, with PPE glyphs. `anonymous` draws the pose as it is; `strict` rebuilds it with canonical bone lengths. `standard` is refused |
| `PersonRender(track_id, keypoints, ppe)` | One person to draw. `ppe` maps `helmet`, `vest`, `gloves` or `harness` to True (green), False (red) or None (grey outline) |
| `ppe_from_trace(trace, paths)` | Reads PPE states from a candidate's `predicate_trace`, given the trace path of each item's `without` predicate |
| `canonical_pose(joints)` | The strict-mode rebuild: each bone keeps its direction and gets a fixed length (a multiple of the torso length), and face points other than the nose are dropped |
| `blur_faces(frame, face_masks)` | Blurs each SAM `face` mask, grown a few pixels, with a blur that scales with the face |
| `render_strip(event, mode, source, plate, out_dir, frames=6)` | Writes the event's strip as PNGs and returns their paths. Standard mode: real frames with mask overlays and blurred faces. Anonymous and strict: skeleton renders only |
| `wipe_frame`, `write_wipe_video` | The skeleton wipe: skeleton to the left of a bar, raw to the right. `write_wipe_video` refuses to run unless `consent_confirmed=True` |

## What the guardrails are, and what backs them

- **The renderer reads keypoints, not pixels.** Clothing colour, skin and faces cannot reach a
  skeleton render because nothing draws from the person's pixels. `tests/privacy` renders a scene
  with a sentinel-coloured person and checks no output contains that colour.
- **`render_strip` never writes or returns a raw frame in anonymous or strict mode.**
- **The plate is built only from pixels no person mask covered**, and masks are grown by
  `dilate_px` so a ragged edge leaves no halo.

## Honest limits

- **The plate trusts the person masks.** A person SAM misses, or one who never moves and is only
  partly masked, becomes part of the plate. Seed an empty frame (`seed_empty`) where you can.
- **Skeletons reduce re-identification risk; they do not remove it.** Gait, context ("the only
  person on the night shift") and the track colour can still point to someone. Say
  "privacy-preserving by design", never "anonymous" or "impossible to identify".
- Strict mode removes proportions and the face points; a person whose torso is not known is not
  drawn at all in strict mode, which loses information rather than leaking it.
- A skeleton is only as good as the pose model; a missed person is simply not drawn.
- `write_wipe_video` writes MPEG-4 Part 2 (`mp4v`). Re-encode it with ffmpeg for editing.
- Face blur needs SAM `face` masks and applies to standard mode only.

## Tests

```bash
uv run pytest tests/privacy
```
