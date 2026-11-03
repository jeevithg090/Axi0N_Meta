"""Extension versions, their approval, and the hook that runs approved ones in the engine.

Every generated check is stored as one version, keyed by `(name, sha256 of its code)`, in
`<root>/<name>/<sha256>.json`, with `parent_sha` pointing at the version it replaces (for
versioning and rollback). Statuses: `rejected` (failed its proof or its review), `proposed`
(passed both; waiting for the owner), `approved` (the owner approved it; runnable) and `retired`.
Every status change appends a `rule_change` record to the evidence chain (`change:
extension_proposed | extension_rejected | extension_approved | extension_retired`), carrying the
code's SHA-256 so the record binds the exact code.

`RegistryHook` is the engine's `ExtensionHook`: for `StateCheck(state="ext:<name>@<sha12>")` it
runs only an `approved` version whose SHA-256 starts with `<sha12>` *and* whose stored code still
hashes to it (a file edited on disk is refused). Anything else, or a sandbox failure, gives the
engine no answer, which it treats as unknown, never compliant.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Final, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from rulecam.contracts import Clock
from rulecam.contracts.rule_ir import StateCheck
from rulecam.evidence.chain import EventChain
from rulecam.extensions.errors import ExtensionError, RegistryError
from rulecam.extensions.generate import Generator, Relation
from rulecam.extensions.proof import ProofReport
from rulecam.extensions.review import ReviewResult
from rulecam.extensions.sample import Line, samples_from_scene
from rulecam.extensions.sandbox import SandboxLimits, run_check
from rulecam.log import get_logger
from rulecam.replay.cache import atomic_write_text
from rulecam.rules.extension import ExtensionValues, extension_ref, parse_extension_ref
from rulecam.rules.scene import Scene

_log = get_logger(__name__)

NAME_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]{2,47}$")
MIN_SHA_CHARS: Final = 12
Status = Literal["proposed", "rejected", "approved", "retired"]


def code_sha256(code: str) -> str:
    """SHA-256 of the code's UTF-8 bytes, hex."""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


