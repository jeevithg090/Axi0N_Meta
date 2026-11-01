"""Eval-set schema validation, loading, and the committed seed set."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from rulecam.eval.errors import EvalSetError
from rulecam.eval.schema import ClipEntry, load_eval_set

REPO_SET = Path(__file__).resolve().parents[2] / "eval" / "clips"
SHA = "a" * 64


def entry(**over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": "yard",
        "camera_id": "yard",
        "source": {"path": "clip.mp4"},
        "duration_s": 30.0,
        "rules": [
            {
                "id": "forklift_2m",
                "request": "Keep people 2 m from a moving forklift.",
                "status": "labelled",
                "events": [{"start_s": 3, "end_s": 6}, {"start_s": 10, "end_s": 12}],
            }
        ],
    }
    data.update(over)
    return data


def test_a_valid_entry() -> None:
    clip = ClipEntry.model_validate(entry())
    assert clip.rule("forklift_2m").events[1].kind == "positive"
    with pytest.raises(EvalSetError):
        clip.rule("nope")


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"source": {"path": "a.mp4", "url": "https://x/a.mp4", "sha256": SHA}}, "exactly one"),
        ({"source": {}}, "exactly one"),
        ({"source": {"url": "https://x/a.mp4"}}, "sha256"),
        ({"source": {"url": "http://x/a.mp4", "sha256": SHA}}, "https"),
        ({"source": {"url": "https://x/a.mp4", "sha256": "abc"}}, "sha256"),
        ({"source": None}, "placeholder"),
        ({"duration_s": None}, "placeholder"),
        ({"name": "Bad Name"}, "name"),
        ({"rules": []}, "rules"),
    ],
)
def test_invalid_entries(over: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        ClipEntry.model_validate(entry(**over))


def rule(**over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {"id": "r", "request": "x", "status": "labelled", "events": []}
    data.update(over)
    return data


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        (rule(events=[]), "has no events"),
        (rule(status="negative", events=[{"start_s": 1, "end_s": 2}]), "must not list events"),
        (rule(status="unlabelled", events=[{"start_s": 1, "end_s": 2}]), "must not list events"),
        (rule(events=[{"start_s": 5, "end_s": 5}]), "must be after"),
        (rule(events=[{"start_s": -1, "end_s": 2}]), "greater than or equal"),
        (rule(events=[{"start_s": 1, "end_s": 5}, {"start_s": 4, "end_s": 6}]), "overlapping"),
        (rule(events=[{"start_s": 1, "end_s": 40}]), "after the clip"),
        (rule(events=[{"start_s": 1, "end_s": 2, "kind": "negative"}]), "positive"),
    ],
)
def test_invalid_rules(bad: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        ClipEntry.model_validate(entry(rules=[bad]))


def test_duplicate_rule_ids_are_rejected() -> None:
    twice = [rule(status="negative"), rule(status="negative")]
    with pytest.raises(ValidationError, match="twice"):
        ClipEntry.model_validate(entry(rules=twice))


def test_placeholder_needs_no_source() -> None:
    clip = ClipEntry.model_validate(
        entry(source=None, duration_s=None, placeholder=True, rules=[rule(status="unlabelled")])
    )
    assert clip.placeholder


def write(root: Path, name: str, data: dict[str, Any]) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    path = folder / "clip.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_load_eval_set(tmp_path: Path) -> None:
    write(tmp_path, "yard", entry())
    write(tmp_path, "later", entry(name="later", source=None, duration_s=None, placeholder=True))
    evalset = load_eval_set(tmp_path)
    assert [c.name for c in evalset.clips] == ["later", "yard"]
    assert [c.name for c in evalset.runnable()] == ["yard"]
    assert evalset.clip_dir("yard") == tmp_path / "yard"
    with pytest.raises(EvalSetError):
        evalset.clip("missing")


def test_load_rejects_bad_files(tmp_path: Path) -> None:
    with pytest.raises(EvalSetError, match=r"no clip\.yaml"):
        load_eval_set(tmp_path)
    write(tmp_path, "other", entry())
    with pytest.raises(EvalSetError, match="must equal its folder"):
        load_eval_set(tmp_path)


def test_load_rejects_yaml_errors_and_missing_tracks(tmp_path: Path) -> None:
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "clip.yaml").write_text("name: [unclosed", encoding="utf-8")
    with pytest.raises(EvalSetError, match="broken"):
        load_eval_set(tmp_path)
    (tmp_path / "broken" / "clip.yaml").unlink()
    tracks = {"file": "gt.txt", "fps": 25, "classes": {1: "person"}}
    write(tmp_path, "yard", entry(tracks=tracks))
    with pytest.raises(EvalSetError, match="tracks file"):
        load_eval_set(tmp_path)
    (tmp_path / "yard" / "gt.txt").write_text("", encoding="utf-8")
    assert load_eval_set(tmp_path).clip("yard").tracks is not None


def test_invalid_schema_is_reported_with_its_path(tmp_path: Path) -> None:
    write(tmp_path, "yard", entry(duration_s=-1))
    with pytest.raises(EvalSetError, match="yard"):
        load_eval_set(tmp_path)


def test_the_committed_seed_set() -> None:
    evalset = load_eval_set(REPO_SET)
    warehouse = evalset.clip("warehouse_45848")
    assert warehouse.rule("forklift_2m").status == "negative"
    assert warehouse.source is not None
    assert warehouse.source.sha256 is not None
    kitchen = evalset.clip("kitchen_15875")
    assert kitchen.rule("forklift_2m").status == "negative"
    assert kitchen.rule("handwash_20s").status == "unlabelled"
    placeholders = [c for c in evalset.clips if c.placeholder]
    assert len(placeholders) >= 12
    assert {c.name for c in evalset.runnable()} == {"warehouse_45848", "kitchen_15875"}
