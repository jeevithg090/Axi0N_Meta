"""Live check of the glasses assistant: real frames and spoken questions through `/wearables`.

Sends each case to a running Rulecam API (`POST /wearables/look`, waiting for the answer), then
runs one safety walk of three patrol frames and finishes it. Writes every answer, its stages and
the client-side wall time to a JSON file. Nothing here is simulated: the frames are stock video
stills and the questions are WAVs spoken by the system voice or typed.

Usage:
    uv run python scripts/eval/glasses_live.py --api http://127.0.0.1:8000 \
        --frames data/wearables-eval --out reports/eval/2026-11-04-glasses-a.json --label a
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

TIMEOUT_S = 400.0


@dataclass(frozen=True)
class Case:
    """One frame and what is asked about it."""

    name: str
    frame: str
    mode: str
    question: str = ""
    audio: str = ""


CASES = (
    Case(
        "warehouse-typed", "warehouse-forklift-ahead.jpg", "warehouse", "Is it safe to walk ahead?"
    ),
    Case("warehouse-spoken", "warehouse-forklift-side.jpg", "warehouse", audio="q-forklift.wav"),
    Case("warehouse-patrol", "warehouse-walk.jpg", "warehouse"),
    Case("aisle-spoken", "bread-8.jpg", "aisle", audio="q-shelf.wav"),
    Case("aisle-typed", "bread-0.jpg", "aisle", "Which shelf facings are empty here?"),
    Case("crowd-spoken", "store-crowd.jpg", "crowd", audio="q-crowd.wav"),
    Case("crowd-patrol", "store-checkout.jpg", "crowd"),
    Case("general-typed", "store-checkout.jpg", "general", "How many people are queueing here?"),
)
WALK = ("warehouse-walk.jpg", "warehouse-forklift-ahead.jpg", "warehouse-forklift-side.jpg")


def look(client: httpx.Client, frames: Path, case: Case, walk_id: str = "") -> dict[str, Any]:
    """Send one case and return the answer with the client's wall time."""
    files: dict[str, tuple[str, bytes, str]] = {
        "image": (case.frame, (frames / case.frame).read_bytes(), "image/jpeg")
    }
    if case.audio:
        files["audio"] = (case.audio, (frames / case.audio).read_bytes(), "audio/wav")
    data = {"mode": case.mode, "source": "upload", "question": case.question, "walk_id": walk_id}
    started = time.perf_counter()
    reply = client.post("/wearables/look", files=files, data=data)
    wall_ms = round((time.perf_counter() - started) * 1000)
    reply.raise_for_status()
    return {"case": case.name, "frame": case.frame, "wall_ms": wall_ms, "answer": reply.json()}


def summary_line(row: dict[str, Any]) -> str:
    """One line per answer for the console."""
    answer = row["answer"]
    stages = " ".join(f"{s['name']}={s['ms']}{'' if s['ok'] else '!'}" for s in answer["stages"])
    return (
        f"{row['case']:<18} {answer['total_ms'] / 1000:6.1f}s verified={answer['verified']!s:<5} "
        f"by={answer['looked_by'][:28]:<28} {stages} | {answer['spoken'][:90]}"
    )


def main() -> None:
    """Run the cases and the walk."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--frames", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--label", default="")
    args = parser.parse_args()
    rows: list[dict[str, Any]] = []
    with httpx.Client(base_url=args.api, timeout=TIMEOUT_S) as client:
        status = client.get("/wearables/status").json()
        for case in CASES:
            rows.append(look(client, args.frames, case))
            print(summary_line(rows[-1]))
        walk = client.post("/wearables/walks", json={"mode": "warehouse", "note": "eval"}).json()
        walk_rows = []
        for frame in WALK:
            row = look(
                client, args.frames, Case(f"walk-{frame}", frame, "warehouse"), walk["walk_id"]
            )
            walk_rows.append(row)
            print(summary_line(row))
        started = time.perf_counter()
        report = client.post(f"/wearables/walks/{walk['walk_id']}/finish").json()
        report_ms = round((time.perf_counter() - started) * 1000)
        print(f"walk report {report_ms / 1000:.1f}s: {report['summary']['summary'][:200]}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "label": args.label,
                "status": status,
                "cases": rows,
                "walk": {"answers": walk_rows, "report": report, "report_wall_ms": report_ms},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
