from __future__ import annotations

from typing import Literal

import pytest

from rulecam.contracts import Chunk, Detection, Segmenter
from rulecam.perception.errors import SamRuntimeError, SamRuntimeUnavailableError
from rulecam.perception.metrics import Backend, PerceptionMetrics
from rulecam.perception.router import (
    FallbackSegmenter,
    MeteredSegmenter,
    Purpose,
    RoutingPolicy,
    SegmenterRouter,
    hosted_requests,
)
from tests.fakes import FakeClock, FakeSegmenter, make_chunk


class FlakySegmenter(FakeSegmenter):
    """Raises `error` while it is set; records calls like `FakeSegmenter`."""

    def __init__(self, error: Exception | None = None) -> None:
        super().__init__()
        self.error = error

    def segment(
        self,
        chunk: Chunk,
        concept: str,
        *,
        box_prompts: dict[int, tuple[float, float, float, float]] | None = None,
        negative_points: list[tuple[int, float, float]] | None = None,
        direction: Literal["forward", "backward"] = "forward",
        include_confidence: bool = True,
    ) -> list[Detection]:
        detections = super().segment(
            chunk,
            concept,
            box_prompts=box_prompts,
            negative_points=negative_points,
            direction=direction,
            include_confidence=include_confidence,
        )
        if self.error is not None:
            raise self.error
        return detections


def _router(
    policy: RoutingPolicy | None = None,
) -> tuple[SegmenterRouter, FakeSegmenter, FlakySegmenter, FakeClock, list[str]]:
    hosted, self_hosted, clock = FakeSegmenter(), FlakySegmenter(), FakeClock()
    alerts: list[str] = []
    router = SegmenterRouter(
        hosted,
        self_hosted,
        clock=clock,
        policy=policy,
        on_fallback=lambda camera_id, error: alerts.append(f"{camera_id}: {error}"),
    )
    return router, hosted, self_hosted, clock, alerts


@pytest.mark.parametrize("purpose", [Purpose.PROBE, Purpose.RULE_CI])
def test_probes_and_rule_ci_use_hosted(purpose: Purpose) -> None:
    router, hosted, self_hosted, _, _ = _router()
    router.for_purpose(purpose).segment(make_chunk(), "thermometer")
    assert len(hosted.calls) == 1
    assert self_hosted.calls == []


def test_runtime_uses_self_hosted_by_default() -> None:
    router, hosted, self_hosted, _, _ = _router()
    router.for_purpose(Purpose.RUNTIME).segment(make_chunk(), "person", direction="backward")
    assert hosted.calls == []
    assert self_hosted.calls[0].direction == "backward"


def test_site_can_run_on_hosted() -> None:
    router, hosted, self_hosted, _, _ = _router(RoutingPolicy(runtime_backend=Backend.HOSTED))
    router.for_purpose(Purpose.RUNTIME).segment(make_chunk(), "person")
    assert len(hosted.calls) == 1
    assert self_hosted.calls == []
    assert router.policy.runtime_backend is Backend.HOSTED


def test_modal_error_falls_back_to_hosted_with_one_alert() -> None:
    router, hosted, self_hosted, clock, alerts = _router(RoutingPolicy(retry_after_s=60))
    runtime = router.for_purpose(Purpose.RUNTIME)
    self_hosted.error = SamRuntimeUnavailableError("Modal call to rulecam-sam failed")

    runtime.segment(make_chunk(index=0), "person", box_prompts={3: (0.0, 0.0, 9.0, 9.0)})
    clock.advance(3)
    runtime.segment(make_chunk(index=1), "person")

    assert [call.chunk_id for call in hosted.calls] == ["cam1_000000", "cam1_000001"]
    assert hosted.calls[0].box_prompts == {3: (0.0, 0.0, 9.0, 9.0)}
    assert len(self_hosted.calls) == 1  # inside the retry window hosted is used directly
    assert alerts == ["cam1: Modal call to rulecam-sam failed"]
    assert isinstance(runtime, FallbackSegmenter)
    assert runtime.degraded


def test_self_hosted_is_retried_after_the_window_and_recovers() -> None:
    router, hosted, self_hosted, clock, alerts = _router(RoutingPolicy(retry_after_s=60))
    runtime = router.for_purpose(Purpose.RUNTIME)
    self_hosted.error = SamRuntimeUnavailableError("down")
    runtime.segment(make_chunk(index=0), "person")

    clock.advance(61)
    runtime.segment(make_chunk(index=1), "person")  # retried, still down: no second alert
    clock.advance(61)
    self_hosted.error = None
    runtime.segment(make_chunk(index=2), "person")

    assert len(self_hosted.calls) == 3
    assert len(hosted.calls) == 2
    assert len(alerts) == 1
    assert isinstance(runtime, FallbackSegmenter)
    assert not runtime.degraded


def test_invalid_prompts_never_fall_back() -> None:
    router, hosted, self_hosted, _, alerts = _router()
    self_hosted.error = ValueError("text and point prompts are mutually exclusive")
    with pytest.raises(ValueError, match="mutually exclusive"):
        router.for_purpose(Purpose.RUNTIME).segment(make_chunk(), "harness")
    assert hosted.calls == []
    assert alerts == []


def test_hosted_runtime_calls_are_metered() -> None:
    metrics = PerceptionMetrics()
    hosted = MeteredSegmenter(FakeSegmenter(), metrics)
    hosted.segment(make_chunk(n_frames=30), "person")
    hosted.segment(
        make_chunk(n_frames=30), "person", box_prompts=dict.fromkeys(range(20), (0, 0, 1, 1))
    )

    total = metrics.report().total
    assert total.sam_calls_hosted == 3
    assert total.sam_calls_self_hosted == 0


def test_fallback_without_alert_hook_still_serves() -> None:
    hosted, clock = FakeSegmenter(), FakeClock()
    runtime = FallbackSegmenter(FlakySegmenter(SamRuntimeError("boom")), hosted, clock=clock)
    assert runtime.segment(make_chunk(), "person") == []
    assert len(hosted.calls) == 1


@pytest.mark.parametrize(("n_boxes", "expected"), [(0, 1), (1, 1), (16, 1), (17, 2), (33, 3)])
def test_hosted_requests_per_call(n_boxes: int, expected: int) -> None:
    boxes = dict.fromkeys(range(n_boxes), (0.0, 0.0, 1.0, 1.0)) or None
    assert hosted_requests(boxes) == expected


def test_router_parts_conform_to_segmenter() -> None:
    router, _, _, _, _ = _router()
    runtime: Segmenter = router.for_purpose(Purpose.RUNTIME)
    metered: Segmenter = MeteredSegmenter(FakeSegmenter(), None)
    assert runtime.segment(make_chunk(), "person") == []
    assert metered.segment(make_chunk(), "person") == []
