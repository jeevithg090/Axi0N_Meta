"""Ask over the crowd: the crowd tables in SQL and the `crowd_scan` tool."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path

from rulecam.ask import AskAgent, FrameGrabber, TrackTables, open_tables
from rulecam.ask.data import CrowdFiles
from rulecam.crowd.scan import CrowdReading, ZoneReading
from rulecam.crowd.zones import CrowdZone
from tests.ask.test_ask import Chunks, Sam, turn, write_frame
from tests.fakes import EPOCH, FakeClock, FakeLLM


def crowd_files(tmp_path: Path) -> CrowdFiles:
    readings = tmp_path / "readings.jsonl"
    rows = [
        {
            "camera_id": "gate",
            "ts": (EPOCH + timedelta(seconds=5 * k)).isoformat(),
            "zone": "view",
            "people": 40 + 10 * k,
            "density_ppm2": 2.0 + k,
            "speed_mps": 1.0 - 0.3 * k,
        }
        for k in range(3)
    ]
    readings.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    alerts = tmp_path / "alerts.jsonl"
    alerts.write_text(
        json.dumps(
            {
                "alert_id": "crowd-1",
                "camera_id": "gate",
                "ts": EPOCH.isoformat(),
                "level": "warning",
                "sent": True,
                "held_reason": None,
            }
        )
        + "\n"
    )
    return CrowdFiles(readings=readings, alerts=alerts)


def test_crowd_tables_load_with_utc_timestamps(tmp_path: Path) -> None:
    tables = TrackTables(tmp_path / "none", crowd=crowd_files(tmp_path))
    rising = tables.query(
        "SELECT max(density_ppm2) - min(density_ppm2), max(ts) FROM crowd_readings"
    )
    assert rising.rows[0][0] == 2.0
    assert str(rising.rows[0][1]).startswith("2026-10-30")
    assert tables.query("SELECT level FROM crowd_alerts").rows == [["warning"]]
    assert tables.query("SELECT count(*) FROM crowd_assessments").rows == [[0]]
    tables.close()


class Crowd:
    """`CrowdMeasure` stand-in: a fixed reading per instant, and a heatmaps folder."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.asked: list[tuple[str, datetime, list[str], str | None, int | None]] = []

    @property
    def heatmaps(self) -> Path:
        return self.folder

    def measure(
        self,
        camera_id: str,
        at: datetime,
        zones: Sequence[CrowdZone] = (),
        *,
        concept: str | None = None,
        grid: int | None = None,
    ) -> CrowdReading:
        self.asked.append((camera_id, at, [z.name for z in zones], concept, grid))
        if at > EPOCH + timedelta(minutes=1):
            msg = "no recorded frame"
            raise ValueError(msg)
        name = "crowd-gate-1.jpg"
        (self.folder / name).write_bytes(b"jpg")
        view = ZoneReading(zone="view", people=120, density_ppm2=4.4)
        return CrowdReading(
            camera_id=camera_id,
            at=at,
            concept=concept or "person",
            grid=grid or 2,
            people=120,
            calibration="moge2",
            zones=[view],
            heatmap=name,
            frame="f.jpg",
        )


def test_spark_scans_the_crowd_in_a_zone_it_draws(tmp_path: Path) -> None:
    crowd = Crowd(tmp_path)
    at, late = EPOCH + timedelta(seconds=6), EPOCH + timedelta(minutes=5)
    spark = FakeLLM(
        [
            turn(
                {
                    "thought": "density at the gate",
                    "action": "crowd_scan",
                    "camera_id": "gate",
                    "times": [at.isoformat(), late.isoformat()],
                    "concept": "head",
                    "grid": 3,
                    "zones": [{"name": "turnstiles", "polygon_px": [[0, 0], [100, 0], [100, 100]]}],
                }
            ),
            turn({"thought": "done", "action": "answer", "answer": "4.4 people per square metre."}),
        ]
    )
    agent = AskAgent(
        spark=spark,
        segmenter=Sam(),
        glimmer=FakeLLM([]),
        glimmer_model="muse-glimmer",
        tables=open_tables(tmp_path / "none", crowd=crowd_files(tmp_path)),
        frames=FrameGrabber(Chunks(), tmp_path / "frames", extract=write_frame),  # type: ignore[arg-type]
        clock=FakeClock(EPOCH + timedelta(minutes=5)),
        crowd=crowd,
    )
    answer = agent.ask("How packed is the turnstile area?")
    assert answer.answer == "4.4 people per square metre."
    step = answer.steps[0]
    assert step.model == "sam-3.1"
    assert step.frames == ["crowd-gate-1.jpg"]
    result = step.result
    assert isinstance(result, dict)
    assert result["readings"][0]["zones"][0]["density_ppm2"] == 4.4
    assert "no recorded frame" in result["notes"][0]
    assert crowd.asked[0] == ("gate", at, ["turnstiles"], "head", 3)
    assert agent.frame_file("crowd-gate-1.jpg") == tmp_path / "crowd-gate-1.jpg"
    system = str(spark.calls[0]["messages"][0]["content"])  # type: ignore[index]
    assert "crowd_readings" in system
    assert "crowd_scan" in system


def test_crowd_scan_without_crowd_watch_says_so(tmp_path: Path) -> None:
    spark = FakeLLM(
        [
            turn(
                {
                    "thought": "scan",
                    "action": "crowd_scan",
                    "camera_id": "gate",
                    "times": [EPOCH.isoformat()],
                }
            ),
            turn({"thought": "done", "action": "answer", "answer": "Crowd watch is off."}),
        ]
    )
    agent = AskAgent(
        spark=spark,
        segmenter=Sam(),
        glimmer=FakeLLM([]),
        glimmer_model="muse-glimmer",
        tables=open_tables(tmp_path / "none"),
        frames=FrameGrabber(Chunks(), tmp_path / "frames", extract=write_frame),  # type: ignore[arg-type]
        clock=FakeClock(EPOCH),
    )
    answer = agent.ask("How packed is it?")
    assert answer.steps[0].result == {
        "error": "crowd_scan is off here (RULECAM_CROWD_EVERY_S is 0)"
    }
