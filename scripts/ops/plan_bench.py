"""Time "Plan this rule": plan one rule N times on one camera fed a looping clip.

Pairs one phone camera on the API, feeds it the clip in 3 s chunks (one a second, the pairing
limit), waits 25 s so the planner has recent frames, then requests the rule N times in a row and
prints each plan's request-to-plan time and per-step times (`GET /warehouse/story`). The first
plan surveys the camera; later ones reuse that survey while the scene is unchanged.

    uv run python scripts/ops/plan_bench.py CLIP "Keep people 4 m from a moving forklift." en 3 \
        --api http://localhost:8000 --out /tmp/plan-bench
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

import httpx

from rulecam.eval.client import HttpApiClient
from rulecam.eval.runner import split_clip

FEED_INTERVAL_S = 1.1
WARMUP_S = 25.0
PLAN_TIMEOUT_S = 900.0


def main() -> int:
    """Run the bench; one JSON line per plan on stdout, all of them in `<out>/bench.json`."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("clip", type=Path)
    parser.add_argument("text")
    parser.add_argument("lang")
    parser.add_argument("runs", type=int)
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--out", type=Path, default=Path("plan-bench"))
    args = parser.parse_args()
    api = HttpApiClient(args.api)
    pairing = api.pair("plan-bench")
    chunks = split_clip(args.clip, args.out / "chunks")
    stop = threading.Event()

    def feed() -> None:
        while not stop.is_set():
            for chunk in chunks:
                if stop.is_set():
                    return
                try:
                    api.upload(pairing, chunk.path)
                except Exception as exc:
                    sys.stderr.write(f"upload failed: {type(exc).__name__}\n")
                time.sleep(FEED_INTERVAL_S)

    threading.Thread(target=feed, daemon=True).start()
    time.sleep(WARMUP_S)
    results = []
    for run in range(1, args.runs + 1):
        started = time.monotonic()
        plan_id = api.request_plan(args.text, args.lang)
        while api.plan(plan_id) is None and time.monotonic() - started < PLAN_TIMEOUT_S:
            time.sleep(0.5)
        wall = time.monotonic() - started
        story = httpx.get(
            f"{args.api}/warehouse/story", params={"plan_id": plan_id}, timeout=30
        ).json()
        planning = story.get("planning") or {}
        row = {
            "run": run,
            "plan_id": plan_id,
            "wall_s": round(wall, 1),
            "total_s": planning.get("total_s"),
            "steps": {s["step"]: s["seconds"] for s in planning.get("steps", [])},
            "reused": [s["detail"] for s in planning.get("steps", []) if s.get("detail")],
        }
        results.append(row)
        sys.stdout.write(json.dumps(row) + "\n")
        sys.stdout.flush()
    stop.set()
    (args.out / "bench.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
