"""Add-on 1.3: CAVIAR activity labels -> eval-set events and clip.yaml entries."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml
from tests.eval.test_caviar_tracking import CLIP_B, caviar_xml, obj

from rulecam.eval.caviar import CaviarBox, CaviarSequence
from rulecam.eval.caviar_events import (
    EVENT_TYPES,
    EventType,
    RulePlan,
    clip_entry,
    event_spans,
    placeholder_entry,
)
from rulecam.eval.errors import EvalError
from rulecam.eval.schema import ClipEntry, load_eval_set

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "eval" / "caviar_clips.py"


def box(frame: int, oid: str, **labels: str) -> CaviarBox:
    return CaviarBox(frame=frame, object_id=oid, xc=10, yc=10, w=4, h=4, labels=labels)


def seq(
    objects: list[CaviarBox], groups: list[CaviarBox] | None = None, frames: int = 250
) -> CaviarSequence:
    return CaviarSequence("S", frames, tuple(objects), tuple(groups or ()))


FIGHT = EVENT_TYPES["fight"]


def test_event_matches_needles_in_the_watched_fields() -> None:
    assert FIGHT.matches({"context": "Fighting"})
    assert not FIGHT.matches({"context": "walking"})
    shop = EVENT_TYPES["shop_enter"]
    assert shop.matches({"situation": "shop enter"})
    assert not shop.matches({"context": "shop enter"})  # context covers the whole walk
    assert EVENT_TYPES["fall"].matches({"situation": "drop down"})
    assert EVENT_TYPES["left_bag"].matches({"role": "abandoned object"})


def test_spans_bridge_short_gaps_and_merge_people() -> None:
    # Person 1 fights frames 0-9 and 20-29 (gap 10 frames < 1 s): one span 0-29.
    # Person 2 fights 25-74 (overlaps): merged. Person 3 at 200-249: its own event.
    objects = [box(f, "1", context="fighting") for f in [*range(10), *range(20, 30)]]
    objects += [box(f, "2", situation="fighting") for f in range(25, 75)]
    objects += [box(f, "3", context="fighting") for f in range(200, 250)]
    objects += [box(f, "4", context="walking") for f in range(250)]
    assert event_spans(seq(objects), FIGHT) == [(0.0, 3.0), (8.0, 10.0)]


def test_short_events_are_widened_and_groups_count() -> None:
    groups = [box(f, "0", context="fighting") for f in range(100, 105)]
    assert event_spans(seq([], groups), FIGHT) == [(4.0, 5.0)]
    # Widening stops at the end of the clip, and a widened event absorbs the next one.
    late = [box(248, "1", context="fighting"), box(249, "1", context="fighting")]
    assert event_spans(seq(late), FIGHT) == [(9.92, 10.0)]
    close = [box(0, "1", context="fighting"), box(20, "2", context="fighting")]
    assert event_spans(seq(close), FIGHT, merge_gap_s=0.5) == [(0.0, 1.8)]


def test_clip_entry_labels_and_validates() -> None:
    sequence = seq([box(f, "1", context="fighting") for f in range(50, 100)])
    entry = clip_entry(
        sequence,
        name="caviar_x",
        camera_id="caviar-inria-lobby",
        url="https://example.org/x.mpg",
        sha256="0" * 64,
        rules=[RulePlan("fight"), RulePlan("left_bag", negative=True)],
        tracks_file="gt.txt",
    )
    clip = ClipEntry.model_validate(entry)
    assert clip.duration_s == 10.0
    assert clip.rule("caviar_fight").events[0].start_s == 2.0
    assert clip.rule("caviar_left_bag").status == "negative"
    assert clip.tracks is not None
    assert clip.tracks.width == 384


def test_clip_entry_refuses_wrong_labels() -> None:
    fights = seq([box(f, "1", context="fighting") for f in range(50)])
    common: dict[str, Any] = {
        "name": "caviar_x",
        "camera_id": "c",
        "url": "https://e.org/x",
        "sha256": "0" * 64,
    }
    with pytest.raises(EvalError, match="negative control"):
        clip_entry(fights, rules=[RulePlan("fight", negative=True)], **common)
    with pytest.raises(EvalError, match="no label matches fall"):
        clip_entry(fights, rules=[RulePlan("fall")], **common)
    with pytest.raises(EvalError, match="invalid"):
        clip_entry(fights, rules=[RulePlan("fight"), RulePlan("fight")], **common)


def test_placeholder_entry() -> None:
    entry = placeholder_entry(
        name="caviar_y",
        camera_id="c",
        rules=[RulePlan("fall")],
        description="A fall.",
        url="https://e.org/y.mpg",
    )
    clip = ClipEntry.model_validate(entry)
    assert clip.placeholder
    assert clip.rule("caviar_fall").status == "unlabelled"
    assert clip.rule("caviar_fall").request == EVENT_TYPES["fall"].request


def test_custom_event_type() -> None:
    event = EventType(rule_id="r", request="q", needles=("meet",), fields=("context",))
    assert event_spans(seq([box(3, "1", context="Meeting")]), event) == [(0.12, 1.12)]


# --- scripts/eval/caviar_clips.py -----------------------------------------------------------


def load_script() -> ModuleType:
    sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("caviar_clips", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_script_writes_corridor_clips_and_placeholders(tmp_path: Path) -> None:
    script = load_script()
    assert script.slug("Rest_FallOnFloor") == "caviar_rest_fall_on_floor"
    assert script.slug("WalkByShop1cor") == "caviar_walk_by_shop1cor"
    src, out = tmp_path / "src", tmp_path / "clips"
    src.mkdir()
    (src / "TwoEnterShop1cor.xml").write_text(CLIP_B, encoding="utf-8")
    (src / "TwoEnterShop1cor.mpg").write_bytes(b"video")
    assert (
        script.main(
            ["corridor", "--src", str(src), "--out", str(out), "--only", "TwoEnterShop1cor"]
        )
        == 0
    )
    entry = yaml.safe_load((out / "caviar_two_enter_shop1cor" / "clip.yaml").read_text("utf-8"))
    assert entry["rules"][0]["status"] == "labelled"
    assert (out / "caviar_two_enter_shop1cor" / "gt.txt").read_text("utf-8").startswith("1,1,")
    assert script.main(["inria", "--placeholders", "--out", str(out)]) == 0
    evalset = load_eval_set(out)
    assert len(evalset.runnable()) == 1
    assert len(evalset.clips) == 1 + len(script.INRIA)
    with pytest.raises(SystemExit, match=r"need WalkByShop1cor\.xml"):
        script.main(["corridor", "--src", str(src), "--out", str(out), "--only", "WalkByShop1cor"])
    with pytest.raises(SystemExit):
        script.main(["inria"])


def test_script_inventory(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    script = load_script()
    xml = tmp_path / "a.xml"
    xml.write_text(caviar_xml("A", [[obj("1", 10, 10, situation="shop enter")]]), "utf-8")
    assert script.main(["inventory", str(xml)]) == 0
    assert '"shop enter": 1' in capsys.readouterr().out
