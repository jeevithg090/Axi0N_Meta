"""Chain Mode data models: rule packs, store setups, fits, proofs, scorecards and roll-ups.

These models belong to Chain Mode and are not shared contracts: they are built from the
contracts (`RuleIR`, `PrivacyMode`, `Severity`, `Provenance`) and the planner's `SiteConfig`,
and nothing outside `rulecam.chain` depends on them.

Guardrail (rule R13): every result here is per store and per rule. No model carries a track,
a person or a worker; the scorecard is built from `RuleOutcome`s, which have none.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Final, Literal

from pydantic import Field, model_validator

from rulecam.contracts.core import Frozen, PrivacyMode, Provenance, Severity
from rulecam.contracts.rule_ir import RuleIR
from rulecam.perception.coverage import CoverageSegment, RuleOutcome
from rulecam.planner.site_config import NAME_PATTERN, SiteConfig

PRIVACY_ORDER: Final = {PrivacyMode.STANDARD: 0, PrivacyMode.ANONYMOUS: 1, PrivacyMode.STRICT: 2}
"""Privacy modes from least to most private; a store may never sit below its pack's floor."""

SEVERITY_ORDER: Final = {Severity.LOW: 0, Severity.MEDIUM: 1, Severity.HIGH: 2}
"""Severities from lowest to highest; a store may raise a rule's severity, never lower it."""

KnobPath = tuple[str | int, ...]
"""Keys from a rule's predicate down to one number, e.g. `("requirement", "total_s_gte")`."""


# --- the pack HQ writes ------------------------------------------------------------------------


class Slot(Frozen):
    """Something a template needs from a store, in words; each store maps it to its own.

    Attributes:
        kind: `polygon` (one of the store's drawn zones), `buffer` (metres around a concept,
            on one of the store's calibrated cameras) or `camera` (a camera the rule runs on,
            for rules with no zone, such as a distance between two moving things).
        describe: What HQ means, for the store manager, e.g. "where cooks start on the line".
        buffer_of: For a buffer slot, the concept it surrounds.
        buffer_m: For a buffer slot, its radius in metres.
    """

    kind: Literal["polygon", "buffer", "camera"]
    describe: str = Field(min_length=1, max_length=200)
    buffer_of: str | None = None
    buffer_m: float | None = Field(default=None, gt=0, le=20)

    @model_validator(mode="after")
    def _buffer_fields(self) -> Slot:
        is_buffer = self.kind == "buffer"
        if is_buffer != (self.buffer_of is not None) or is_buffer != (self.buffer_m is not None):
            msg = "a buffer slot needs buffer_of and buffer_m; other slots have neither"
            raise ValueError(msg)
        return self


class Knob(Frozen):
    """A threshold stores may tune, inside the range HQ proved.

    Attributes:
        name: The knob's name, e.g. `wash_seconds`.
        path: Where the number sits in the rule's predicate.
        low: The smallest value a store may set.
        high: The largest value a store may set.
        stricter: Which way is safer: `higher` (a longer wash) or `lower` (a shorter wait).
        unit: For people, e.g. `s` or `m`.
    """

    name: str = Field(pattern=NAME_PATTERN)
    path: KnobPath = Field(min_length=1)
    low: float
    high: float
    stricter: Literal["higher", "lower"]
    unit: str = ""

    @model_validator(mode="after")
    def _ordered(self) -> Knob:
        if self.low > self.high:
            msg = f"knob {self.name!r}: low {self.low:g} is above high {self.high:g}"
            raise ValueError(msg)
        return self


class RuleTemplate(Frozen):
    """One rule of a pack: a Rule IR whose zones are slots, and the knobs stores may tune.

    Attributes:
        concern: The worry in plain words; the planner guardrails check it.
        rule: The rule; its `zones` stay empty because each store binds them from `slots`.
        slots: Slot name -> what the slot is. Predicates name polygon and buffer slots as
            their zones.
        knobs: Thresholds stores may tune.
    """

    concern: str = Field(min_length=1, max_length=400)
    rule: RuleIR
    slots: dict[str, Slot] = Field(default_factory=dict)
    knobs: tuple[Knob, ...] = ()

    @property
    def rule_id(self) -> str:
        """The rule's id, the same in every store."""
        return self.rule.id


