"""One request in, one stored extension version out: generate → prove → review → registry.

`ExtensionService.propose` turns an owner's request into a `CheckSpec`, asks the generator for a
`CheckSource`, proves it (`proof.prove`, the validator included), sends it to the reviewer only
if the proof passed, and stores the version as `proposed` (proof passed and review approved) or
`rejected` (anything else, with the reasons in the report). Nothing becomes runnable here: an
owner approves a proposed version through the registry, and a rule then refers to it as
`StateCheck(state="ext:<name>@<sha12>")`.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from rulecam.contracts import Clock
from rulecam.extensions.generate import CheckGenerator, CheckSpec, Relation
from rulecam.extensions.proof import ProofStep, Runner, context_for, prove
from rulecam.extensions.registry import (
    NAME_PATTERN,
    ExtensionRecord,
    ExtensionRegistry,
    code_sha256,
)
from rulecam.extensions.review import ExtensionReviewer, skipped
from rulecam.extensions.sample import Line
from rulecam.extensions.sandbox import run_check

_ABOVE: Final = re.compile(r"\b(above|over|higher than|taller than)\b", re.IGNORECASE)
_BELOW: Final = re.compile(r"\b(below|under|lower than|beneath)\b", re.IGNORECASE)
_WORD: Final = re.compile(r"[a-z0-9]+")
_STOP_WORDS: Final = frozenset({"a", "an", "the", "if", "me", "alert", "when", "is", "on", "of"})
_NAME_WORDS: Final = 5


class ExtensionRequest(BaseModel):
    """An owner's request for a new kind of check.

    Attributes:
        request: The owner's words, e.g. "alert if a pallet is stacked above the yellow line".
        concept: The rule concept whose objects the check sees, e.g. "pallet".
        camera_id: The camera the reference lines are drawn on.
        lines: Owner-drawn reference lines, `{name: [[x1, y1], [x2, y2]]}` in pixels.
        relation: Above or below the line; read from the request when not given.
        name: The extension's name; made from the request when not given.
        frame_w: The camera's frame width, when known.
        frame_h: The camera's frame height, when known.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    request: str = Field(min_length=3, max_length=500)
    concept: str = Field(min_length=1, max_length=80)
    camera_id: str = Field(min_length=1, max_length=80)
    lines: dict[str, Line] = Field(default_factory=dict, max_length=4)
    relation: Relation | None = None
    name: str | None = Field(default=None, pattern=NAME_PATTERN.pattern)
    frame_w: int | None = Field(default=None, gt=0)
    frame_h: int | None = Field(default=None, gt=0)


def infer_relation(request: str) -> Relation | None:
    """Above or below, when the request says exactly one of them."""
    above, below = bool(_ABOVE.search(request)), bool(_BELOW.search(request))
    if above == below:
        return None
    return "above" if above else "below"


def name_for(request: str) -> str:
    """A registry name from the request's first meaningful words, e.g. `pallet_stacked_above`."""
    words = [w for w in _WORD.findall(request.lower()) if w not in _STOP_WORDS]
    if not words:
        return "ext_check"
    name = "_".join(words[:_NAME_WORDS])
    if not name[0].isalpha():
        name = f"ext_{name}"
    name = name[:48].rstrip("_")
    return name if NAME_PATTERN.match(name) else "ext_check"


def spec_for(req: ExtensionRequest) -> CheckSpec:
    """The generator's view of a request."""
    return CheckSpec(
        request=req.request,
        concept=req.concept,
        lines=tuple(sorted(req.lines)),
        relation=req.relation or infer_relation(req.request),
    )


class ExtensionService:
    """Generates, proves, reviews and stores extension versions."""

    def __init__(
        self,
        generator: CheckGenerator,
        reviewer: ExtensionReviewer,
        registry: ExtensionRegistry,
        *,
        clock: Clock,
        runner: Runner = run_check,
        steps: Sequence[ProofStep] | None = None,
    ) -> None:
        """Create the service.

        Args:
            generator: Muse Code (or the Spark fallback).
            reviewer: Spark Open (or the Spark 1.3 fallback).
            registry: Where versions are stored.
            clock: Time source for `created_at`.
            runner: Runs code over samples (the sandbox).
            steps: Proof steps (`proof.DEFAULT_STEPS` when None); add-ons extend this.
        """
        self._generator = generator
        self._reviewer = reviewer
        self._registry = registry
        self._clock = clock
        self._runner = runner
        self._steps = steps

    @property
    def registry(self) -> ExtensionRegistry:
        """The registry versions are stored in."""
        return self._registry

    def propose(self, req: ExtensionRequest) -> ExtensionRecord:
        """Generate, prove and review a check for `req`, and store the version.

        Args:
            req: The owner's request.

        Returns:
            The stored version: `proposed` when it may be approved, else `rejected`.

        Raises:
            ClientConfigError: If no generator is available.
            ModelClientError: If generation fails.
            RegistryError: If the same code was already stored under this name.
        """
        spec = spec_for(req)
        source = self._generator.generate(spec)
        proof = prove(context_for(source.code, source.tests, spec, self._runner), self._steps)
        review = (
            self._reviewer.review(spec, source.code, proof)
            if proof.passed
            else skipped("not reviewed: the proof failed")
        )
        name = req.name or name_for(req.request)
        parent = self._registry.latest(name)
        record = ExtensionRecord(
            name=name,
            sha256=code_sha256(source.code),
            parent_sha=parent.sha256 if parent is not None else None,
            request=req.request,
            concept=req.concept,
            camera_id=req.camera_id,
            lines=req.lines,
            relation=spec.relation,
            frame_w=req.frame_w,
            frame_h=req.frame_h,
            code=source.code,
            tests=source.tests,
            generator=source.generator,
            model=source.model,
            proof=proof,
            review=review,
            status="proposed" if proof.passed and review.verdict == "approve" else "rejected",
            created_at=self._clock.now(),
        )
        return self._registry.add(record)
