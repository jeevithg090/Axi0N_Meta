r"""Track the MOT17 training sequences with the runtime's tracker and score them (add-on 1.5).

So our HOTA and IDF1 can sit next to published trackers' numbers, this runs the runtime's own
image-mode tracker (SAM 3.1 keyframes on the GPU endpoint + ByteTrack, the `person` prompt) over
the seven MOT17 training sequences and scores them twice:

- with `rulecam.eval.tracking` (ground truth: rows with consider = 1 and class 1, pedestrian);
- with TrackEval when it is installed (the MOTChallenge reference, which also removes matches to
  distractor classes), on the same frames.

The tracker samples keyframes (`--keyframe-fps`, 2 to 10 per second), so only those frames are
scored, as `rulecam eval run` does. Published MOT17 numbers score every frame: they are only
comparable at the highest keyframe rate, and the report says which rate was used.

MOT17 (MOTChallenge, CC BY-NC-SA 3.0) is about 5.5 GB; it is downloaded once into `--root`.
Run on the API pod (it needs the SAM endpoint and its keys from the API's `.env`):

    uv run --with git+https://github.com/JonathonLuiten/TrackEval \\
      python scripts/eval/mot17.py --root /workspace/data/mot17 --keyframe-fps 10 \\
      --out reports/eval/$(date +%F)-mot17.json

Tracks go to a separate store under `--root/run/tracks`, never the live one.
"""

from __future__ import annotations

import argparse
import configparser
import json
import subprocess
import sys
import zipfile
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from rulecam.bus import Bus
from rulecam.capture.chunker import probe_chunk
from rulecam.clock import SystemClock
from rulecam.config import get_settings
from rulecam.contracts import Chunk
from rulecam.eval.motchallenge import SeqInfo, restrict_gt, tracker_rows, write_layout
from rulecam.eval.record import PredictedBox
from rulecam.eval.runner import split_clip
from rulecam.eval.tracking import parse_mot, score_tracking
from rulecam.replay.cache import ReplayCache
from rulecam.runtime.factory import RuntimeTracker, _image_tracker, build_perception
from rulecam.runtime.timings import StageTimings

MOT17_URL = "https://motchallenge.net/data/MOT17.zip"
TRAIN = ("MOT17-02", "MOT17-04", "MOT17-05", "MOT17-09", "MOT17-10", "MOT17-11", "MOT17-13")
DETECTOR = "FRCNN"  # every detector folder holds the same images and ground truth
CONSIDER, CLASS, PEDESTRIAN = 6, 7, 1
EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


def download(root: Path) -> Path:
    """Download and unzip MOT17 once; return the train folder."""
    train = root / "MOT17" / "train"
    if train.is_dir():
        return train
    root.mkdir(parents=True, exist_ok=True)
    archive = root / "MOT17.zip"
    if not archive.is_file():
        print(f"downloading {MOT17_URL} (about 5.5 GB) to {archive}")
        part = archive.with_suffix(".part")
        with (
            httpx.stream("GET", MOT17_URL, follow_redirects=True, timeout=600.0) as reply,
            part.open("wb") as handle,
        ):
            reply.raise_for_status()
            for block in reply.iter_bytes(1 << 20):
                handle.write(block)
        part.replace(archive)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(root)
    return train


def seq_info(seq_dir: Path) -> SeqInfo:
    """Read a MOT17 sequence's `seqinfo.ini`."""
    ini = configparser.ConfigParser()
    ini.read(seq_dir / "seqinfo.ini")
    sec = ini["Sequence"]
    return SeqInfo(
        name=sec["name"].rsplit("-", 1)[0],
        fps=float(sec["frameRate"]),
        length=int(sec["seqLength"]),
        width=int(sec["imWidth"]),
        height=int(sec["imHeight"]),
    )


def pedestrian_rows(gt: Path) -> list[str]:
    """Ground-truth rows that MOT17 counts: consider flag 1, class 1 (pedestrian)."""
    out = []
    for line in gt.read_text(encoding="utf-8").splitlines():
        cols = line.split(",")
        if len(cols) > CLASS and int(float(cols[CONSIDER])) == 1 and int(float(cols[CLASS])) == 1:
            out.append(line)
    return out


