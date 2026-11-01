"""Observations from the chain, run scoring, latency, cost, and the replay of saved runs."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from tests.fakes import EPOCH
from tests.fakes.eval_api import (
    candidate_record,
    decision_record,
    run_record,
    slots,
    verdict_record,
)

from rulecam.eval.errors import EvalError
from rulecam.eval.observe import observe
from rulecam.eval.record import BusFrame, MetricsSample, Timeline, load_runs, save_run
from rulecam.eval.schema import EvalSet, load_eval_set
from rulecam.eval.score import ScoreConfig, load_report, save_report, score_runs

T = EPOCH


def at(seconds: float) -> Any:
    return T + timedelta(seconds=seconds)


def evalset(tmp_path: Path, rules: list[dict[str, Any]], *, name: str = "yard") -> EvalSet:
    folder = tmp_path / "set" / name
    folder.mkdir(parents=True)
    data = {
        "name": name,
        "camera_id": name,
        "source": {"path": "clip.mp4"},
        "duration_s": 30,
        "rules": rules,
    }
    (folder / "clip.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    return load_eval_set(tmp_path / "set")


LABELLED = {
    "id": "forklift_2m",
    "request": "Keep people 2 m from a moving forklift.",
    "status": "labelled",
    "events": [{"start_s": 6, "end_s": 9}, {"start_s": 20, "end_s": 23}],
}


def test_timeline_maps_camera_time_both_ways() -> None:
    timeline = Timeline(slots(3))
    assert timeline.to_clip_s(at(4.5)) == 4.5
    assert timeline.to_clip_s(at(-1)) is None
    assert timeline.to_clip_s(at(9.5)) is None
    assert timeline.to_camera(7.0) == at(7.0)
    assert timeline.to_camera(99.0) is None
    assert not Timeline(())


def test_clip_span_clamps_an_overlapping_episode_into_the_pass() -> None:
    timeline = Timeline(slots(3))  # three 3 s chunks: camera seconds 0-9 are clip seconds 0-9
    assert timeline.clip_span(at(4), at(5)) == (4.0, 5.0)
    assert timeline.clip_span(at(-0.002), at(1)) == (0.0, 1.0)  # stamped 2 ms early
    assert timeline.clip_span(at(8), at(12)) == (8.0, 9.0)
    assert timeline.clip_span(at(-5), at(-1)) is None
    assert timeline.clip_span(at(10), at(11)) is None
    assert timeline.clip_span(at(5), at(4)) is None
    assert Timeline(()).clip_span(at(0), at(1)) is None


def test_clip_span_maps_a_gap_between_chunks_to_the_earlier_chunk() -> None:
    first, second = slots(2)
    later = second.model_copy(update={"cam_start": second.cam_start + timedelta(seconds=2)})
    timeline = Timeline((first, later))  # chunk 1 ends at 3 s, chunk 2 starts at 5 s
    assert timeline.clip_span(at(4), at(6)) == (3.0, 4.0)


def test_a_candidate_stamped_just_before_the_pass_is_still_scored() -> None:
    """Regression: the 1 Nov live run's driver candidate began 2 ms before chunk 0 and was lost."""
    chain = [
        candidate_record(1, "driver", at(-0.002), at(1.1)),
        verdict_record(2, "driver", "no_violation", at(30)),
        decision_record(3, "driver", alert=True, at=at(30)),
    ]
    (obs,) = observe(run_record(chain))
    assert obs.start_s == 0.0
    assert obs.end_s == pytest.approx(1.1)
    assert obs.alert  # HIGH acted first: a false alert on a negative clip


def test_observe_folds_verdicts_and_filters_camera_plan_and_pass() -> None:
    chain = [
        candidate_record(1, "e1", at(6), at(7)),
        verdict_record(2, "e1", "violation", at(12)),
        candidate_record(3, "e2", at(20), at(21)),
        verdict_record(4, "e2", "unsure", at(25)),
        verdict_record(5, "e2", "violation", at(27), tier="spark_open"),
        candidate_record(6, "e3", at(22), at(22)),
        verdict_record(7, "e3", "violation", at(23)),
        decision_record(8, "e3", alert=False, at=at(24)),
        candidate_record(9, "other-cam", at(6), at(7), camera_id="phone-9"),
        candidate_record(10, "old-plan", at(6), at(7), plan_hash="q" * 64),
        candidate_record(11, "before-pass", at(-10), at(-9)),
        {"seq": 12, "kind": "candidate", "ts": "x", "payload": {"candidate": {"bad": 1}}},
    ]
    observed = {o.event_id: o for o in observe(run_record(chain))}
    assert set(observed) == {"e1", "e2", "e3"}
    assert observed["e1"].alert
    assert observed["e1"].alert_at == at(12)
    assert observed["e2"].tier1 == "unsure"
    assert observed["e2"].tier2 == "violation"
    assert observed["e2"].alert
    assert not observed["e3"].alert  # the decision overrides the Tier 1 violation


def test_a_bus_alert_makes_an_alert_and_is_earlier() -> None:
    chain = [candidate_record(1, "e1", at(6), at(7)), verdict_record(2, "e1", "unsure", at(15))]
    frame = BusFrame(at=at(11), topic="alert.new", payload={"event_id": "e1"})
    (obs,) = observe(run_record(chain, bus=(frame,)))
    assert obs.alert
    assert obs.alert_at == at(11)


