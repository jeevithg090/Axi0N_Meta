"""The image-mode fallback chain: self-hosted keyframes, then hosted, counted (WS2b, WS2c)."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from rulecam.contracts import Chunk
from rulecam.perception.errors import SamResponseError, SamRuntimeError, SamRuntimeUnavailableError
from rulecam.perception.keyframes import Keyframe, KeyframeBatch
from rulecam.perception.metrics import Backend
from rulecam.perception.router import KeyframeFallback, RoutingPolicy, keyframe_chain
from tests.fakes import FakeClock, make_chunk


class Scripted:
    """A `KeyframeSegmenter` that raises the queued errors first, then answers."""

    def __init__(self, backend: Backend, *errors: Exception, billed: bool = False) -> None:
        self.backend = backend
        self.errors = list(errors)
        self.billed = billed
        self.calls = 0

    def segment_keyframes(
        self, chunk: Chunk, frames: Sequence[Keyframe], concepts: Sequence[str]
    ) -> KeyframeBatch:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return KeyframeBatch(
            detections=dict.fromkeys(concepts, ()),
            requests=1,
            billed_images=len(frames) * len(concepts) if self.billed else 0,
            backend=self.backend,
        )


def chain(
    primary: Scripted, fallback: Scripted, clock: FakeClock, alerts: list[str]
) -> KeyframeFallback:
    return KeyframeFallback(
        primary,
        fallback,
        clock=clock,
        retry_after_s=60.0,
        on_fallback=lambda camera_id, error: alerts.append(f"{camera_id}:{type(error).__name__}"),
    )


def test_self_hosted_serves_while_it_works() -> None:
    primary, fallback = Scripted(Backend.SELF_HOSTED), Scripted(Backend.HOSTED, billed=True)
    keyframes = chain(primary, fallback, FakeClock(), [])
    batch = keyframes.segment_keyframes(make_chunk(), [], ("person",))
    assert batch.backend is Backend.SELF_HOSTED
    assert (primary.calls, fallback.calls) == (1, 0)
    assert not keyframes.degraded
    assert not keyframes.billed


def test_an_outage_falls_back_alerts_once_and_retries_after_the_window() -> None:
    clock, alerts = FakeClock(), list[str]()
    primary = Scripted(
        Backend.SELF_HOSTED, SamRuntimeUnavailableError("down"), SamResponseError("bad")
    )
    fallback = Scripted(Backend.HOSTED, billed=True)
    keyframes = chain(primary, fallback, clock, alerts)
    chunk = make_chunk()
    assert keyframes.segment_keyframes(chunk, [], ("person",)).backend is Backend.HOSTED
    assert keyframes.degraded
    assert keyframes.billed  # the budget now reserves hosted image prices
    clock.advance(30)
    assert keyframes.segment_keyframes(chunk, [], ("person",)).backend is Backend.HOSTED
    assert primary.calls == 1  # inside the window: straight to hosted
    clock.advance(31)
    assert keyframes.segment_keyframes(chunk, [], ("person",)).backend is Backend.HOSTED
    assert primary.calls == 2  # retried, failed again: same outage, no second alert
    clock.advance(61)
    assert keyframes.segment_keyframes(chunk, [], ("person",)).backend is Backend.SELF_HOSTED
    assert not keyframes.degraded
    assert alerts == ["cam1:SamRuntimeUnavailableError"]
    assert fallback.calls == 3


def test_invalid_requests_never_fall_back() -> None:
    primary = Scripted(Backend.SELF_HOSTED, ValueError("empty concept"))
    fallback = Scripted(Backend.HOSTED)
    keyframes = chain(primary, fallback, FakeClock(), [])
    with pytest.raises(ValueError, match="empty concept"):
        keyframes.segment_keyframes(make_chunk(), [], ("",))
    assert fallback.calls == 0


def test_a_failing_fallback_raises() -> None:
    primary = Scripted(Backend.SELF_HOSTED, SamRuntimeUnavailableError("down"))
    fallback = Scripted(Backend.HOSTED, SamRuntimeError("also down"))
    with pytest.raises(SamRuntimeError, match="also down"):
        chain(primary, fallback, FakeClock(), []).segment_keyframes(make_chunk(), [], ("p",))


@pytest.mark.parametrize(
    ("backend", "has_self_hosted", "expected"),
    [
        (Backend.SELF_HOSTED, True, KeyframeFallback),
        (Backend.SELF_HOSTED, False, Scripted),
        (Backend.HOSTED, True, Scripted),
    ],
)
def test_keyframe_chain_follows_the_policy(
    backend: Backend, has_self_hosted: bool, expected: type
) -> None:
    hosted = Scripted(Backend.HOSTED)
    self_hosted = Scripted(Backend.SELF_HOSTED) if has_self_hosted else None
    picked = keyframe_chain(
        hosted, self_hosted, policy=RoutingPolicy(runtime_backend=backend), clock=FakeClock()
    )
    assert isinstance(picked, expected)
    if expected is Scripted:
        assert picked is hosted
