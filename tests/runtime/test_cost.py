"""The per-camera dollar budget, its alarm and degrade path, and cost per camera-hour (WS3)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from rulecam.config import Settings
from rulecam.perception.metrics import Backend, HostedUsage, PerceptionMetrics
from rulecam.runtime.cost import (
    CostMeter,
    CostSettings,
    CostWatch,
    active_cameras,
    hosted_usd,
)
from rulecam.runtime.idle_stop import IdleGpuStop
from rulecam.runtime.sampling import CameraSampler, SamplingPolicy
from tests.fakes import EPOCH, FakeClock, make_chunk
from tests.runtime.fakes import sink_plan


class Bill:
    """A mutable hosted bill per camera (stands in for `PerceptionMetrics.hosted_usage`)."""

    def __init__(self) -> None:
        self.usage: dict[str, HostedUsage] = {}

    def add(self, camera: str, *, frames: int = 0, images: int = 0, seconds: float = 0.0) -> None:
        old = self.usage.get(camera, HostedUsage())
        self.usage[camera] = HostedUsage(
            billed_frames=old.billed_frames + frames,
            images=old.images + images,
            seconds=old.seconds + seconds,
        )

    def __call__(self) -> dict[str, HostedUsage]:
        return dict(self.usage)


def meter(bill: Bill, budget: float = 12.0, gpu: float = 0.0) -> CostMeter:
    return CostMeter(
        CostSettings(budget_usd_per_camera_hour=budget, gpu_usd_per_hour=gpu), usage=bill
    )


def test_prices_come_from_sam_cost() -> None:
    assert hosted_usd(HostedUsage(billed_frames=1000, images=1000)) == pytest.approx(0.20 + 2.50)


def test_settings_defaults_replace_the_frame_budget() -> None:
    settings = Settings(_env_file=None)
    cost = CostSettings.from_settings(settings)
    assert cost.budget_usd_per_camera_hour == 12.0  # the former 1,000 hosted frames/min
    assert cost.gpu_usd_per_hour == 0.0
    assert settings.rulecam_sam_budget_frames_per_min == 0


def test_active_cameras_need_a_rule_of_the_plan_and_a_recent_chunk() -> None:
    plan = sink_plan().model_copy(update={"rule_cameras": {"in-sink": ("cam1", "cam2")}})
    seen = {"cam1": EPOCH, "cam2": EPOCH - timedelta(minutes=5), "cam3": EPOCH}
    assert active_cameras(plan, seen, EPOCH + timedelta(seconds=10)) == {"cam1"}
    assert active_cameras(None, seen, EPOCH) == frozenset()


def test_hosted_spend_over_the_budget_raises_the_alarm_and_degrades() -> None:
    bill = Bill()
    cost = meter(bill, budget=12.0)
    cost.tick(EPOCH, {"cam1"})
    assert not cost.report().alarm
    # 1,000 billed frames in a minute = $0.20/min = $12/h: exactly the budget.
    bill.add("cam1", frames=1000, seconds=30)
    cost.tick(EPOCH + timedelta(seconds=30), {"cam1"})
    report = cost.report()
    assert report.alarm
    assert report.over_budget == ("cam1",)
    (cam,) = report.cameras
    assert cam.hosted_usd_per_hour == pytest.approx(12.0)
    assert cam.degraded
    assert cost.over_budget("cam1")
    assert not cost.over_budget("cam2")
    # A minute later nothing more was billed: it recovers by itself.
    cost.tick(EPOCH + timedelta(seconds=91), {"cam1"})
    assert not cost.report().alarm
    assert not cost.over_budget("cam1")


def test_under_the_budget_and_budget_off() -> None:
    bill = Bill()
    cost = meter(bill, budget=12.0)
    cost.tick(EPOCH, {"cam1"})
    bill.add("cam1", frames=500)  # $6/h
    cost.tick(EPOCH + timedelta(seconds=10), {"cam1"})
    assert not cost.report().alarm
    off = meter(bill, budget=0.0)
    off.tick(EPOCH, {"cam1"})
    bill.add("cam1", frames=100_000)
    off.tick(EPOCH + timedelta(seconds=1), {"cam1"})
    assert not off.report().alarm


def test_images_count_in_dollars() -> None:
    bill = Bill()
    cost = meter(bill, budget=1.0)
    cost.tick(EPOCH, {"cam1"})
    bill.add("cam1", images=10)  # $0.025 in a minute = $1.50/h
    cost.tick(EPOCH + timedelta(seconds=5), {"cam1"})
    assert cost.over_budget("cam1")


def test_gpu_share_alarms_but_never_degrades() -> None:
    """Segmenting less cannot lower the GPU pods' hourly price, so it only raises the alarm."""
    bill = Bill()
    cost = meter(bill, budget=0.5, gpu=1.2)
    cost.tick(EPOCH, {"cam1", "cam2"})  # $0.60/h each
    report = cost.report()
    assert report.over_budget == ("cam1", "cam2")
    assert not any(c.degraded for c in report.cameras)
    assert not cost.over_budget("cam1")
    cost.tick(EPOCH + timedelta(seconds=1), {"cam1", "cam2", "cam3"})  # $0.40/h each
    assert not cost.report().alarm


