"""The eval runner against a fake API in simulated time (no network, no sleeping)."""

from __future__ import annotations

import itertools
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from tests.fakes import EPOCH
from tests.fakes.eval_api import (
    FakeApi,
    SimClock,
    candidate_record,
    fake_splitter,
    iso,
    verdict_record,
)

from rulecam.contracts import Track, TrackPoint
from rulecam.eval.errors import EvalRunError
from rulecam.eval.record import BusFrame, load_runs
from rulecam.eval.runner import (
    EvalRunner,
    RunConfig,
    _metrics_sample,
    relevant_records,
    run_eval_set,
    split_clip,
)
from rulecam.eval.schema import ClipEntry, EvalSet
from rulecam.eval.score import score_runs

T = EPOCH


def at(seconds: float) -> datetime:
    return T + timedelta(seconds=seconds)


CLIP = ClipEntry.model_validate(
    {
        "name": "yard",
        "camera_id": "yard",
        "source": {"path": "clip.mp4"},
        "duration_s": 12,
        "rules": [
            {
                "id": "forklift_2m",
                "request": "Keep people 2 m from a moving forklift.",
                "status": "labelled",
                "events": [{"start_s": 3.5, "end_s": 6}],
            },
            {"id": "wash", "request": "Wash 20 s", "status": "unlabelled"},
        ],
    }
)


class FakeBus:
    def __init__(self) -> None:
        self.started = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> list[BusFrame]:
        return [BusFrame(at=at(27), topic="alert.new", payload={"event_id": "e1"})]


class FakeTracks:
    def window(self, camera_id: str, t0: datetime, t1: datetime) -> list[Track]:
        points = tuple(
            TrackPoint(ts=at(s), frame_idx=i, box_xyxy=(0, 0, 10, 10), foot_px=(5, 10))
            for i, s in enumerate((17.0, 19.0, 22.0))
        )
        return [Track(track_id="t1", camera_id=camera_id, concept="person", points=points)]


def scripted_chain(api: FakeApi) -> list[dict[str, object]]:
    # The scored pass starts at camera time T+18 (clip second 0), so clip 4-5 s is T+22-23.
    return [
        candidate_record(1, "e1", at(22), at(23), written=at(24)),
        verdict_record(2, "e1", "violation", at(28)),
    ]


def runner(api: FakeApi, sim: SimClock, **config: float) -> EvalRunner:
    return EvalRunner(
        api,
        config=RunConfig(**({"survey_s": 6.0, "plan_poll_s": 10.0} | config)),
        clock=sim.clock(),
        splitter=fake_splitter(4),
        bus=FakeBus,
        tracks=FakeTracks(),
    )


def test_a_full_run_records_the_scored_pass(tmp_path: Path) -> None:
    sim = SimClock()
    api = FakeApi(clock=sim, chain_for=scripted_chain)
    record = runner(api, sim).run(CLIP, CLIP.rules[0], tmp_path / "clip.mp4", tmp_path)
    # Uploads 3 s apart: survey at 3, 6 (request), 9, 12, 15, 18 (plan ready), then 21..30.
    times = [(t - T).total_seconds() for _, t in api.uploads]
    assert times == [3, 6, 9, 12, 15, 18, 21, 24, 27, 30]
    assert api.requests == ["Keep people 2 m from a moving forklift."]
    assert [s.cam_start for s in record.chunks] == [at(18), at(21), at(24), at(27)]
    assert [s.clip_start_s for s in record.chunks] == [0, 3, 6, 9]
    assert record.plan_version_hash == "p" * 64
    assert record.activated == ("keep-clear",)
    assert len(record.rule_hashes) == 1
    assert record.chunk_width == 640
    assert len(record.chain) == 2
    assert len(record.bus) == 1
    assert record.metrics[0].in_flight == 2
    assert record.metrics[-1].in_flight == 0
    assert [p.clip_s for p in record.predicted] == [1.0, 4.0]  # T+17 is before the pass
    report = score_runs(
        [record],
        EvalSet(root=tmp_path, clips=(CLIP,)),
        generated_at=T,
    )
    (scored,) = report.runs
    assert (scored.tp, scored.fp, scored.fn) == (1, 0, 0)
    assert scored.latencies_s == (5.5,)  # the bus alert at T+27, event start at T+21.5
    assert scored.billed_frames == 4 * 50


