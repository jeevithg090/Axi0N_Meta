"""The E1 manifest layer and its consent gate (task P4-06)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from rulecam.eval.manifests import (
    ConsentError,
    E1Manifest,
    ManifestError,
    check_against_set,
    file_sha256,
    load_manifest,
    unlabelled,
)
from rulecam.eval.schema import ClipEntry

ZERO = "0" * 64
FL_EVENT = {"start_s": 1.0, "end_s": 3.0}


def clip(
    name: str = "owner_fl_pos_1m", *, rule_id: str = "forklift_2m", **rule: object
) -> ClipEntry:
    """A clip.yaml entry with one rule; by default `labelled` with one event (a positive)."""
    labels: dict[str, object] = {
        "id": rule_id,
        "request": "Keep people 2 m from a forklift.",
        "status": "labelled",
        "events": [FL_EVENT],
    } | rule
    return ClipEntry.model_validate(
        {"name": name, "camera_id": "cam1", "placeholder": True, "rules": [labels]}
    )


def entry(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "path": "eval/clips/owner_fl_pos_1m/clip.mp4",
        "sha256": ZERO,
        "use_case": "uc1",
        "rule_id": "forklift_2m",
        "label": "positive",
        "staged_by": "team",
        "provenance": "recorded",
        "consent": True,
        "consent_form": "form-001",
    }
    return base | over


def write(tmp_path: Path, entries: list[dict[str, object]], **over: object) -> Path:
    path = tmp_path / "manifest.yaml"
    body: dict[str, object] = {"name": "e1-dock", "entries": entries} | over
    path.write_text(yaml.safe_dump(body), encoding="utf-8")
    return path


def test_a_consented_manifest_loads(tmp_path: Path) -> None:
    manifest = load_manifest(write(tmp_path, [entry()]))
    assert manifest.name == "e1-dock"
    assert manifest.entries[0].clip_name == "owner_fl_pos_1m"
    assert manifest.use_cases() == ("uc1",)


def test_a_clip_without_consent_is_refused(tmp_path: Path) -> None:
    path = write(tmp_path, [entry(consent=False, consent_form="")])
    with pytest.raises(ConsentError, match="no signed consent form"):
        load_manifest(path)


def test_consent_defaults_to_false_so_a_missing_field_is_refused(tmp_path: Path) -> None:
    """Leaving `consent` out must not quietly admit the clip."""
    fields = entry()
    del fields["consent"]
    del fields["consent_form"]
    with pytest.raises(ConsentError):
        load_manifest(write(tmp_path, [fields]))


def test_the_refusal_names_every_clip_that_needs_a_form(tmp_path: Path) -> None:
    rows = [
        entry(consent=False, consent_form=""),
        entry(path="eval/clips/b/clip.mp4", consent=False, consent_form=""),
        entry(path="eval/clips/c/clip.mp4"),
    ]
    with pytest.raises(ConsentError) as caught:
        load_manifest(write(tmp_path, rows))
    message = str(caught.value)
    assert "2 clip(s)" in message
    assert "eval/clips/b/clip.mp4" in message
    assert "eval/clips/owner_fl_pos_1m/clip.mp4" in message
    assert "eval/clips/c/clip.mp4" not in message


def test_claiming_consent_without_a_form_id_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="no consent_form id"):
        load_manifest(write(tmp_path, [entry(consent_form="  ")]))


def test_a_missing_file_and_a_non_mapping_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="no E1 manifest"):
        load_manifest(tmp_path / "absent.yaml")
    path = tmp_path / "list.yaml"
    path.write_text("- one\n- two\n", encoding="utf-8")
    with pytest.raises(ManifestError, match="must be a mapping"):
        load_manifest(path)


def test_an_unknown_field_and_a_bad_label_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ManifestError):
        load_manifest(write(tmp_path, [entry(surprise="yes")]))
    with pytest.raises(ManifestError):
        load_manifest(write(tmp_path, [entry(label="maybe")]))


def test_the_same_clip_and_rule_twice_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="twice"):
        load_manifest(write(tmp_path, [entry(), entry()]))


def test_the_same_clip_with_two_rules_is_allowed(tmp_path: Path) -> None:
    manifest = load_manifest(write(tmp_path, [entry(), entry(rule_id="helmet_required")]))
    assert len(manifest.for_rule("forklift_2m")) == 1
    assert len(manifest.for_rule("helmet_required")) == 1


def test_a_clip_outside_the_eval_set_is_refused(tmp_path: Path) -> None:
    manifest = load_manifest(write(tmp_path, [entry()]))
    check_against_set(manifest, [clip()])
    with pytest.raises(ManifestError, match="not in the eval set"):
        check_against_set(manifest, [clip("something_else")])


def test_a_rule_dropped_from_clip_yaml_is_refused(tmp_path: Path) -> None:
    manifest = load_manifest(write(tmp_path, [entry()]))
    with pytest.raises(ManifestError, match="no longer lists rule 'forklift_2m'"):
        check_against_set(manifest, [clip(rule_id="helmet_required")])


@pytest.mark.parametrize(
    ("label", "status", "events"),
    [("positive", "negative", []), ("negative", "labelled", [FL_EVENT])],
)
def test_a_label_that_contradicts_clip_yaml_is_refused(
    tmp_path: Path, label: str, status: str, events: list[object]
) -> None:
    """A relabelled clip must not be measured under the manifest entry written for the old label."""
    manifest = load_manifest(write(tmp_path, [entry(label=label)]))
    with pytest.raises(ManifestError, match="needs its manifest entry reviewed"):
        check_against_set(manifest, [clip(status=status, events=events)])


def test_an_unlabelled_rule_agrees_with_either_label(tmp_path: Path) -> None:
    for label in ("positive", "negative"):
        manifest = load_manifest(write(tmp_path, [entry(label=label)]))
        check_against_set(manifest, [clip(status="unlabelled", events=[])])


def test_a_changed_clip_cannot_reuse_its_consent(tmp_path: Path) -> None:
    """A re-recording has a new hash, so the old permission must not cover it."""
    folder = tmp_path / "eval" / "clips" / "owner_fl_pos_1m"
    folder.mkdir(parents=True)
    (folder / "clip.mp4").write_bytes(b"first cut")
    digest = hashlib.sha256(b"first cut").hexdigest()
    manifest = load_manifest(write(tmp_path, [entry(sha256=digest)]))
    check_against_set(manifest, [clip()], root=tmp_path)

    (folder / "clip.mp4").write_bytes(b"re-recorded")
    with pytest.raises(ManifestError, match="needs its own consent entry"):
        check_against_set(manifest, [clip()], root=tmp_path)


def test_a_missing_clip_file_is_reported(tmp_path: Path) -> None:
    manifest = load_manifest(write(tmp_path, [entry()]))
    with pytest.raises(ManifestError, match="clip file is missing"):
        check_against_set(manifest, [clip()], root=tmp_path)


def test_file_sha256_reads_a_file_in_chunks(tmp_path: Path) -> None:
    path = tmp_path / "clip.bin"
    payload = b"x" * (3 * (1 << 20) + 7)
    path.write_bytes(payload)
    assert file_sha256(path) == hashlib.sha256(payload).hexdigest()


def test_unlabelled_lists_the_pairs_still_waiting_for_labels(tmp_path: Path) -> None:
    manifest = load_manifest(
        write(tmp_path, [entry(), entry(path="eval/clips/b/clip.mp4", rule_id="helmet_required")])
    )
    assert unlabelled(manifest, [("owner_fl_pos_1m", "forklift_2m")]) == ("b/helmet_required",)
    assert unlabelled(manifest, []) == ("b/helmet_required", "owner_fl_pos_1m/forklift_2m")


def test_a_manifest_needs_at_least_one_entry() -> None:
    with pytest.raises(ValueError, match="at least 1 item"):
        E1Manifest(name="empty", entries=())
