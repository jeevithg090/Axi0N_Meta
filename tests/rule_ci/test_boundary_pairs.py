"""Unit tests for boundary-pair Muse Image edits (P2 stretch task, MP §3.2, §12)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest
import yaml
from PIL import Image

from rulecam.clients.meta_hosted.image import ImageResult
from rulecam.contracts import Detection, Mask, RuleIR
from rulecam.contracts.core import Calibration
from rulecam.rule_ci.boundary_pairs import (
    BoundaryPairCase,
    project_pixel_to_ground,
    render_boundary_pair,
    run_boundary_pairs_suite,
)
from tests.fakes import EPOCH

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "rules"


def _rule(name: str = "wash-before-line") -> RuleIR:
    return RuleIR.model_validate(yaml.safe_load((FIXTURES / f"{name}.rule.yaml").read_text()))


class ChainedFakeRenderer:
    """Fake Muse Image client recording `previous_response_id`."""

    def __init__(self, out: Path) -> None:
        self.out = out
        self.calls: list[tuple[str, str | None]] = []

    def generate(
        self,
        prompt: str,
        *,
        reference_images: Sequence[Path] = (),
        size: str = "1024x1024",
        previous_response_id: str | None = None,
    ) -> ImageResult:
        self.calls.append((prompt, previous_response_id))
        idx = len(self.calls)
        path = self.out / f"img_{idx}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (320, 240), (idx * 20, 50, 100)).save(path)
        return ImageResult(path=path, response_id=f"resp_{idx}")


class FakeSegmenter:
    """Fake SAM 3.1 segmenter returning detections based on image path name."""

    def __init__(self, detections_by_image: dict[str, list[Detection]]) -> None:
        self.detections_by_image = detections_by_image

    def segment_image(
        self,
        frame: Path,
        concept: str,
        *,
        camera_id: str = "probe",
        include_confidence: bool = True,
    ) -> list[Detection]:
        return self.detections_by_image.get(f"{frame.name}:{concept}", [])


@pytest.fixture
def frame(tmp_path: Path) -> Path:
    p = tmp_path / "reference_frame.png"
    Image.new("RGB", (320, 240), (50, 50, 50)).save(p)
    return p


def test_project_pixel_to_ground() -> None:
    # 0.01 scale homography: 100px -> 1.0m
    cal = Calibration(
        camera_id="cam1",
        homography=((0.01, 0.0, 0.0), (0.0, 0.01, 0.0), (0.0, 0.0, 1.0)),
        method="clicks4",
        err_m=0.02,
    )
    gx, gy = project_pixel_to_ground(150.0, 250.0, cal)
    assert pytest.approx(gx, 0.001) == 1.5
    assert pytest.approx(gy, 0.001) == 2.5


def test_render_boundary_pair_chains_previous_response_id(tmp_path: Path, frame: Path) -> None:
    renderer = ChainedFakeRenderer(tmp_path / "renders")
    # Base still (img_1) has hands; Edit still (img_2) has no hands
    mask = Mask(rle="x", box_xyxy=(50.0, 50.0, 100.0, 100.0), confidence=0.9)
    det = Detection(
        camera_id="cam1", frame_idx=0, ts=EPOCH, concept="hands", object_id=1, mask=mask
    )
    segmenter = FakeSegmenter({"img_1.png:hands": [det]})

    case = BoundaryPairCase(
        name="hands_removed_edit",
        concept="hands",
        base_prompt="chef standing at the prep counter with hands on the board",
        edit_prompt="the same chef steps back and removes hands from the board",
        expected_base_firing=True,
        expected_edit_firing=False,
    )

    rule = _rule()
    result = render_boundary_pair(
        case, rule, frame, renderer=renderer, segmenter=segmenter, camera_id="cam1"
    )

    assert result.passed is True
    assert result.base_fired is True
    assert result.edit_fired is False
    assert len(renderer.calls) == 2
    # Turn 1: initial generation
    assert renderer.calls[0] == (case.base_prompt, None)
    # Turn 2: chained edit with previous_response_id
    assert renderer.calls[1] == (case.edit_prompt, "resp_1")


def test_run_boundary_pairs_suite(tmp_path: Path, frame: Path) -> None:
    renderer = ChainedFakeRenderer(tmp_path / "renders")
    mask = Mask(rle="x", box_xyxy=(10.0, 10.0, 80.0, 80.0), confidence=0.95)
    det = Detection(
        camera_id="cam1", frame_idx=0, ts=EPOCH, concept="person", object_id=1, mask=mask
    )
    # Both img_1 and img_2 see person
    segmenter = FakeSegmenter({"img_1.png:person": [det], "img_2.png:person": [det]})

    cases = [
        BoundaryPairCase(
            name="case_1",
            concept="person",
            base_prompt="person enters",
            edit_prompt="person stays",
            expected_base_firing=True,
            expected_edit_firing=True,
        )
    ]

    report = run_boundary_pairs_suite(
        _rule(), frame, cases, renderer=renderer, segmenter=segmenter, camera_id="cam1"
    )

    assert report.total == 1
    assert report.passed == 1
    assert report.pass_rate == 1.0
    assert "1/1 verified" in report.summary()