class ExtensionRecord(BaseModel):
    """One version of an extension.

    Attributes:
        name: The extension's name (`NAME_PATTERN`).
        sha256: SHA-256 of `code`; with `name`, the version's key.
        parent_sha: The version this one replaces, or None for the first.
        request: The owner's words.
        concept: The rule concept whose objects it sees.
        camera_id: The camera its lines are drawn on.
        lines: Owner-drawn reference lines on that camera, in pixels.
        relation: Above or below the line, when the request says so.
        frame_w: The camera's frame width, when known.
        frame_h: The camera's frame height, when known.
        code: The check's source.
        tests: The generator's test file (data only).
        generator: `muse-code` or `spark-fallback`.
        model: The generator's model.
        proof: The proof report.
        review: The second model's review.
        status: Where it stands.
        created_at: When it was proposed.
        decided_by: Who approved or retired it.
        decided_at: When.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=NAME_PATTERN.pattern)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_sha: str | None = None
    request: str
    concept: str
    camera_id: str
    lines: dict[str, Line] = Field(default_factory=dict)
    relation: Relation | None = None
    frame_w: int | None = None
    frame_h: int | None = None
    code: str
    tests: str
    generator: Generator
    model: str
    proof: ProofReport
    review: ReviewResult
    status: Status
    created_at: datetime
    decided_by: str | None = None
    decided_at: datetime | None = None

    @property
    def ref(self) -> str:
        """The `StateCheck.state` that runs this version: `ext:<name>@<sha12>`."""
        return extension_ref(self.name, self.sha256)

    @property
    def approvable(self) -> bool:
        """Proposed, its proof passed and its review approved."""
        return self.status == "proposed" and self.proof.passed and self.review.verdict == "approve"


class ExtensionRegistry:
    """Stores extension versions as JSON files and records every status change in the chain."""

    def __init__(self, root: Path, *, clock: Clock, chain: EventChain | None = None) -> None:
        """Create the registry.

        Args:
            root: Directory of versions (`<data>/extensions`).
            clock: Time source for decisions.
            chain: The evidence chain for `rule_change` records; None records nothing.
        """
        self._root = root
        self._clock = clock
        self._chain = chain

    def add(self, record: ExtensionRecord) -> ExtensionRecord:
        """Store a new version (proposed or rejected) and record it.

        Raises:
            RegistryError: If the SHA does not match the code, the status is not proposed or
                rejected, or this version already exists.
        """
        if record.sha256 != code_sha256(record.code):
            msg = "the record's sha256 does not match its code"
            raise RegistryError(msg)
        if record.status not in {"proposed", "rejected"}:
            msg = f"a new version is proposed or rejected, not {record.status}"
            raise RegistryError(msg)
        if self._path(record.name, record.sha256).exists():
            msg = f"version {record.ref} already exists"
            raise RegistryError(msg)
        self._write(record)
        self._record(record, f"extension_{record.status}", by="system")
        return record

    def versions(self, name: str) -> list[ExtensionRecord]:
        """Every stored version of `name`, oldest first."""
        folder = self._root / name
        if not NAME_PATTERN.match(name) or not folder.is_dir():
            return []
        found = [r for p in sorted(folder.glob("*.json")) if (r := self._read(p)) is not None]
        return sorted(found, key=lambda r: (r.created_at, r.sha256))

    def latest(self, name: str) -> ExtensionRecord | None:
        """The newest version of `name`, if any."""
        found = self.versions(name)
        return found[-1] if found else None

    def all_latest(self) -> list[ExtensionRecord]:
        """The newest version of every extension, by name."""
        if not self._root.is_dir():
            return []
        names = sorted(p.name for p in self._root.iterdir() if p.is_dir())
        return [r for name in names if (r := self.latest(name)) is not None]

    def get(self, name: str, sha: str) -> ExtensionRecord:
        """The version of `name` whose SHA-256 starts with `sha` (at least 12 hex characters).

        Raises:
            RegistryError: If none or more than one version matches.
        """
        if len(sha) < MIN_SHA_CHARS:
            msg = f"a version is named by at least {MIN_SHA_CHARS} hex characters of its sha256"
            raise RegistryError(msg)
        matches = [r for r in self.versions(name) if r.sha256.startswith(sha)]
        if len(matches) != 1:
            msg = f"no single version of {name!r} matches {sha!r}"
            raise RegistryError(msg)
        return matches[0]

    def approve(self, name: str, sha: str, *, by: str) -> ExtensionRecord:
        """Approve a proposed version that passed its proof and review.

        Raises:
            RegistryError: If it is unknown or not approvable.
        """
        record = self.get(name, sha)
        if not record.approvable:
            msg = (
                f"{record.ref} cannot be approved: status {record.status}, proof "
                f"{'passed' if record.proof.passed else 'failed'}, review {record.review.verdict}"
            )
            raise RegistryError(msg)
        return self._decide(record, "approved", by)

    def retire(self, name: str, sha: str, *, by: str) -> ExtensionRecord:
        """Retire a proposed or approved version; it never runs again.

        Raises:
            RegistryError: If it is unknown, rejected or already retired.
        """
        record = self.get(name, sha)
        if record.status not in {"proposed", "approved"}:
            msg = f"{record.ref} is {record.status}; only proposed or approved can be retired"
            raise RegistryError(msg)
        return self._decide(record, "retired", by)

    def runnable(self, name: str, sha12: str) -> ExtensionRecord | None:
        """The approved version `ext:<name>@<sha12>` names, if its code is unchanged on disk."""
        try:
            record = self.get(name, sha12)
        except RegistryError:
            return None
        if record.status != "approved" or code_sha256(record.code) != record.sha256:
            return None
        return record

    def _decide(self, record: ExtensionRecord, status: Status, by: str) -> ExtensionRecord:
        decided = record.model_copy(
            update={"status": status, "decided_by": by, "decided_at": self._clock.now()}
        )
        self._write(decided)
        self._record(decided, f"extension_{status}", by=by)
        return decided

    def _path(self, name: str, sha256: str) -> Path:
        return self._root / name / f"{sha256}.json"

    def _write(self, record: ExtensionRecord) -> None:
        path = self._path(record.name, record.sha256)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, record.model_dump_json(indent=2))

    def _read(self, path: Path) -> ExtensionRecord | None:
        try:
            return ExtensionRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            _log.warning("extension_unreadable", path=str(path), error=repr(exc)[:200])
            return None

    def _record(self, record: ExtensionRecord, change: str, *, by: str) -> None:
        if self._chain is None:
            return
        mutation = next((s for s in record.proof.steps if s.name == "mutation"), None)
        self._chain.append(
            "rule_change",
            {
                "change": change,
                "extension": record.name,
                "ref": record.ref,
                "sha256": record.sha256,
                "parent_sha": record.parent_sha,
                "concept": record.concept,
                "camera_id": record.camera_id,
                "generator": record.generator,
                "model": record.model,
                "proof_passed": record.proof.passed,
                "mutation_score": mutation.metrics.get("score") if mutation else None,
                "review": record.review.verdict,
                "reviewer": record.review.reviewer,
                "by": by,
            },
        )


class RegistryHook:
    """The engine's `ExtensionHook`: runs approved versions from a registry in the sandbox."""

    def __init__(self, registry: ExtensionRegistry, limits: SandboxLimits | None = None) -> None:
        """Create the hook.

        Args:
            registry: Where approved versions are found.
            limits: Sandbox limits for each window's batch.
        """
        self._registry = registry
        self._limits = limits

    def evaluate(self, node: StateCheck, scene: Scene) -> ExtensionValues | None:
        """See `rules.extension.ExtensionHook.evaluate`."""
        parsed = parse_extension_ref(node.state)
        record = self._registry.runnable(*parsed) if parsed is not None else None
        if record is None:
            _log.warning("extension_not_runnable", state=node.state)
            return None
        if record.concept != node.object or (record.lines and record.camera_id != scene.camera_id):
            return None
        frame = (record.frame_w, record.frame_h) if record.frame_w and record.frame_h else None
        samples = samples_from_scene(scene, node.object, record.lines, frame)
        try:
            outcomes = run_check(record.code, [s.as_json() for s in samples], limits=self._limits)
        except ExtensionError as exc:
            _log.warning("extension_run_failed", ref=record.ref, error=str(exc)[:200])
            return None
        truth = np.array([o.result is not None and o.result.value for o in outcomes], dtype=bool)
        known = np.array([o.result is not None for o in outcomes], dtype=bool)
        return ExtensionValues(truth=truth, known=known)
