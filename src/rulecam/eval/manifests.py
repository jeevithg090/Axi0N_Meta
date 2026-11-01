"""E1 clip manifests with a consent gate (task P4-06, MP §8 E1).

An E1 manifest is the list of clips a measured E1 run is allowed to use. It is deliberately
separate from `eval/clips/<name>/clip.yaml`, which says what a clip *contains*:

* a `clip.yaml` is the label (which rule fires, and when);
* a manifest entry is the **permission and the provenance**: who staged the clip, where the
  footage came from, and that a signed consent form exists for everyone in it.

Consent is a hard gate, not a warning. `load_manifest` raises `ConsentError` for an entry whose
`consent` is false or missing, so a clip without a signed form can never reach a measured run.
The forms themselves are never committed (`docs/consent.md`); the manifest records only the form
id, so an auditor can ask for that form by name.

A manifest entry pins the clip's `sha256`, and `check_against_set` refuses a manifest whose hash,
rule or label no longer matches the eval set (the clip's `clip.yaml`), so a relabelled or
re-recorded clip cannot be measured under an old permission.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Final, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from rulecam.eval.schema import ClipEntry

SHA256: Final = r"^[0-9a-f]{64}$"
SLUG: Final = r"^[a-z0-9][a-z0-9_-]*$"
READ_CHUNK: Final = 1 << 20

Label = Literal["positive", "negative"]
StagedBy = Literal["team", "outsider"]
UseCase = Literal["uc1", "uc2", "uc3", "uc4", "uc5"]


class ManifestError(ValueError):
    """A manifest could not be read, or does not match the eval set."""


class ConsentError(ManifestError):
    """A manifest entry has no signed consent form, so it cannot be measured."""


class ManifestEntry(BaseModel):
    """One clip a measured E1 run may use.

    Attributes:
        path: Clip path, relative to the repository root.
        sha256: Hash of the clip file, pinned so a re-recording cannot reuse the permission.
        use_case: Which use case the clip belongs to (`uc1` to `uc5`).
        rule_id: The rule measured on this clip.
        label: Whether the rule should fire (`positive`) or must not (`negative`).
        staged_by: `team` for a clip we staged, `outsider` for one staged by someone who has
            not seen the rules. Outsider clips are the only honest generalisation evidence,
            so the report counts them separately.
        provenance: Where the footage came from, e.g. `recorded`, `public_dataset`.
        consent: Whether a signed consent form exists for everyone who appears in the clip.
        consent_form: The form's id, as filed outside the repository. Required when `consent`.
        notes: Anything a reader of the results needs to know about the clip.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(min_length=1)
    sha256: str = Field(pattern=SHA256)
    use_case: UseCase
    rule_id: str = Field(pattern=SLUG)
    label: Label
    staged_by: StagedBy
    provenance: str = Field(min_length=1)
    consent: bool = False
    consent_form: str = ""
    notes: str = ""

    @model_validator(mode="after")
    def _consent_names_its_form(self) -> Self:
        if self.consent and not self.consent_form.strip():
            msg = f"{self.path}: consent is claimed but no consent_form id is given"
            raise ValueError(msg)
        return self

    @property
    def clip_name(self) -> str:
        """The eval-set clip folder this entry refers to, e.g. `owner_fl_pos_1m`."""
        return Path(self.path).parent.name