def test_score_counts_tp_fp_fn_latency_and_agreement(tmp_path: Path) -> None:
    chain = [
        candidate_record(1, "e1", at(6.2), at(8.5)),
        verdict_record(2, "e1", "violation", at(14)),  # latency 14 - 6 = 8 s
        candidate_record(3, "e1b", at(9.0), at(9.0)),  # merges into the same episode
        verdict_record(4, "e1b", "violation", at(16)),
        candidate_record(5, "fp", at(14), at(14.5)),
        verdict_record(6, "fp", "violation", at(18)),
        candidate_record(7, "quiet", at(20.5), at(21)),
        verdict_record(8, "quiet", "unsure", at(26)),
    ]
    metrics = (
        MetricsSample(at=at(0), lag_s=2, billed_frames=1000),
        MetricsSample(at=at(15), lag_s=4, billed_frames=1500, keyframe_mode=True),
        MetricsSample(at=at(30), lag_s=8, billed_frames=2500),
    )
    report = score_runs(
        [run_record(chain, metrics=metrics)], evalset(tmp_path, [LABELLED]), generated_at=T
    )
    (run,) = report.runs
    assert (run.tp, run.fp, run.fn) == (1, 1, 1)
    assert run.alerts == 2
    assert run.candidates == 4
    assert run.latencies_s == (8.0,)
    assert run.decisive == 3
    assert run.agree == 2  # e1 and e1b match the event; "fp" says violation outside any event
    assert run.keyframe_mode
    assert run.lags_s == (2, 4, 8)
    assert run.billed_frames == 1500
    assert run.usd_per_camera_hour == pytest.approx(1500 * 0.0002 / (30 / 3600))
    (rule,) = report.rules
    assert rule.precision == 0.5
    assert rule.recall == 0.5
    assert rule.latency_p95_s == 8.0


def test_negative_clips_count_false_alerts(tmp_path: Path) -> None:
    negative = {"id": "forklift_2m", "request": "x", "status": "negative"}
    chain = [
        candidate_record(1, "a", at(3), at(4)),
        verdict_record(2, "a", "violation", at(9)),
        candidate_record(3, "b", at(15), at(16)),
        verdict_record(4, "b", "unsure", at(20)),
    ]
    report = score_runs([run_record(chain)], evalset(tmp_path, [negative]), generated_at=T)
    (rule,) = report.rules
    assert rule.false_alerts_on_negatives == 1
    assert rule.negative_clips == 1
    assert rule.recall is None
    assert rule.precision == 0.0
    assert report.runs[0].agree == 0  # the decisive "violation" was wrong
    assert report.runs[0].billed_frames is None  # no metrics samples: cost unknown, not $0
    assert report.runs[0].usd_per_camera_hour is None


def test_refusals_errors_and_unlabelled_runs(tmp_path: Path) -> None:
    unlabelled = {"id": "wash", "request": "Wash 20 s", "status": "unlabelled"}
    rules: list[dict[str, Any]] = [LABELLED | {"expect": "refusal"}, unlabelled]
    chain = [candidate_record(1, "w", at(3), at(4)), verdict_record(2, "w", "violation", at(5))]
    runs = [
        run_record([], refused="no rule passes"),
        run_record(chain, rule="wash"),
        run_record([], rule="wash", error="planner timed out"),
    ]
    report = score_runs(runs, evalset(tmp_path, rules), generated_at=T)
    refused, scored, errored = report.runs
    assert refused.outcome == "refused"
    assert refused.refused == "no rule passes"
    assert scored.outcome == "activated"
    assert scored.agree == 0
    assert scored.decisive == 1
    assert errored.outcome == "error"
    assert report.rules == ()  # nothing scored: a refusal and an unlabelled rule


def test_realtime_only_latency_and_reports_round_trip(tmp_path: Path) -> None:
    chain = [candidate_record(1, "e1", at(6), at(8)), verdict_record(2, "e1", "violation", at(9))]
    report = score_runs(
        [run_record(chain, speed=2.0)],
        evalset(tmp_path, [LABELLED]),
        config=ScoreConfig(gpu_usd_per_hour=1.0),
        generated_at=T,
    )
    assert report.runs[0].latencies_s == (3.0,)
    assert report.rules[0].latency_p95_s is None  # 2x pace: not a real-time number
    path = save_report(report, tmp_path / "out" / "r.json")
    assert load_report(path) == report
    (tmp_path / "bad.json").write_text("{}", encoding="utf-8")
    with pytest.raises(EvalError):
        load_report(tmp_path / "bad.json")


def test_runs_round_trip_through_files(tmp_path: Path) -> None:
    record = run_record([candidate_record(1, "e1", at(6), at(8))])
    save_run(record, tmp_path)
    assert load_runs(tmp_path) == [record]
    with pytest.raises(EvalError, match="no"):
        load_runs(tmp_path / "empty")
    (tmp_path / "x.run.json").write_text("not json", encoding="utf-8")
    with pytest.raises(EvalError):
        load_runs(tmp_path)


BASELINE = Path(__file__).resolve().parents[2] / "eval" / "baseline" / "2026-10-31-live"
SEED = Path(__file__).resolve().parents[2] / "eval" / "clips"


def test_replay_of_the_committed_baseline() -> None:
    """CI replay: the 31 Oct live run re-scores offline to the same numbers."""
    report = score_runs(load_runs(BASELINE), load_eval_set(SEED), generated_at=T)
    (run,) = report.runs
    assert run.clip == "warehouse_45848"
    assert run.status == "negative"
    assert run.candidates == 10
    assert run.alerts == 0
    assert run.decisive == 0
    assert report.rules[0].false_alerts_on_negatives == 0
