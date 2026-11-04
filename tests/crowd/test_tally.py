"""The hottest zone counted three ways, and the phase meter."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import cv2
import numpy as np

from rulecam.ask.vision import FrameGrabber
from rulecam.crowd.forecast import forecast
from rulecam.crowd.memory import CrowdMemory
from rulecam.crowd.scan import CrowdReading, ZoneReading
from rulecam.crowd.tally import Tallier, hottest
from rulecam.crowd.warden import CrowdWarden
from rulecam.crowd.zones import CrowdZone
from tests.crowd.conftest import wait_for
from tests.crowd.test_warden import INSTRUCTION, crowd, latest, warning
from tests.fakes import EPOCH, FakeClock, FakeLLM

__all__ = ["crowd"]

GATE = CrowdZone(name="gate", polygon_px=[(100, 100), (300, 100), (300, 200), (100, 200)])


class Files:
    def attach(self, path: Path) -> dict[str, object]:
        return {"type": "input_image", "file_id": path.name}


def reading(frame: str | None, people: int = 10, density: float = 5.0) -> CrowdReading:
    return CrowdReading(
        camera_id="gate",
        at=EPOCH,
        concept="head",
        grid=1,
        people=people + 3,
        calibration="owner",
        frame=frame,
        zones=[
            ZoneReading(zone="view", people=people + 3, density_ppm2=1.0),
            ZoneReading(zone="gate", people=people, area_m2=2.0, density_ppm2=density),
            ZoneReading(zone="queue", people=3, area_m2=6.0, density_ppm2=0.5),
        ],
    )


def test_the_hottest_zone_is_counted_by_sam_spark_and_glimmer(tmp_path: Path) -> None:
    frames = tmp_path / "frames"
    frames.mkdir()
    cv2.imwrite(str(frames / "f.jpg"), np.full((300, 400, 3), 200, dtype=np.uint8))
    queue = CrowdZone(name="queue", polygon_px=[(0, 0), (50, 0), (50, 50), (0, 50)])
    zone = hottest(reading("f.jpg"), [queue, GATE])
    assert zone == GATE
    points = [[100.0, 100.0], [500.0, 500.0], [900.0, 900.0], [5.0, 5.0]]  # the last is outside
    spark = FakeLLM([{"points": points, "count": 4}])
    glimmer = FakeLLM([{"count": 13, "confidence": 0.7, "seen": "Packed."}])
    tallier = Tallier(
        spark=spark,
        files=Files(),
        glimmer=glimmer,
        glimmer_model="muse-glimmer",
        frames_dir=frames,
        out_dir=tmp_path / "heat",
    )
    counted = tallier.tally(reading("f.jpg"), GATE)
    assert (counted.sam, counted.spark, counted.spark_said, counted.glimmer) == (10, 3, 4, 13)
    assert (counted.low, counted.high) == (3, 13)
    assert counted.spread == round(10 / 13, 3)
    assert counted.disagree
    assert counted.crop is not None
    assert (tmp_path / "heat" / counted.crop).is_file()
    assert counted.crop_box == (88, 88, 224, 124)
    assert len(counted.points_px) == 3
    assert "un-greyed" in str(spark.calls[0]["messages"][0]["content"])  # type: ignore[index]
    cold = Tallier(
        spark=FakeLLM([{"points": [], "count": 0}]),
        files=None,
        glimmer=FakeLLM([]),
        glimmer_model="muse-glimmer",
        frames_dir=frames,
        out_dir=tmp_path / "heat",
        glimmer_warm=lambda: False,
    ).tally(reading("f.jpg"), GATE)
    assert (cold.spark, cold.glimmer, cold.disagree) == (None, None, False)
    assert cold.notes == ["this Spark client cannot be shown images", "Glimmer had no warm worker"]


def test_spark_hears_the_three_way_count_in_its_next_assessment(
    tmp_path: Path, crowd: CrowdMemory, grabber: FrameGrabber
) -> None:
    spark = FakeLLM([warning(alert=False), warning(alert=False)])
    tallier = Tallier(
        spark=FakeLLM([{"points": [[500.0, 500.0]], "count": 1}]),
        files=Files(),
        glimmer=FakeLLM([{"count": 2, "confidence": 0.5, "seen": "Two."}]),
        glimmer_model="muse-glimmer",
        frames_dir=grabber.out_dir,
        out_dir=tmp_path / "heat",
    )
    chosen = CrowdWarden(
        memory=crowd,
        frames=grabber,
        spark=spark,
        glimmer=FakeLLM([]),
        glimmer_model="muse-glimmer",
        chain=None,
        notifier=None,
        tts=None,
        clock=FakeClock(EPOCH),
        out_dir=tmp_path / "crowd",
        tallier=tallier,
    )
    whole = CrowdZone(name="all", polygon_px=[(0, 0), (400, 0), (400, 300), (0, 300)])
    watch = chosen.watch("gate", INSTRUCTION, zones=[whole])
    now = latest(crowd).model_copy(
        update={
            "zones": [*latest(crowd).zones, ZoneReading(zone="all", people=8, density_ppm2=0.1)]
        }
    )
    crowd._readings["gate"].append(now)  # the reading with the zone measured
    chosen.tick()
    wait_for(lambda: chosen.tally("gate") is not None)
    counted = chosen.tally("gate")
    assert counted is not None
    assert (counted.zone, counted.sam, counted.spark, counted.glimmer) == ("all", 8, 1, 2)
    assert json.loads(chosen.tallies_path.read_text())["zone"] == "all"
    running = chosen._running  # the assessment the tick started, if any
    if running is not None:
        wait_for(running.done)
    chosen.assess(watch, now)
    prompt = json.loads(str(spark.calls[-1]["messages"][1]["content"]))  # type: ignore[index]
    assert prompt["count_check"]["range"] == [1, 8]


def test_the_phase_meter_counts_stop_and_go_swings() -> None:
    speeds = [0.8, 0.3, 0.7, 0.2, 0.6, 0.1]
    history = [
        CrowdReading(
            camera_id="gate",
            at=EPOCH + timedelta(seconds=10 * k),
            concept="head",
            grid=1,
            people=10,
            calibration="owner",
            zones=[
                ZoneReading(zone="view", people=10, speed_mps=v, pressure=0.01 * k),
            ],
        )
        for k, v in enumerate(speeds)
    ]
    (view,) = forecast(history)
    assert view.phase is not None
    assert view.phase.swings == 4
    assert view.phase.speeds == speeds
    assert view.phase.swing_share == round(0.7 / (sum(speeds) / 6), 3)
    assert view.phase.pressure_peak == 0.05
    assert view.phase.speed_per_min is not None
    assert view.phase.speed_per_min < 0
    assert forecast(history[:2])[0].phase is None