class E1Manifest(BaseModel):
    """Every clip a measured E1 run may use, with the window it was agreed for."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    description: str = ""
    entries: tuple[ManifestEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _no_duplicate_pairs(self) -> Self:
        seen = [(e.path, e.rule_id) for e in self.entries]
        if len(seen) != len(set(seen)):
            msg = "a manifest lists the same (path, rule_id) twice"
            raise ValueError(msg)
        return self

    def for_rule(self, rule_id: str) -> tuple[ManifestEntry, ...]:
        """Return the entries measuring one rule."""
        return tuple(e for e in self.entries if e.rule_id == rule_id)

    def use_cases(self) -> tuple[UseCase, ...]:
        """Return the use cases this manifest covers, in order."""
        return tuple(sorted({e.use_case for e in self.entries}))


def file_sha256(path: Path) -> str:
    """Return the hex sha256 of a file, read in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(READ_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(path: Path) -> E1Manifest:
    """Read a manifest and refuse every entry that has no consent.

    Args:
        path: The manifest YAML file.

    Returns:
        The manifest, with every entry consented.

    Raises:
        ManifestError: If the file is missing, is not a mapping, or fails validation.
        ConsentError: If any entry's `consent` is false or missing. The message names every
            such clip, so one run of the command lists everything that needs a form.
    """
    if not path.is_file():
        msg = f"no E1 manifest at {path}"
        raise ManifestError(msg)
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        msg = f"{path}: a manifest must be a mapping with `name` and `entries`"
        raise ManifestError(msg)
    try:
        manifest = E1Manifest.model_validate(loaded)
    except ValueError as exc:  # pydantic ValidationError is a ValueError
        msg = f"{path}: {exc}"
        raise ManifestError(msg) from exc
    refused = [e.path for e in manifest.entries if not e.consent]
    if refused:
        listed = ", ".join(sorted(refused))
        msg = (
            f"{path}: {len(refused)} clip(s) have no signed consent form and cannot be "
            f"measured: {listed}. Collect the form (docs/consent.md), file it outside the "
            "repository, then set `consent: true` with its `consent_form` id."
        )
        raise ConsentError(msg)
    return manifest


def _label_mismatch(entry: ManifestEntry, clip: ClipEntry) -> str | None:
    """Say how an entry disagrees with its clip's `clip.yaml`, or return `None` if it agrees.

    A rule still `unlabelled` in `clip.yaml` agrees with either label: labelling it is the
    work `rulecam eval label` lists.
    """
    rule = next((r for r in clip.rules if r.id == entry.rule_id), None)
    if rule is None:
        return f"{entry.path}: clip.yaml for {clip.name} no longer lists rule {entry.rule_id!r}"
    expected = {"labelled": "positive", "negative": "negative"}.get(rule.status)
    if expected is not None and expected != entry.label:
        return (
            f"{entry.path}: the manifest says {entry.rule_id} is {entry.label}, but clip.yaml "
            f"labels it {rule.status}. A relabelled clip needs its manifest entry reviewed."
        )
    return None


def check_against_set(
    manifest: E1Manifest, clips: Iterable[ClipEntry], *, root: Path | None = None
) -> None:
    """Check that a manifest still matches the eval set and the files on disk.

    Args:
        manifest: The loaded manifest.
        clips: The clips of the eval set (`EvalSet.clips`).
        root: Repository root for resolving `path`. When given, every entry's file must exist
            and hash to its pinned `sha256`; when `None`, the files are not read.

    Raises:
        ManifestError: If an entry names a clip that is not in the eval set, a rule its
            `clip.yaml` no longer lists, or a label that contradicts the `clip.yaml` label, or
            (with `root`) if its file is missing or its hash has changed.
    """
    by_name = {clip.name: clip for clip in clips}
    unknown = sorted({e.clip_name for e in manifest.entries if e.clip_name not in by_name})
    if unknown:
        msg = f"manifest names clips that are not in the eval set: {', '.join(unknown)}"
        raise ManifestError(msg)
    for entry in manifest.entries:
        mismatch = _label_mismatch(entry, by_name[entry.clip_name])
        if mismatch is not None:
            raise ManifestError(mismatch)
    if root is None:
        return
    for entry in manifest.entries:
        file = root / entry.path
        if not file.is_file():
            msg = f"{entry.path}: the clip file is missing, so its hash cannot be checked"
            raise ManifestError(msg)
        actual = file_sha256(file)
        if actual != entry.sha256:
            msg = (
                f"{entry.path}: sha256 is {actual}, the manifest pins {entry.sha256}. "
                "A re-recorded clip needs its own consent entry."
            )
            raise ManifestError(msg)


def unlabelled(manifest: E1Manifest, labelled: Sequence[tuple[str, str]]) -> tuple[str, ...]:
    """Return `clip/rule` pairs in the manifest that the eval set has not labelled yet.

    Args:
        manifest: The loaded manifest.
        labelled: `(clip_name, rule_id)` pairs whose labels are ready.

    Returns:
        The missing pairs as `clip/rule` strings, sorted, for the labelling CLI to list.
    """
    ready = set(labelled)
    return tuple(
        sorted(
            f"{e.clip_name}/{e.rule_id}"
            for e in manifest.entries
            if (e.clip_name, e.rule_id) not in ready
        )
    )
