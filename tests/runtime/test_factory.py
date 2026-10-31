"""The real-runtime factory and its small adapters (P3-08)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr

from rulecam.bus import Bus
from rulecam.clients.errors import ClientConfigError
from rulecam.config import Settings
from rulecam.contracts import PrivacyMode
from rulecam.evidence.chain import EventChain
from rulecam.perception.metrics import Backend, PerceptionMetrics
from rulecam.replay.cache import ReplayCache
from rulecam.replay.errors import ReplayMiss
from rulecam.rules.backtest import DecisionContext, candidate_payload, read_candidate_payload
from rulecam.rules.store import ParquetTrackStore
from rulecam.runtime.factory import (
    ChainEventSource,
    ChunkIndex,
    LockedTrackStore,
    NoActions,
    NoStrips,
    VerifyOnlyPolicy,
    build_perception,
    build_runtime,
    hosted_sam,
)
from tests.fakes import EPOCH, FakeClock, make_chunk, make_track, make_verdict
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


def test_placeholders_never_act_and_attach_no_pixels() -> None:
    rule = make_rule({"op": "inside", "object": "person", "zone": "z"})
    candidate = make_candidate().model_copy(update={"strip_paths": ("raw/frame.png",)})
    policy = VerifyOnlyPolicy()
    assert policy.act_first(candidate, rule) is False
    decision = policy.decide(candidate, rule, make_verdict(verdict="violation"))
    assert (decision.alert, decision.route) == (True, "human_queue")
    assert not policy.decide(candidate, rule, make_verdict(verdict="no_violation")).alert
    assert NoActions().run(candidate, rule, decision) == []
    stripped = NoStrips().render(candidate, make_chunk(), PrivacyMode.STANDARD)
    assert stripped.strip_paths == ()


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
