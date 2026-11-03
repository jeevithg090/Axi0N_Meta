r"""Cross-check `rulecam eval track` against TrackEval on one CAVIAR sequence (add-on 1.5).

Picks one clip (`--segment`) out of a chained CAVIAR stream, scores the camera's stored tracks
for that clip's seconds with our scorer (`rulecam.eval.tracking`) and with TrackEval, on the
same frames (the frames the runtime tracked), and prints both side by side. They should agree
to within rounding; a gap means one of the two implementations is wrong.

Run where the track store is (the API pod), from the repository:

    ulimit -n 65536   # the store has many small Parquet files
    uv run --with git+https://github.com/JonathonLuiten/TrackEval \\
      python scripts/eval/trackeval_crosscheck.py \\
      --store /workspace/rulecam/data/tracks --camera phone-68 \\
      --start 2026-11-02T09:44:11.875Z --scale 0.5 --segment TwoEnterShop1cor \\
      --caviar /workspace/caviar/WalkByShop1cor.xml ... (all seven, stream order)
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from trackeval_bridge import evaluate

from rulecam.eval.caviar import CAVIAR_FPS, chain_mot, load_caviar, mot_rows
from rulecam.eval.motchallenge import SeqInfo, restrict_gt, tracker_rows, write_layout
from rulecam.eval.tracking import parse_mot, score_tracking
from rulecam.eval.trackstore import stored_boxes
from rulecam.rules.store import ParquetTrackStore


def _args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--camera", required=True)
    parser.add_argument("--start", required=True, help="camera time of the stream's frame 1")
    parser.add_argument("--caviar", type=Path, nargs="+", required=True, help="stream order")
    parser.add_argument("--segment", required=True, help="the clip to check, e.g. TwoEnterShop1cor")
    parser.add_argument("--scale", type=float, default=1.0, help="stored px -> GT px")
    parser.add_argument("--width", type=int, default=384, help="GT frame width")
    parser.add_argument("--height", type=int, default=288, help="GT frame height")
    parser.add_argument("--out", type=Path, help="write the comparison as JSON")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    """Score one segment both ways and print the comparison; returns an exit code."""
    args = _args(argv)
    sequences = [load_caviar(path) for path in args.caviar]
    _, segments = chain_mot(sequences)
    picked = [i for i, seg in enumerate(segments) if seg.name == args.segment]
    if not picked:
        print(f"no segment {args.segment!r}; have {[s.name for s in segments]}", file=sys.stderr)
        return 2
    segment, sequence = segments[picked[0]], sequences[picked[0]]
    start = datetime.fromisoformat(args.start.replace("Z", "+00:00")).astimezone(UTC)
    t0 = start + timedelta(seconds=segment.start_s)
    t1 = start + timedelta(seconds=segment.end_s)
    with ParquetTrackStore(args.store) as store:
        stored = stored_boxes(store, args.camera, t0, t1)
    # Both scorers see the same samples: those landing on one of the clip's frames.
    boxes = [b for b in stored if round(b.clip_s * CAVIAR_FPS) + 1 <= sequence.frames]
    gt_rows, _ = mot_rows(sequence)
    scale = (args.scale, args.scale)
    ours = score_tracking(parse_mot(gt_rows, {1: "person"}), boxes, fps=CAVIAR_FPS, scale=scale)[0]
    rows, frames = tracker_rows(boxes, fps=CAVIAR_FPS, scale=scale, concept="person")
    with tempfile.TemporaryDirectory(prefix="trackeval-") as tmp:
        root = Path(tmp)
        info = SeqInfo(segment.name, CAVIAR_FPS, sequence.frames, args.width, args.height)
        write_layout(root, info, restrict_gt(gt_rows, frames), rows)
        reference = evaluate(root, {segment.name: sequence.frames})[segment.name]
    comparison = {
        "segment": segment.name,
        "camera": args.camera,
        "window": [t0.isoformat(), t1.isoformat()],
        "frames_scored": ours.frames,
        "ours": {"hota": ours.hota, "idf1": ours.idf1, "mota": ours.mota, "idsw": ours.id_switches},
        "trackeval": {
            "hota": reference.hota,
            "idf1": reference.idf1,
            "mota": reference.mota,
            "idsw": reference.id_switches,
        },
    }
    print(f"{'metric':<8}{'ours':>10}{'TrackEval':>12}")
    for key in ("hota", "idf1", "mota", "idsw"):
        print(f"{key:<8}{comparison['ours'][key]:>10.4f}{comparison['trackeval'][key]:>12.4f}")
    if args.out:
        args.out.write_text(json.dumps(comparison, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
