"""The real-runtime factory and its small adapters (P3-08)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr

from rulecam.act import ActionExecutor
from rulecam.bus import Bus
from rulecam.clients.errors import ClientConfigError
from rulecam.config import Settings
from rulecam.evidence.chain import EventChain
from rulecam.perception.keyframe_tracker import KeyframeTracker
from rulecam.perception.keyframes import HostedKeyframes, Keyframe, SelfHostedKeyframes
from rulecam.perception.metrics import Backend, PerceptionMetrics
from rulecam.perception.router import KeyframeFallback, LimitedSegmenter, Purpose
from rulecam.perception.sam_runtime import HttpSamWorker, ModalWorker
from rulecam.replay.cache import ReplayCache
from rulecam.replay.errors import ReplayMiss
from rulecam.rules.backtest import DecisionContext, candidate_payload, read_candidate_payload
from rulecam.rules.store import ParquetTrackStore
from rulecam.runtime.factory import (
    ChainEventSource,
    ChunkIndex,
    LockedTrackStore,
    Perception,
    build_perception,
    build_runtime,
    build_tier1,
    glimmer_health_url,
    hosted_sam,
    self_hosted_worker,
)
from rulecam.runtime.loop import RuntimeLoop
from rulecam.runtime.strips import EpisodeStrips
from rulecam.verify.guard import GuardedVerifier
from rulecam.verify.policy import VerificationPolicy
from tests.fakes import EPOCH, FakeClock, make_chunk, make_track
from tests.rules.helpers import CAL, make_rule
from tests.verify.helpers import make_candidate


def settings(tmp_path: Path, **extra: object) -> Settings:
    return Settings(_env_file=None, rulecam_data_dir=tmp_path, **extra)  # type: ignore[arg-type]


def test_chunk_index_filters_by_camera_and_time() -> None:
    metrics = PerceptionMetrics()
    index = ChunkIndex(metrics, per_camera=2)
    for i in range(3):
        index.record(make_chunk(index=i))
    index.record(make_chunk(camera_id="cam2"))
    late = EPOCH + timedelta(seconds=3)
    assert [c.chunk_id for c in index.chunks("cam1", late, late + timedelta(seconds=1))] == [
        "cam1_000001"
    ]
    assert index.chunks("nope", EPOCH, late) == []
    assert set(index.last_seen()) == {"cam1", "cam2"}
    assert {c.camera_id for c in metrics.report().cameras} == {"cam1", "cam2"}


def test_chain_event_source_finds_candidate_records(tmp_path: Path) -> None:
    chain = EventChain(tmp_path, "site", clock=FakeClock())
    candidate = make_candidate()
    context = DecisionContext(
        rule=make_rule({"op": "inside", "object": "person", "zone": "sink_zone"}),
        calibration=CAL,
        zones={},
        t0=EPOCH,
        t1=EPOCH + timedelta(seconds=3),
    )
    chain.append("plan", {"p": 1})
    chain.append("candidate", {"legacy": True, "event_id": candidate.event_id})
    chain.append("candidate", candidate_payload(candidate, context))
    source = ChainEventSource(tmp_path, "site")
    found = source.find(candidate.event_id)
    assert found is not None
    assert found.seq == 3  # the runtime's nested payload, which the correction loop can read
    assert read_candidate_payload(found)[0] == candidate
    assert source.find("missing") is None
    assert ChainEventSource(tmp_path, "other").find(candidate.event_id) is None


def test_locked_track_store_delegates(tmp_path: Path) -> None:
    store = LockedTrackStore(ParquetTrackStore(tmp_path / "tracks"))
    track = make_track()
    store.append([track])
    got = store.window(track.camera_id, EPOCH - timedelta(hours=1), EPOCH + timedelta(hours=1))
    assert [t.track_id for t in got] == [track.track_id]


def test_hosted_sam_is_offline_without_keys(tmp_path: Path) -> None:
    bus = Bus()
    clock = FakeClock()
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not really a video")
    chunk = make_chunk().model_copy(update={"path": str(video)})
    live = ReplayCache(tmp_path / "replay", "live", clock=clock, bus=bus)
    sam = hosted_sam(settings(tmp_path), live, clock=clock, bus=bus)
    with pytest.raises(ClientConfigError):
        sam.segment(chunk, "person")
    replay = ReplayCache(tmp_path / "replay", "replay", clock=clock, bus=bus)
    keyed = settings(
        tmp_path, meta_api_base_url="https://example.invalid", meta_api_key=SecretStr("k")
    )
    with pytest.raises(ReplayMiss):  # replay never calls HTTP, even with keys
        hosted_sam(keyed, replay, clock=clock, bus=bus).segment(chunk, "person")


@pytest.mark.parametrize(
    ("extra", "backend"),
    [
        ({}, Backend.HOSTED),
        (
            {"modal_token_id": SecretStr("a"), "modal_token_secret": SecretStr("b")},
            Backend.SELF_HOSTED,
        ),
    ],
)
def test_build_perception_and_runtime(
    tmp_path: Path, extra: dict[str, object], backend: Backend
) -> None:
    bus = Bus()
    clock = FakeClock()
    cache = ReplayCache(tmp_path / "replay", "replay", clock=clock, bus=bus)
    cfg = settings(tmp_path, **extra)
    perception = build_perception(cfg, cache=cache, clock=clock, bus=bus)
    assert perception.router.policy.runtime_backend is backend
    assert (tmp_path / "tracks").is_dir()
    chain = EventChain(tmp_path / "chain", "site", clock=clock)
    loop = build_runtime(cfg, perception, cache=cache, clock=clock, bus=bus, chain=chain)
    assert loop.plan is None
    assert loop.handle_chunk(make_chunk()) == []
    assert isinstance(loop._strips, EpisodeStrips)  # the real renderer, not a placeholder
    assert isinstance(loop._policy, VerificationPolicy)  # the real P3-05 policy
    assert isinstance(loop._actions, ActionExecutor)  # the real P3-06 actions
    assert loop._actions.dry_run  # dry run until the owner turns it off


MODAL = {"modal_token_id": SecretStr("a"), "modal_token_secret": SecretStr("b")}
SAM_HTTP = {"sam_base_url": "http://127.0.0.1:9", "sam_api_key": SecretStr("k")}


@pytest.mark.parametrize(
    ("extra", "worker_type", "name"),
    [
        ({}, None, None),
        ({"sam_base_url": "http://127.0.0.1:9"}, None, None),  # a URL without a key is not enough
        (MODAL, ModalWorker, "modal"),
        (SAM_HTTP, HttpSamWorker, "http"),
        ({**MODAL, **SAM_HTTP}, HttpSamWorker, "http"),  # our GPU pod wins over Modal
    ],
)
def test_self_hosted_worker_precedence(
    tmp_path: Path, extra: dict[str, object], worker_type: type | None, name: str | None
) -> None:
    picked = self_hosted_worker(settings(tmp_path, **extra))
    if worker_type is None:
        assert picked is None
    else:
        assert picked is not None
        assert isinstance(picked[0], worker_type)
        assert picked[1] == name


def test_http_outage_falls_back_to_hosted_and_is_counted(tmp_path: Path) -> None:
    bus = Bus()
    clock = FakeClock()
    cache = ReplayCache(tmp_path / "replay", "replay", clock=clock, bus=bus)
    perception = build_perception(settings(tmp_path, **SAM_HTTP), cache=cache, clock=clock, bus=bus)
    assert perception.router.policy.runtime_backend is Backend.SELF_HOSTED
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not really a video")
    chunk = make_chunk().model_copy(update={"path": str(video)})
    with pytest.raises(ReplayMiss):  # nothing listens on port 9: hosted (replay-only) is tried
        perception.router.for_purpose(Purpose.RUNTIME).segment(chunk, "person")
    assert perception.metrics.report().total.sam_fallbacks == 1


@pytest.mark.parametrize("masks", [False, True])
def test_runtime_hosted_sam_is_box_only_by_default_and_reports_stages(
    tmp_path: Path, masks: bool
) -> None:
    bus = Bus()
    clock = FakeClock()
    cache = ReplayCache(tmp_path / "replay", "replay", clock=clock, bus=bus)
    cfg = settings(
        tmp_path,
        rulecam_runtime_sam_masks=masks,
        rulecam_sam_max_concurrency=3,
        rulecam_runtime_sam_mode="video",
    )
    perception = build_perception(cfg, cache=cache, clock=clock, bus=bus)
    runtime_hosted = perception.runtime_hosted
    assert runtime_hosted is not None
    assert runtime_hosted is not perception.hosted
    assert (runtime_hosted._mask_decoder is None) is not masks
    assert perception.hosted._mask_decoder is not None  # probes and calibration keep masks
    assert runtime_hosted.costs is perception.hosted.costs
    chain = EventChain(tmp_path / "chain", "site", clock=clock)
    loop = build_runtime(cfg, perception, cache=cache, clock=clock, bus=bus, chain=chain)
    assert runtime_hosted._stage_sink is not None
    runtime_hosted._stage_sink("upload", "cam1", "c1", 2.0)
    assert loop.metrics.stages["upload"].p50_s == 2.0
    limited = loop._tracker._segmenter._inner._inner  # type: ignore[attr-defined]
    assert isinstance(limited, LimitedSegmenter)
    assert limited.max_concurrent == 3


def test_tier1_is_glimmer_behind_retry_and_a_circuit_breaker(tmp_path: Path) -> None:
    clock, bus = FakeClock(), Bus()
    live = settings(
        tmp_path, rulecam_mode="live", rulecam_verify_retries=2, rulecam_verify_breaker_failures=5
    )
    cache = ReplayCache(tmp_path / "replay", "live", clock=clock, bus=bus)
    tier1 = build_tier1(live, cache=cache, clock=clock, bus=bus)
    assert isinstance(tier1, GuardedVerifier)
    policy = tier1.breaker.policy
    assert (policy.retries, policy.failure_threshold, policy.cooldown_s) == (2, 5, 30.0)
    assert tier1.health().state == "closed"
    replay = settings(tmp_path, rulecam_mode="replay")
    assert isinstance(build_tier1(replay, cache=cache, clock=clock, bus=bus), GuardedVerifier)


def test_glimmer_health_url_drops_the_api_suffix() -> None:
    assert glimmer_health_url("http://127.0.0.1:8080/v1") == "http://127.0.0.1:8080/health"
    assert glimmer_health_url("https://pod-8080.proxy.runpod.net/v1/") == (
        "https://pod-8080.proxy.runpod.net/health"
    )


# --- image mode (RULECAM_RUNTIME_SAM_MODE, WS2b) ----------------------------------------------


def _image_runtime(tmp_path: Path, **extra: object) -> tuple[Perception, RuntimeLoop]:
    bus = Bus()
    clock = FakeClock()
    cache = ReplayCache(tmp_path / "replay", "replay", clock=clock, bus=bus)
    cfg = settings(tmp_path, **extra)
    perception = build_perception(cfg, cache=cache, clock=clock, bus=bus)
    chain = EventChain(tmp_path / "chain", "site", clock=clock)
    return perception, build_runtime(
        cfg, perception, cache=cache, clock=clock, bus=bus, chain=chain
    )


def test_image_mode_is_the_default_and_hosted_without_a_gpu_pod(tmp_path: Path) -> None:
    assert Settings(_env_file=None).rulecam_runtime_sam_mode == "image"
    perception, loop = _image_runtime(tmp_path, rulecam_keyframe_fps_cameras="dock=4")
    tracker = loop._tracker
    assert isinstance(tracker, KeyframeTracker)
    assert isinstance(tracker._keyframes, HostedKeyframes)
    assert len(tracker.indices(make_chunk("dock"))) == 15
    assert len(tracker.indices(make_chunk("cam1"))) == 6
    assert loop._usage is tracker
    assert perception.image_worker is None
    assert loop.handle_chunk(make_chunk()) == []


def test_image_mode_on_modal_has_no_image_worker(tmp_path: Path) -> None:
    perception, loop = _image_runtime(tmp_path, **MODAL)
    assert perception.image_worker is None
    assert isinstance(loop._tracker._keyframes, HostedKeyframes)  # type: ignore[attr-defined]


def test_image_mode_on_our_gpu_pod_falls_back_to_hosted_images_and_counts_it(
    tmp_path: Path,
) -> None:
    perception, loop = _image_runtime(tmp_path, **SAM_HTTP)
    tracker = loop._tracker
    assert isinstance(tracker, KeyframeTracker)
    chain = tracker._keyframes
    assert isinstance(chain, KeyframeFallback)
    assert isinstance(chain._primary, SelfHostedKeyframes)
    assert isinstance(perception.image_worker, HttpSamWorker)
    frames = [Keyframe(0, EPOCH, b"\xff\xd8jpeg")]
    with pytest.raises(ReplayMiss):  # nothing on port 9; hosted (replay-only) is tried next
        chain.segment_keyframes(make_chunk(), frames, ("person",))
    assert chain.degraded
    assert perception.metrics.report().total.sam_fallbacks == 1
    assert tracker.estimate(make_chunk(), 1) == 75  # hosted prices while degraded


def test_bad_keyframe_rate_overrides_fail_at_startup(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="camera_id=fps"):
        _image_runtime(tmp_path, rulecam_keyframe_fps_cameras="dock")