class RulePack(Frozen):
    """A versioned set of rule templates that head office writes once for every store.

    Attributes:
        pack_id: The pack's id, e.g. `kitchen-basics`.
        version: Bumped on every change; stores run one version at a time.
        title: For people.
        publisher: Who wrote it, e.g. "Head office food safety".
        vertical: e.g. `kitchen`, `warehouse`.
        privacy_floor: The least private mode a store may run the pack in.
        templates: The rules.
    """

    pack_id: str = Field(pattern=NAME_PATTERN)
    version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=120)
    publisher: str = Field(min_length=1, max_length=120)
    vertical: str = "general"
    privacy_floor: PrivacyMode = PrivacyMode.STANDARD
    templates: tuple[RuleTemplate, ...] = Field(min_length=1)

    def digest(self) -> str:
        """SHA-256 of the pack's canonical JSON: what every store's fit names."""
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --- the store's side ------------------------------------------------------------------------


class StoreSetup(Frozen):
    """One store: its site configuration and how it maps the pack onto its cameras.

    Attributes:
        store_id: Short id, e.g. `store-14`.
        name: For people, e.g. "Main St".
        region: For grouping on the HQ view, e.g. `north`.
        site: The store's own site configuration (`docs/site.example.yaml` format): privacy
            mode, roles, cameras, zones and calibrations.
        frames: Camera id -> frame size (width, height) in pixels, to check zones are in view.
        bindings: Template id -> slot -> the store zone (polygon slot) or camera (buffer and
            camera slots). Unbound slots bind themselves where the answer is clear (see
            `rulecam.chain.fit.auto_binding`).
        knobs: Template id -> knob -> the value the store wants.
        severity: Template id -> the severity the store wants (only raising is accepted).
    """

    store_id: str = Field(pattern=NAME_PATTERN)
    name: str = Field(min_length=1, max_length=120)
    region: str = ""
    site: SiteConfig = Field(default_factory=SiteConfig)
    frames: dict[str, tuple[int, int]] = Field(default_factory=dict)
    bindings: dict[str, dict[str, str]] = Field(default_factory=dict)
    knobs: dict[str, dict[str, float]] = Field(default_factory=dict)
    severity: dict[str, Severity] = Field(default_factory=dict)

    @property
    def cameras(self) -> tuple[str, ...]:
        """Every camera the store has: configured or with a known frame size."""
        return tuple(sorted({*self.site.cameras, *self.frames}))


# --- proving the pack at HQ ------------------------------------------------------------------

PackFindingCode = Literal[
    "duplicate_rule",
    "declined_concern",
    "private_space_slot",
    "template_zones",
    "undefined_slot",
    "unused_slot",
    "buffer_concept",
    "bad_knob",
    "privacy_inconsistency",
    "missing_action",
]


class PackFinding(Frozen):
    """One problem that stops a pack being released.

    Attributes:
        code: The problem class.
        rule_id: The template it is about.
        message: What is wrong, for HQ.
    """

    code: PackFindingCode
    rule_id: str
    message: str


class SettingProof(Frozen):
    """Rule CI on a template's reference fit at one setting of its knobs.

    Attributes:
        label: `default`, or the knob changed, e.g. `wash_seconds=60`.
        knobs: Every knob's value at this setting.
        ci_green: Whether Rule CI passed.
        summary: Rule CI's summary, e.g. `boundary 16/16, mutations 5/5`.
        failures: Rule CI's failures, readable.
    """

    label: str
    knobs: dict[str, float] = Field(default_factory=dict)
    ci_green: bool
    summary: str
    failures: tuple[str, ...] = ()


class TemplateProof(Frozen):
    """Rule CI on one template at its default and at both ends of every knob's range.

    Attributes:
        rule_id: The template.
        settings: One proof per setting.
    """

    rule_id: str
    settings: tuple[SettingProof, ...] = ()

    @property
    def passed(self) -> bool:
        """Green at every setting a store is allowed to pick."""
        return bool(self.settings) and all(s.ci_green for s in self.settings)


