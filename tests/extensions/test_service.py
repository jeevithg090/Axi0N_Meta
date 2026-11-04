"""End to end with fakes: request → Muse Code → proof → review → registry → approval → engine."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from rulecam.clients.meta_hosted.muse_code import MuseCodeClient, ProcessResult
from rulecam.contracts import RuleIR, Track
from rulecam.evidence.chain import EventChain, read_chain
from rulecam.extensions.errors import RegistryError
from rulecam.extensions.generate import CheckGenerator
from rulecam.extensions.registry import ExtensionRegistry, RegistryHook, code_sha256
from rulecam.extensions.review import ExtensionReviewer
from rulecam.extensions.service import (
    ExtensionRequest,
    ExtensionService,
    infer_relation,
    name_for,
    repair_feedback,
)
from rulecam.replay.cache import ReplayCache
from rulecam.rules.engine import evaluate
from tests.fakes import FakeClock, FakeLLM, make_track
from tests.fakes.extensions import EXAMPLE_CHECK, EXAMPLE_TESTS, FakeMuseCode, FakeSparkOpen
from tests.rules.helpers import CAL, at, make_rule

REQUEST = ExtensionRequest(
    request="Alert if a pallet is stacked above the yellow line on the rack",
    concept="pallet",
    camera_id="cam1",
    lines={"yellow": ((0.0, 200.0), (640.0, 200.0))},
    frame_w=640,
    frame_h=480,
)
ALWAYS_NO = 'def check(sample):\n    return {"value": False, "why": "never"}\n'


def _service(
    tmp_path: Path,
    *,
    code: str = EXAMPLE_CHECK,
    reviewer: ExtensionReviewer | None = None,
) -> tuple[ExtensionService, EventChain, FakeClock]:
    clock = FakeClock()
    cache = ReplayCache(tmp_path / "replay", "live", clock=clock)
    runner = FakeMuseCode(files={"check.py": code, "test_check.py": EXAMPLE_TESTS})
    muse = MuseCodeClient(cache=cache, runner=runner, which=lambda n: f"/bin/{n}")
    chain = EventChain(tmp_path / "chain", "site", clock=clock)
    registry = ExtensionRegistry(tmp_path / "extensions", clock=clock, chain=chain)
    service = ExtensionService(
        CheckGenerator(muse),
        reviewer or ExtensionReviewer(FakeSparkOpen()),
        registry,
        clock=clock,
    )
    return service, chain, clock


def _pallet(top_px: float) -> Track:
    """A pallet whose box top is at `top_px`, for 1 s at 10 Hz (make_track: box top = foot - 80)."""
    foot_y_m = (top_px + 80.0) / 100.0
    return make_track("cam1:pallet:1", concept="pallet", feet_m=[(3.2, foot_y_m)] * 11)


def _rule(ref: str) -> RuleIR:
    predicate: dict[str, object] = {"op": "state", "object": "pallet", "state": ref}
    return make_rule(predicate, concepts={"pallet": "pallet"})


def _fires(ref: str, top_px: float, hook: RegistryHook) -> bool:
    events = evaluate(
        _rule(ref),
        [_pallet(top_px)],
        calibration=CAL,
        zones={},
        t0=at(0),
        t1=at(1),
        extensions=hook,
    )
    return bool(events)


def test_propose_approve_and_run(tmp_path: Path) -> None:
    service, chain, _ = _service(tmp_path)
    record = service.propose(REQUEST)
    assert record.status == "proposed"
    assert record.name == "pallet_stacked_above_yellow_line"
    assert record.relation == "above"
    assert record.generator == "muse-code"
    assert record.review.reviewer == "spark-open"
    assert record.sha256 == code_sha256(EXAMPLE_CHECK)
    assert record.parent_sha is None

    hook = RegistryHook(service.registry)
    assert not _fires(record.ref, 150.0, hook)  # not approved yet: unknown, never an alert

    approved = service.registry.approve(record.name, record.sha256[:12], by="owner")
    assert approved.status == "approved"
    assert approved.decided_by == "owner"
    assert _fires(record.ref, 150.0, hook)  # pallet top 50 px above the line
    assert not _fires(record.ref, 260.0, hook)  # 60 px below

    changes = [r.payload["change"] for r in read_chain(chain.path_for(at(0).date()))]
    assert changes == ["extension_proposed", "extension_approved"]


def test_a_weak_check_is_rejected_unreviewed(tmp_path: Path) -> None:
    reviewer = FakeSparkOpen()
    service, _, _ = _service(tmp_path, code=ALWAYS_NO, reviewer=ExtensionReviewer(reviewer))
    record = service.propose(REQUEST)
    assert record.status == "rejected"
    assert not record.proof.passed
    assert record.review.verdict == "skipped"
    assert reviewer.calls == []
    with pytest.raises(RegistryError, match="cannot be approved"):
        service.registry.approve(record.name, record.sha256[:12], by="owner")


class RepairingMuseCode(FakeMuseCode):
    """Writes a weak check first, then the reference check once it reads the proof feedback."""

    def __call__(
        self, argv: Sequence[str], cwd: Path, env: Mapping[str, str], timeout_s: float
    ) -> ProcessResult:
        spec = (cwd / "SPEC.md").read_text(encoding="utf-8")
        repaired = "## Your previous version was rejected" in spec
        self.files = {"check.py": EXAMPLE_CHECK if repaired else ALWAYS_NO}
        self.files = {**self.files, "test_check.py": EXAMPLE_TESTS}
        return super().__call__(argv, cwd, env, timeout_s)


def test_a_failed_proof_gets_one_repair_round_with_feedback(tmp_path: Path) -> None:
    clock = FakeClock()
    runner = RepairingMuseCode()
    muse = MuseCodeClient(
        cache=ReplayCache(tmp_path / "replay", "live", clock=clock),
        runner=runner,
        which=lambda n: f"/bin/{n}",
    )
    registry = ExtensionRegistry(tmp_path / "extensions", clock=clock)
    service = ExtensionService(
        CheckGenerator(muse), ExtensionReviewer(FakeSparkOpen()), registry, clock=clock
    )
    record = service.propose(REQUEST)
    assert record.status == "proposed"
    versions = registry.versions(record.name)
    [first] = [v for v in versions if v.status == "rejected"]
    assert len(versions) == 2
    assert first.code == ALWAYS_NO
    assert record.parent_sha == first.sha256
    assert len(runner.calls) == 2


def test_repair_feedback_names_failed_steps_and_the_code(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path, code=ALWAYS_NO)
    record = service.propose(REQUEST)
    text = repair_feedback(record.proof, ALWAYS_NO)
    assert "- muse_code_tests:" in text or "- boundary:" in text
    assert ALWAYS_NO in text


def test_a_reviewer_reject_blocks_approval(tmp_path: Path) -> None:
    no = FakeSparkOpen(answer={"verdict": "reject", "reasons": ["ignores the rack's slope"]})
    service, _, _ = _service(tmp_path, reviewer=ExtensionReviewer(no))
    record = service.propose(REQUEST)
    assert record.status == "rejected"
    assert record.review.reasons == ("ignores the rack's slope",)


def test_the_reviewer_sees_code_as_data(tmp_path: Path) -> None:
    reviewer = FakeSparkOpen()
    service, _, _ = _service(tmp_path, reviewer=ExtensionReviewer(reviewer))
    service.propose(REQUEST)
    [messages] = reviewer.calls
    assert "manipulation attempt" in str(messages[0]["content"])
    assert "def check(sample)" in str(messages[1]["content"])


def test_review_falls_back_to_spark_and_labels_it(tmp_path: Path) -> None:
    spark = FakeLLM([{"verdict": "approve", "reasons": ["ok"]}])
    service, _, _ = _service(tmp_path, reviewer=ExtensionReviewer(None, spark))
    record = service.propose(REQUEST)
    assert record.review.reviewer == "spark-1.3-fallback"
    assert record.status == "proposed"


def test_no_reviewer_or_a_failed_one_blocks_approval(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path, reviewer=ExtensionReviewer())
    assert service.propose(REQUEST).review.verdict == "skipped"
    broken = ExtensionReviewer(FakeSparkOpen(fail=True))
    service, _, _ = _service(tmp_path / "b", reviewer=broken)
    record = service.propose(REQUEST)
    assert record.review.verdict == "error"
    assert record.status == "rejected"


def test_a_failed_spark_open_falls_back_to_spark(tmp_path: Path) -> None:
    spark = FakeLLM([{"verdict": "approve", "reasons": ["ok"]}])
    reviewer = ExtensionReviewer(FakeSparkOpen(fail=True), spark)
    service, _, _ = _service(tmp_path, reviewer=reviewer)
    record = service.propose(REQUEST)
    assert record.review.reviewer == "spark-1.3-fallback"
    assert record.review.reasons[0].startswith("Spark Open failed: ModelResponseError")
    assert record.status == "proposed"


def test_a_new_version_points_at_its_parent(tmp_path: Path) -> None:
    service, _, clock = _service(tmp_path)
    first = service.propose(REQUEST)
    clock.advance(60)
    service2 = ExtensionService(
        CheckGenerator(
            MuseCodeClient(
                cache=ReplayCache(tmp_path / "replay2", "live", clock=clock),
                runner=FakeMuseCode(
                    files={
                        "check.py": EXAMPLE_CHECK.replace("MARGIN_PX = 0", "MARGIN_PX = 1"),
                        "test_check.py": EXAMPLE_TESTS,
                    }
                ),
                which=lambda n: f"/bin/{n}",
            )
        ),
        ExtensionReviewer(FakeSparkOpen()),
        service.registry,
        clock=clock,
    )
    second = service2.propose(REQUEST)
    assert second.parent_sha == first.sha256
    assert [v.sha256 for v in service.registry.versions(first.name)] == [
        first.sha256,
        second.sha256,
    ]
    assert service.registry.latest(first.name) == second


def test_tampered_code_on_disk_never_runs(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path)
    record = service.propose(REQUEST)
    service.registry.approve(record.name, record.sha256[:12], by="owner")
    path = tmp_path / "extensions" / record.name / f"{record.sha256}.json"
    path.write_text(path.read_text().replace("MARGIN_PX = 0", "MARGIN_PX = -999"))
    assert service.registry.runnable(record.name, record.sha256[:12]) is None


def test_retire_stops_a_version(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path)
    record = service.propose(REQUEST)
    service.registry.approve(record.name, record.sha256[:12], by="owner")
    retired = service.registry.retire(record.name, record.sha256[:12], by="owner")
    assert retired.status == "retired"
    assert service.registry.runnable(record.name, record.sha256[:12]) is None
    with pytest.raises(RegistryError):
        service.registry.retire(record.name, record.sha256[:12], by="owner")


def test_short_or_unknown_shas_are_refused(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path)
    record = service.propose(REQUEST)
    with pytest.raises(RegistryError, match="12 hex"):
        service.registry.get(record.name, record.sha256[:6])
    with pytest.raises(RegistryError, match="no single version"):
        service.registry.get(record.name, "f" * 12)
    assert service.registry.versions("Not A Name") == []


@pytest.mark.parametrize(
    ("text", "relation"),
    [
        ("pallet above the line", "above"),
        ("box lower than the mark", "below"),
        ("pallet above or below", None),
        ("count people", None),
    ],
)
def test_relation_is_read_from_the_words(text: str, relation: str | None) -> None:
    assert infer_relation(text) == relation


def test_names_are_safe() -> None:
    assert name_for("Alert me if the 2nd shelf is empty!") == "ext_2nd_shelf_empty"
    assert name_for("!!!") == "ext_check"