def test_cost_per_camera_hour_includes_gpu_and_idle_time() -> None:
    bill = Bill()
    cost = meter(bill, budget=0.0, gpu=3.6)  # $0.001 per second
    cost.tick(EPOCH, {"cam1"})
    for second in range(1, 11):
        cost.tick(EPOCH + timedelta(seconds=second), {"cam1"})  # 10 s active
    for second in range(11, 16):
        cost.tick(EPOCH + timedelta(seconds=second), set())  # 5 s idle
    bill.add("cam1", frames=500, seconds=36)  # $0.10 hosted, 0.01 camera-hours
    cost.tick(EPOCH + timedelta(seconds=16), set())
    report = cost.report()
    assert report.gpu_idle_usd == pytest.approx(0.006)
    (cam,) = report.cameras
    assert cam.usd == pytest.approx(0.10 + 0.010)
    assert cam.camera_hours == pytest.approx(0.01)
    assert cam.usd_per_camera_hour == pytest.approx(11.0)
    assert report.usd == pytest.approx(0.116)
    assert report.usd_per_camera_hour == pytest.approx(11.6)
    # A long gap (the process slept) accrues nothing.
    cost.tick(EPOCH + timedelta(hours=1), {"cam1"})
    assert cost.report().usd == pytest.approx(0.116)


def test_empty_report() -> None:
    report = meter(Bill()).report()
    assert (report.alarm, report.usd, report.usd_per_camera_hour) == (False, 0.0, None)


def test_the_sampler_degrades_a_camera_over_the_dollar_budget() -> None:
    over = {"cam1"}
    sampler = CameraSampler(SamplingPolicy(budget_stride=3), over_budget=lambda c: c in over)
    rules = sink_plan("medium").rules
    seen = [sampler.admit(make_chunk(index=i), rules, moving=True) for i in range(6)]
    assert seen == ["run", "budget", "budget", "run", "budget", "budget"]
    assert sampler.budget_exceeded == 1
    assert sampler.degraded() == ["cam1"]
    over.clear()
    assert sampler.admit(make_chunk(index=6), rules, moving=True) == "run"
    assert sampler.degraded() == []
    # HIGH rules are never thinned while something moves.
    over.add("cam1")
    high = sink_plan("high").rules
    assert {sampler.admit(make_chunk(index=7 + i), high, moving=True) for i in range(3)} == {"run"}


def test_perception_metrics_hosted_usage() -> None:
    metrics = PerceptionMetrics(gpu_usd_per_hour=3.6)
    metrics.record_chunk("cam1", 3.0)
    metrics.record_sam_call("cam1", backend=Backend.HOSTED, frames=30)
    metrics.record_sam_call("cam1", backend=Backend.SELF_HOSTED, frames=30, gpu_seconds=1.0)
    metrics.record_image_call("cam1", backend=Backend.HOSTED)
    metrics.record_image_call("cam1", backend=Backend.SELF_HOSTED, gpu_seconds=2.0)
    assert metrics.hosted_usage() == {"cam1": HostedUsage(billed_frames=50, images=1, seconds=3.0)}
    total = metrics.report().total
    assert (total.sam_calls_hosted, total.sam_calls_self_hosted) == (2, 2)
    assert total.gpu_seconds == pytest.approx(3.0)


class Stopper:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def stop(self, pods: object) -> list[str]:
        self.calls.append(tuple(pods))  # type: ignore[arg-type]
        return ["pod-1"]


def test_cost_watch_ticks_the_meter_and_the_idle_stop() -> None:
    clock = FakeClock()
    bill = Bill()
    plan = sink_plan()
    seen = {"cam1": clock.now()}
    stopper = Stopper()
    idle = IdleGpuStop(stopper, after_s=5, pods=("rulecam-sam",))
    current = {"plan": plan}
    watch = CostWatch(
        meter(bill, budget=1.0),
        clock=clock,
        plan=lambda: current["plan"],
        last_seen=lambda: seen,
        idle_stop=idle,
    )
    watch.tick()
    bill.add("cam1", frames=1000)
    clock.advance(1)
    watch.tick()
    report = watch.report()
    assert report.alarm
    assert report.idle_stop is not None
    assert report.idle_stop.enabled
    assert report.idle_stop.idle_s == 0
    current["plan"] = None  # type: ignore[assignment]
    for _ in range(6):
        clock.advance(1)
        watch.tick()
    assert stopper.calls == [("rulecam-sam",)]
    assert watch.report().idle_stop.stopped  # type: ignore[union-attr]
    without = CostWatch(meter(bill), clock=clock, plan=lambda: None, last_seen=dict)
    without.tick()
    assert without.report().idle_stop is None