def encode(seq_dir: Path, info: SeqInfo, out: Path) -> Path:
    """Encode `img1/*.jpg` into one H.264 MP4 at the sequence's frame rate."""
    video = out / f"{info.name}.mp4"
    if not video.is_file():
        out.mkdir(parents=True, exist_ok=True)
        subprocess.run(  # noqa: S603 - fixed arguments, local paths
            [  # noqa: S607 - ffmpeg from PATH, as the chunker uses it
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-framerate",
                f"{info.fps:g}",
                "-i",
                str(seq_dir / "img1" / "%06d.jpg"),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(video),
            ],
            check=True,
        )
    return video


def track_sequence(
    video: Path, info: SeqInfo, work: Path, runtime: RuntimeTracker
) -> list[PredictedBox]:
    """Run the image tracker over a sequence's chunks; return its samples in clip seconds."""
    camera = f"mot17-{info.name.lower()}"
    boxes: list[PredictedBox] = []
    for local in split_clip(video, work / info.name):
        probed = probe_chunk(local.path)
        chunk = Chunk(
            chunk_id=f"{camera}-{local.index:05d}",
            camera_id=camera,
            path=str(local.path),
            start_ts=EPOCH + timedelta(seconds=local.clip_start_s),
            fps=probed.fps,
            n_frames=probed.n_frames,
            width=probed.width,
            height=probed.height,
        )
        tracker = runtime.tracker
        for track in tracker.stitch(chunk, tracker.segment(chunk, ["person"])):
            boxes.extend(
                PredictedBox(
                    clip_s=(p.ts - EPOCH).total_seconds(),
                    track=track.track_id,
                    concept=track.concept,
                    box_xyxy=p.box_xyxy,
                )
                for p in track.points
            )
    return boxes


def main(argv: list[str]) -> int:
    """Track and score the MOT17 training sequences; returns an exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path("data/mot17"))
    parser.add_argument("--keyframe-fps", type=float, default=10.0)
    parser.add_argument("--seq", nargs="*", default=list(TRAIN))
    parser.add_argument("--out", type=Path, default=Path("reports/eval/mot17.json"))
    args = parser.parse_args(argv)
    train = download(args.root)
    run_dir = args.root / "run"
    settings = get_settings().model_copy(
        update={"rulecam_data_dir": run_dir, "rulecam_keyframe_fps": args.keyframe_fps}
    )
    clock, bus = SystemClock(), Bus()
    cache = ReplayCache.from_settings(settings, clock=clock, bus=bus)
    perception = build_perception(settings, cache=cache, clock=clock, bus=bus)
    runtime = _image_tracker(settings, perception, clock=clock, bus=bus, timings=StageTimings())
    results, lengths = [], {}
    layout = run_dir / "trackeval"
    for name in args.seq:
        seq_dir = train / f"{name}-{DETECTOR}"
        info = seq_info(seq_dir)
        boxes = track_sequence(encode(seq_dir, info, run_dir / "video"), info, run_dir, runtime)
        gt_rows = pedestrian_rows(seq_dir / "gt" / "gt.txt")
        ours = score_tracking(parse_mot(gt_rows, {1: "person"}), boxes, fps=info.fps)[0]
        rows, frames = tracker_rows(boxes, fps=info.fps, concept="person")
        frames = {f for f in frames if f <= info.length}
        rows = [r for r in rows if int(r.split(",", 1)[0]) <= info.length]
        full_gt = (seq_dir / "gt" / "gt.txt").read_text(encoding="utf-8").splitlines()
        write_layout(layout, info, restrict_gt(full_gt, frames), rows)
        lengths[info.name] = info.length
        results.append({"seq": info.name, "points": len(boxes), "ours": asdict(ours)})
        print(f"{info.name}: HOTA {ours.hota:.3f} IDF1 {ours.idf1:.3f} MOTA {ours.mota:.3f}")
    try:
        from trackeval_bridge import evaluate  # noqa: PLC0415 - optional, needs TrackEval

        reference = evaluate(layout, lengths)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        reference = {}
    for row in results:
        found = reference.get(str(row["seq"]))
        row["trackeval"] = asdict(found) if found else None
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "keyframe_fps": args.keyframe_fps,
        "detector_folder": DETECTOR,
        "note": "only keyframes are scored; published MOT17 numbers score every frame",
        "sequences": results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