def test_a_refused_plan_is_recorded(tmp_path: Path) -> None:
    sim = SimClock()
    api = FakeApi(clock=sim, refuse="no rule passes the activation gate")
    record = runner(api, sim).run(CLIP, CLIP.rules[0], tmp_path / "clip.mp4", tmp_path)
    assert record.refused == "no rule passes the activation gate"
    assert record.chunks == ()
    assert record.chain == ()


def test_a_planner_timeout_is_an_error(tmp_path: Path) -> None:
    sim = SimClock()
    api = FakeApi(clock=sim, plan_ready_after_polls=10**6)
    record = runner(api, sim, plan_timeout_s=30.0).run(
        CLIP, CLIP.rules[0], tmp_path / "clip.mp4", tmp_path
    )
    assert record.error is not None
    assert "not ready" in record.error
    assert api.polls >= 3


def test_accelerated_pace_respects_the_pairing_rate_limit(tmp_path: Path) -> None:
    sim = SimClock()
    api = FakeApi(clock=sim)
    record = runner(api, sim, speed=2.5, survey_s=0.0).run(
        CLIP, CLIP.rules[0], tmp_path / "clip.mp4", tmp_path
    )
    gaps = [
        (b - a).total_seconds()
        for (_, a), (_, b) in zip(api.uploads, api.uploads[1:], strict=False)
    ]
    assert min(gaps) >= 1.2 - 1e-9  # 3 s / 2.5
    assert record.speed == 2.5
    # Uploads outpace the camera clock: chunks are placed back to back, never overlapping.
    starts = [s.cam_start for s in record.chunks]
    assert all(b - a == timedelta(seconds=3) for a, b in itertools.pairwise(starts))
    with pytest.raises(ValueError, match="speed"):
        RunConfig(speed=5.0)


def test_drain_gives_up_after_its_timeout(tmp_path: Path) -> None:
    sim = SimClock()
    api = FakeApi(clock=sim, in_flight=[3])
    record = runner(api, sim, drain_timeout_s=5.0).run(
        CLIP, CLIP.rules[0], tmp_path / "clip.mp4", tmp_path
    )
    assert record.metrics[-1].in_flight == 3


def test_run_eval_set_skips_unlabelled_rules(tmp_path: Path) -> None:
    sim = SimClock()
    api = FakeApi(clock=sim)
    paths = run_eval_set(
        runner(api, sim),
        [(CLIP, tmp_path / "clip.mp4")],
        work_dir=tmp_path,
        runs_dir=tmp_path / "runs",
    )
    assert [p.name for p in paths] == ["yard__forklift_2m.run.json"]
    assert len(load_runs(tmp_path / "runs")) == 1
    both = run_eval_set(
        runner(api, sim),
        [(CLIP, tmp_path / "clip.mp4")],
        work_dir=tmp_path,
        runs_dir=tmp_path / "all",
        include_unlabelled=True,
    )
    assert len(both) == 2


def test_relevant_records_keeps_this_camera_since_the_start() -> None:
    records = [
        candidate_record(1, "old", at(0), at(1), written=at(-5)),
        candidate_record(2, "mine", at(2), at(3)),
        candidate_record(3, "theirs", at(2), at(3), camera_id="phone-9"),
        verdict_record(4, "mine", "unsure", at(5)),
        verdict_record(5, "theirs", "unsure", at(5)),
        verdict_record(6, "old", "unsure", at(6)),
        {"seq": 7, "kind": "plan", "ts": iso(at(7)), "payload": "not a mapping"},
        {"seq": 8, "kind": "plan", "ts": None, "payload": {}},
    ]
    kept = relevant_records(records, "phone-1", at(0))
    assert [r["seq"] for r in kept] == [2, 4]


def test_metrics_sample_tolerates_odd_payloads() -> None:
    assert _metrics_sample({"lag_s": "slow"}, T).lag_s == 0.0
    assert _metrics_sample({"lag_s": 4.5, "other": 1}, T).lag_s == 4.5


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_split_clip_cuts_three_second_chunks(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg is not None
    subprocess.run(  # noqa: S603
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x120:rate=10",
            "-t",
            "7",
            str(clip),
        ],
        check=True,
    )
    chunks = split_clip(clip, tmp_path / "chunks")
    assert len(chunks) == 3
    assert chunks[1].clip_start_s == pytest.approx(3.0)
    assert sum(c.duration_s for c in chunks) == pytest.approx(7.0, abs=0.2)
    with pytest.raises(EvalRunError):
        split_clip(tmp_path / "missing.mp4", tmp_path / "none")
