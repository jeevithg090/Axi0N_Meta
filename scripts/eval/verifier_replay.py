r"""Replay the labelled verifier strips to Glimmer in two strip formats (add-on 1.4).

For every item of a labelling sheet (`rulecam eval verifier-sheet`), Glimmer is asked again with

- `raw8_960`: the strip as prototype mode rendered it (8 raw frames at 960 px), and
- `blur6_640`: the pre-prototype format built from it (6 of the 8 frames, 640 px, faces
  blurred with YuNet when `RULECAM_FACE_MODEL` is set),

through the normal Glimmer client and replay cache. Each answer is a JSON line in `--out`; with
the labellers' CSVs the two formats are scored against their labels (`verifier_set.score_labels`).

Run on the API pod (the strips and Glimmer's endpoint and keys are there), from the repository:

    uv run python scripts/eval/verifier_replay.py --key data/verifier-sheet/key.json \
      --strips data/strips --out reports/eval/$(date +%F)-verifier-replay.jsonl \
      --labels labels-alice.csv labels-bob.csv
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from rulecam.bus import Bus
from rulecam.clients.open_weights.glimmer import GlimmerClient
from rulecam.clock import SystemClock
from rulecam.config import get_settings
from rulecam.eval.verifier_set import (
    Image,
    VerdictItem,
    candidate_for,
    downgrade_strip,
    load_key,
    read_labels,
    rule_ir,
    score_labels,
    strip_dir,
    strip_frames,
)
from rulecam.replay.cache import ReplayCache
from rulecam.runtime.strip_draw import NoFaceDetector, blur_boxes
from rulecam.runtime.strips import face_detector
from rulecam.verify.tier1 import GlimmerVerifier

NEW, OLD = "raw8_960", "blur6_640"


def variants(
    item: VerdictItem, strips: Path, work: Path, blur: Callable[[Image], Image]
) -> dict[str, list[Path]]:
    """The item's strip in both formats (empty when the strip is not on disk)."""
    frames = strip_frames(strip_dir(strips, item.camera_id, item.event_id))
    if not frames:
        return {}
    return {NEW: frames, OLD: downgrade_strip(frames, work / item.item, blur=blur)}


def summary(rows: list[dict[str, object]], variant: str) -> str:
    """One line per format: verdict counts and median latency."""
    mine = [r for r in rows if r["variant"] == variant]
    counts = {
        v: sum(r["verdict"] == v for r in mine) for v in ("violation", "no_violation", "unsure")
    }
    latencies = [int(str(r["latency_ms"])) for r in mine]
    median = statistics.median(latencies) / 1000 if latencies else 0.0
    return f"{variant}: {len(mine)} verdicts {counts}, median {median:.1f} s"


def main(argv: list[str]) -> int:
    """Replay every item in both formats; returns an exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--key", type=Path, default=Path("data/verifier-sheet/key.json"))
    parser.add_argument("--strips", type=Path, default=Path("data/strips"))
    parser.add_argument("--out", type=Path, default=Path("reports/eval/verifier-replay.jsonl"))
    parser.add_argument("--labels", type=Path, nargs="*", default=[])
    args = parser.parse_args(argv)
    settings = get_settings()
    clock, bus = SystemClock(), Bus()
    client = GlimmerClient.from_settings(
        settings,
        cache=ReplayCache.from_settings(settings, clock=clock, bus=bus),
        clock=clock,
        bus=bus,
    )
    verifier = GlimmerVerifier(client)
    faces = face_detector(settings.rulecam_face_model)
    if isinstance(faces, NoFaceDetector):
        print(
            "warning: no RULECAM_FACE_MODEL, so blur6_640 strips are not face-blurred",
            file=sys.stderr,
        )

    def blur(image: Image) -> Image:
        return blur_boxes(image, faces.detect(image))

    rows: list[dict[str, object]] = []
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="strips-") as tmp, args.out.open("w") as sink:
        for item in load_key(args.key):
            found = variants(item, args.strips, Path(tmp), blur)
            if not found:
                print(f"{item.item}: no strip for {item.event_id}", file=sys.stderr)
                continue
            for variant, frames in found.items():
                verdict = verifier.verify(
                    candidate_for(item, frames, variant=variant), rule_ir(item)
                )
                row = {
                    "item": item.item,
                    "event_id": item.event_id,
                    "variant": variant,
                    "frames": len(frames),
                    "verdict": verdict.verdict,
                    "score": verdict.score,
                    "latency_ms": verdict.latency_ms,
                    "reason": verdict.reason,
                    "original_verdict": item.verdict,
                }
                rows.append(row)
                sink.write(json.dumps(row) + "\n")
                print(f"{item.item} {variant}: {verdict.verdict} {verdict.score:.2f}")
    for variant in (NEW, OLD):
        print(summary(rows, variant))
    if args.labels:
        given = {path.stem: read_labels(path) for path in args.labels}
        for variant in (NEW, OLD):
            verdicts = {str(r["item"]): str(r["verdict"]) for r in rows if r["variant"] == variant}
            report = score_labels(verdicts, given)
            path = args.out.with_name(f"{args.out.stem}-{variant}-score.json")
            path.write_text(report.to_json(), encoding="utf-8")
            print(f"{variant}: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
