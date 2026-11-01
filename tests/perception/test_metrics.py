from __future__ import annotations

import json
import math

import pytest

from rulecam.perception.metrics import (
    Backend,
    PerceptionMetrics,
    hosted_billed_frames,
    hosted_usd,
)


@pytest.mark.parametrize(("frames", "billed"), [(1, 50), (30, 50), (50, 50), (51, 51), (900, 900)])
def test_hosted_bills_at_least_50_frames(frames: int, billed: int) -> None:
    assert hosted_billed_frames(frames) == billed


def test_a_30_frame_request_costs_one_cent() -> None:
    assert hosted_usd(30) == pytest.approx(0.01)


def test_continuous_hosted_costs_about_12_dollars_per_concept_camera_hour() -> None:
    metrics = PerceptionMetrics()
    for _ in range(1200):  # one hour of 3 s chunks, one concept, all hosted
        metrics.record_chunk("cam1", 3.0)
        metrics.record_sam_call("cam1", backend=Backend.HOSTED, frames=30)
    camera = metrics.report().cameras[0]
    assert camera.camera_hours == pytest.approx(1.0)
    assert camera.sam_calls_per_camera_hour == pytest.approx(1200)
    assert camera.hosted_equivalent_usd_per_camera_hour == pytest.approx(12.0)
    assert camera.actual_usd_per_camera_hour == pytest.approx(12.0)


def test_self_hosted_cost_comes_from_gpu_seconds() -> None:
    metrics = PerceptionMetrics(gpu_usd_per_hour=1.80)
    metrics.record_chunk("cam1", 1800.0)
    metrics.record_sam_call("cam1", backend=Backend.SELF_HOSTED, frames=30, gpu_seconds=600.0)
    metrics.record_sam_call("cam1", backend=Backend.SELF_HOSTED, frames=30, gpu_seconds=600.0)

    camera = metrics.report().cameras[0]
    assert camera.gpu_seconds == 1200.0
    assert camera.sam_calls_self_hosted == 2
    assert camera.actual_usd_per_camera_hour == pytest.approx(1.80 * (1200 / 3600) / 0.5)
    assert camera.hosted_equivalent_usd_per_camera_hour == pytest.approx(0.02 / 0.5)


def test_gated_fraction_and_totals_across_cameras() -> None:
    metrics = PerceptionMetrics()
    metrics.record_chunk("cam2", 3.0)
    metrics.record_gate("cam2", considered=4, gated=3)
    metrics.record_chunk("cam1", 3.0)
    metrics.record_gate("cam1", considered=4, gated=1)
    metrics.record_sam_call("cam1", backend=Backend.HOSTED, frames=30)

    report = metrics.report()
    assert [c.camera_id for c in report.cameras] == ["cam1", "cam2"]
    assert report.cameras[0].gated_fraction == pytest.approx(0.25)
    assert report.total.camera_id == "all"
    assert report.total.gated_fraction == pytest.approx(0.5)
    assert report.total.camera_hours == pytest.approx(6 / 3600)
    assert report.total.sam_calls == 1


def test_empty_report_has_no_rates() -> None:
    report = PerceptionMetrics().report()
    assert report.cameras == ()
    assert report.total.sam_calls_per_camera_hour is None
    assert report.total.gated_fraction is None


def test_report_is_json_with_numbers() -> None:
    metrics = PerceptionMetrics()
    metrics.record_chunk("cam1", 3.0)
    metrics.record_gate("cam1", considered=2, gated=1)
    metrics.record_sam_call("cam1", backend=Backend.SELF_HOSTED, frames=30, gpu_seconds=0.4)

    body = json.loads(metrics.report().model_dump_json())
    camera = body["cameras"][0]
    numeric = [value for key, value in camera.items() if key != "camera_id"]
    assert all(isinstance(value, int | float) and math.isfinite(value) for value in numeric)


def test_image_calls_price_hosted_images_and_self_hosted_gpu_time() -> None:
    metrics = PerceptionMetrics(gpu_usd_per_hour=3.6)
    metrics.record_chunk("cam1", 3600.0)
    metrics.record_image_call("cam1", backend=Backend.HOSTED, images=1)
    metrics.record_image_call("cam1", backend=Backend.SELF_HOSTED, images=12, gpu_seconds=2.0)
    summary = metrics.report().cameras[0]
    assert (summary.sam_calls_hosted, summary.sam_calls_self_hosted) == (1, 1)
    assert summary.hosted_equivalent_usd_per_camera_hour == pytest.approx(13 * 0.0025)
    assert summary.actual_usd_per_camera_hour == pytest.approx(0.0025 + 2.0 * 0.001)
    assert summary.gpu_seconds == 2.0