class PackProof(Frozen):
    """Whether a pack may be released: lint clean and Rule CI green across every knob's range.

    Attributes:
        pack_id: The pack.
        version: Its version.
        digest: Its SHA-256.
        findings: Lint findings.
        templates: Rule CI per template (templates with findings are not run).
    """

    pack_id: str
    version: int
    digest: str
    findings: tuple[PackFinding, ...] = ()
    templates: tuple[TemplateProof, ...] = ()

    @property
    def releasable(self) -> bool:
        """No findings, and every template is green at every allowed setting."""
        return not self.findings and bool(self.templates) and all(t.passed for t in self.templates)


# --- fitting the pack to a store ---------------------------------------------------------------

FitStatus = Literal["ready", "needs_setup", "blocked", "failed"]
"""`ready`: runs. `needs_setup`: a zone, camera, calibration or role is missing. `blocked`: a
guardrail stops it (privacy floor, private space). `failed`: lint or Rule CI is red."""


class FittedRule(Frozen):
    """One template fitted to one store.

    Attributes:
        rule_id: The template's rule id.
        status: See `FitStatus`.
        says: One line for the store manager.
        problems: Why it is not ready.
        warnings: Store overrides HQ did not allow (the pack's value is used instead).
        knobs: The knob values in use.
        severity: The severity in use.
        cameras: The store cameras the rule watches.
        ci_summary: Rule CI's summary on the store's own zones, when it ran.
        rule: The store's concrete rule, when it could be built.
    """

    rule_id: str
    status: FitStatus
    says: str
    problems: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    knobs: dict[str, float] = Field(default_factory=dict)
    severity: Severity
    cameras: tuple[str, ...] = ()
    ci_summary: str = ""
    rule: RuleIR | None = None


class StoreFit(Frozen):
    """A pack version fitted to one store.

    Attributes:
        store_id: The store.
        store_name: For people.
        region: For grouping.
        pack_id: The pack.
        pack_version: Its version.
        pack_digest: Its SHA-256, so HQ knows exactly what the store runs.
        rules: One fit per template, in pack order.
        warnings: Store settings for rules the pack does not have.
    """

    store_id: str
    store_name: str
    region: str = ""
    pack_id: str
    pack_version: int
    pack_digest: str
    rules: tuple[FittedRule, ...]
    warnings: tuple[str, ...] = ()

    @property
    def ready_count(self) -> int:
        """How many rules run at this store."""
        return sum(r.status == "ready" for r in self.rules)

    def rule(self, rule_id: str) -> FittedRule | None:
        """The fit of one rule, or None when the pack has no such rule."""
        return next((r for r in self.rules if r.rule_id == rule_id), None)


# --- what stores report back, and the HQ scorecard -----------------------------------------------


class StoreObservations(Frozen):
    """What one store's runtime reports for the window: outcomes and camera coverage.

    Attributes:
        outcomes: Rule outcomes (each with rule, camera and time span; never a person).
        coverage: Camera id -> its watched-time segments (`CoverageWatchdog.segments`). Time
            with no segment counts as not watched.
    """

    outcomes: tuple[RuleOutcome, ...] = ()
    coverage: dict[str, tuple[CoverageSegment, ...]] = Field(default_factory=dict)


class ChainObservations(Frozen):
    """Every store's observations over one window, all of one provenance.

    Attributes:
        provenance: Where the numbers came from; never mixed in one scorecard (SA §9).
        start: Window start.
        end: Window end.
        stores: Store id -> its observations.
    """

    provenance: Provenance
    start: datetime
    end: datetime
    stores: dict[str, StoreObservations] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _ordered(self) -> ChainObservations:
        if self.end <= self.start:
            msg = "the observation window must end after it starts"
            raise ValueError(msg)
        return self


ScoreStatus = Literal["ok", "below_chain", "camera_blind", "too_few", "not_ready"]
"""`ok`; `below_chain`: the store's 95% interval sits below the chain's rate; `camera_blind`:
a camera the rule needs was BLIND too long to trust the numbers; `too_few`: fewer watched
outcomes than the small-crew floor, so no rate is shown; `not_ready`: the rule does not run."""


