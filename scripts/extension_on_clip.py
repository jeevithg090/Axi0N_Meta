"""Run an approved rule extension over a real clip: SAM 3.1 tracks, then the rules engine.

Usage (live: needs the SAM 3.1 endpoint settings, e.g. on the API pod):

    uv run python scripts/extension_on_clip.py CLIP.mp4 --extension pallet_above_line
        --sha 0123456789ab --seconds 8 --fps 5 --out report.json

1. ffmpeg cuts the first `--seconds` of the clip at `--fps` into one faststart chunk.
2. Hosted SAM 3.1 segments and tracks the extension's concept in it (one video call).
3. The detections become tracks (`perception.tracking.track_point`, grouped by SAM's object id).
4. The rules engine evaluates `StateCheck(object=<concept>, state="ext:<name>@<sha12>")` with the
   registry hook, so only the approved version runs, in the sandbox. Every sample's answer is
   recorded, and the engine's alert episodes are counted.

It prints and writes a JSON summary: SAM objects and seconds, samples with an alert, episodes,
and the check's own words for the first alerts. No track ids or people are reported.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from rulecam.clients.meta_hosted.sam import HostedSam31
from rulecam.clock import SystemClock
from rulecam.config import get_settings
from rulecam.contracts import Chunk, RuleIR, Track
from rulecam.contracts.rule_ir import StateCheck
from rulecam.extensions.registry import ExtensionRegistry, RegistryHook
from rulecam.extensions.sample import samples_from_scene
from rulecam.extensions.sandbox import run_check
from rulecam.perception.tracking import group_by_object, track_point
from rulecam.rules.engine import evaluate
from rulecam.rules.extension import ExtensionValues, extension_ref
from rulecam.rules.scene import Scene

START = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
MAX_WHY = 5


@dataclass
class Recording:
    """Delegates to the registry hook and keeps every answer it gave."""

    hook: RegistryHook
    values: list[ExtensionValues] = field(default_factory=list)
    scenes: list[Scene] = field(default_factory=list)

    def evaluate(self, node: StateCheck, scene: Scene) -> ExtensionValues | None:
        """See `rules.extension.ExtensionHook.evaluate`."""
        found = self.hook.evaluate(node, scene)
        if found is not None:
            self.values.append(found)
            self.scenes.append(scene)
        return found


def cut_chunk(clip: Path, out: Path, seconds: float, fps: float, camera_id: str) -> Chunk:
    """The clip's first `seconds` at `fps`, 1280x720, as one faststart H.264 chunk."""
    subprocess.run(  # noqa: S603 - fixed argv
        [  # noqa: S607 - ffmpeg from PATH
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(clip), "-t", f"{seconds}",
            "-vf", f"fps={fps},scale=1280:720", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(out),
        ],
        check=True,
    )  # fmt: skip
    return Chunk(
        chunk_id=f"{camera_id}_ext",
        camera_id=camera_id,
        path=str(out),
        start_ts=START,
        fps=fps,
        n_frames=round(seconds * fps),
        width=1280,
        height=720,
    )


def rule_for(concept: str, ref: str) -> RuleIR:
    """A rule whose whole predicate is the extension's state check."""
    return RuleIR.model_validate(
        {
            "id": "extension-on-clip",
            "source": {"kind": "text", "text": f"extension {ref}"},
            "concepts": {concept: {"prompt": concept}},
            "zones": {},
            "predicate": {"op": "state", "object": concept, "state": ref},
            "severity": "medium",
            "actions": [{"kind": "log"}],
        }
    )


def main() -> None:
    """Run the extension over the clip and print the summary."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("clip", type=Path)
    parser.add_argument("--extension", required=True)
    parser.add_argument("--sha", required=True, help="12+ hex of the approved version")
    parser.add_argument("--seconds", type=float, default=8.0)
    parser.add_argument("--fps", type=float, default=5.0)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    settings = get_settings()
    clock = SystemClock()
    registry = ExtensionRegistry(settings.rulecam_data_dir / "extensions", clock=clock)
    record = registry.runnable(args.extension, args.sha[:12])
    if record is None:
        raise SystemExit(f"{args.extension}@{args.sha[:12]} is not approved here")
    ref = extension_ref(record.name, record.sha256)
    sam = HostedSam31.from_settings(settings, clock=clock)
    with tempfile.TemporaryDirectory() as tmp:
        chunk = cut_chunk(
            args.clip, Path(tmp) / "chunk.mp4", args.seconds, args.fps, record.camera_id
        )
        started = time.perf_counter()
        detections = sam.segment(chunk, record.concept)
        sam_s = time.perf_counter() - started
    tracks = [
        Track(
            track_id=f"{record.camera_id}:{record.concept}:{oid}",
            camera_id=record.camera_id,
            concept=record.concept,
            points=tuple(track_point(d) for d in dets),
        )
        for oid, dets in group_by_object(detections).items()
    ]
    hook = Recording(RegistryHook(registry))
    t1 = START + timedelta(seconds=args.seconds)
    started = time.perf_counter()
    events = evaluate(
        rule_for(record.concept, ref), tracks, calibration=None, zones={}, t0=START, t1=t1,
        hz=args.fps, extensions=hook,
    )  # fmt: skip
    engine_s = time.perf_counter() - started
    truth = [bool(v) for values in hook.values for v in values.truth]
    known = [bool(v) for values in hook.values for v in values.known]
    whys: list[str] = []
    for scene in hook.scenes:
        frame = (record.frame_w, record.frame_h) if record.frame_w and record.frame_h else None
        samples = [
            s.as_json() for s in samples_from_scene(scene, record.concept, record.lines, frame)
        ]
        for outcome in run_check(record.code, samples):
            if outcome.result is not None and outcome.result.value and len(whys) < MAX_WHY:
                whys.append(outcome.result.why)
    summary = {
        "extension": ref,
        "clip": args.clip.name,
        "seconds": args.seconds,
        "fps": args.fps,
        "sam_detections": len(detections),
        "sam_objects": len(tracks),
        "sam_seconds": round(sam_s, 1),
        "samples": len(truth),
        "samples_known": sum(known),
        "samples_alerting": sum(truth),
        "alert_episodes": len(events),
        "engine_seconds": round(engine_s, 2),
        "first_alert_whys": whys,
    }
    text = json.dumps(summary, indent=2)
    if args.out is not None:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