class RuleScore(Frozen):
    """One rule at one store over the window (store level only).

    Attributes:
        store_id: The store.
        rule_id: The rule.
        status: See `ScoreStatus`.
        compliant: Watched compliant outcomes.
        violations: Watched violations.
        blind: Outcomes that overlapped BLIND time (counted in neither).
        rate: Compliant share of watched outcomes; None below the floor or with none.
        lo: Wilson 95% lower bound of `rate`.
        hi: Wilson 95% upper bound of `rate`.
        watched_share: The worst-watched camera's watched share of the window.
        blind_camera: That camera.
        blind_reason: Its main BLIND reason (`moved`, `dark`, ... or `no_video`).
    """

    store_id: str
    rule_id: str
    status: ScoreStatus
    compliant: int = 0
    violations: int = 0
    blind: int = 0
    rate: float | None = None
    lo: float | None = None
    hi: float | None = None
    watched_share: float | None = None
    blind_camera: str | None = None
    blind_reason: str | None = None


class FixItem(Frozen):
    """One thing HQ should get fixed, most urgent first.

    Attributes:
        store_id: The store.
        rule_id: The rule.
        kind: `setup` (the rule does not run), `camera` (a camera is BLIND) or `compliance`
            (the store is below the chain).
        severity: The rule's severity.
        says: One line for HQ, in plain words.
    """

    store_id: str
    rule_id: str
    kind: Literal["setup", "camera", "compliance"]
    severity: Severity
    says: str


class ChainScorecard(Frozen):
    """The HQ view: every store, every rule, and the fix-first list.

    Attributes:
        pack_id: The pack.
        pack_version: Its version.
        provenance: Where the numbers came from.
        start: Window start.
        end: Window end.
        chain_rates: Rule id -> pooled compliant share across stores (None with no data).
        scores: One score per store and rule.
        fix_first: What to fix, most urgent first.
    """

    pack_id: str
    pack_version: int
    provenance: Provenance
    start: datetime
    end: datetime
    chain_rates: dict[str, float | None] = Field(default_factory=dict)
    scores: tuple[RuleScore, ...] = ()
    fix_first: tuple[FixItem, ...] = ()


# --- evidence: one root for the whole chain ------------------------------------------------------


class StoreRoot(Frozen):
    """One store's daily Merkle root (a line of `evidence/roots/<day>.txt`).

    Attributes:
        store_id: The store (the chain's site name).
        root: The hex Merkle root of the store's chain that day.
        n_records: Records in that chain.
    """

    store_id: str
    root: str = Field(pattern=r"^[0-9a-f]{64}$")
    n_records: int = Field(ge=0)


class ChainRoot(Frozen):
    """The HQ root for one day: a Merkle root over every store's root.

    Attributes:
        day: The UTC day.
        root: The hex root.
        stores: The store roots, in store id order (the leaves' order).
    """

    day: date
    root: str
    stores: tuple[StoreRoot, ...]


class ProofStep(Frozen):
    """One step of an inclusion proof: the sibling hash and which side it sits on."""

    sibling: str
    side: Literal["left", "right"]


class InclusionProof(Frozen):
    """Proof that one store's root is inside the HQ root, checkable with nothing else.

    Attributes:
        day: The UTC day.
        store: The store root being proven.
        leaf: `sha256("<store> <root> <n>")`.
        steps: Siblings from the leaf up to the root.
        root: The HQ root.
    """

    day: date
    store: StoreRoot
    leaf: str
    steps: tuple[ProofStep, ...] = ()
    root: str


class ChainReport(Frozen):
    """Everything the HQ view shows: the pack's proof, every store's fit and the scorecard.

    Attributes:
        proof: The pack's proof.
        fits: One fit per store.
        scorecard: The scorecard, when there were observations.
    """

    proof: PackProof
    fits: tuple[StoreFit, ...]
    scorecard: ChainScorecard | None = None
