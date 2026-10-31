"""Turning an approved `SitePlan` into the runtime's `ActivePlan` (task P3-08).

`gate_rules` reads each rule's acceptance trace (P2-06c, with `ci_green` and `nonvacuous` from
Rule CI, P2-08) and sorts the rule into one of three states:

- `pass`: every check on the rule passed, including both Rule CI checks;
- `amber`: Rule CI passed and every failing check is one a re-compile cannot fix and the
  planner kept the rule for (`detect_lb>=0.8`, `calibration_err<=tol`, `citation_text_match`);
- `fail`: anything else: no trace for the rule, Rule CI missing or failing, or a blocking check
  (or a check this gate does not know) failing.

Only `pass` and `amber` rules are activated. `active_plan_from` builds the `ActivePlan` from them:
each rule's `ZoneSpec`s become contract `Zone`s keyed by name (a name two rules declare
differently is left out, so each rule falls back to its own spec), the plan's privacy mode is
carried over, and the ground calibration is taken from what the caller supplies (a `SitePlan`
holds none). The `plan_version_hash` is the SHA-256 of the canonical JSON of what is enforced.

**Camera scope.** Each activated rule runs only on the cameras the planner found it can be
checked on (`rule_cameras`): the cameras of its `observable`/`caveat` feasibility evidence, plus
the cameras of its zones and of the calibration it names. The verdict of a rule is found by its
source text (the concern it was compiled from), else by its concepts: the verdict whose probed
concepts are exactly the rule's (dynamic concepts included, nothing foreign). A rule with no such
evidence is left out of `ActivePlan.rule_cameras` and runs on every camera, as before (logged
`active_rule_unscoped`). `ActivePlan` is the runtime's own model, not a contract.

`PlanActivator` does the approval side effects: persist the activation (atomic write), hand the
plan to the runtime, and append the approval to the evidence chain. `restore_activation` reloads
the persisted plan at start-up so a restart keeps enforcing.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Final, Literal

from pydantic import ValidationError

from rulecam.contracts import Calibration, FeasibilityVerdict, Frozen, RuleIR, SitePlan, Zone
from rulecam.contracts.rule_ir import ZoneSpec
from rulecam.errors import RulecamError
from rulecam.evidence.chain import EventChain, canonical_json
from rulecam.log import get_logger
from rulecam.replay.cache import atomic_write_text
from rulecam.runtime.loop import ActivePlan, RuntimeLoop

_log = get_logger(__name__)

RULE_CI_CHECKS: Final = ("ci_green", "nonvacuous")
AMBER_CHECKS: Final = frozenset({"detect_lb>=0.8", "calibration_err<=tol", "citation_text_match"})
"""Failing checks a rule may still be activated with (the planner keeps it with an amber verdict).
Every other failing check blocks activation."""

SCOPING_VERDICTS: Final = frozenset({"observable", "caveat"})
"""Feasibility verdicts whose evidence cameras a rule is activated on."""

GateStatus = Literal["pass", "amber", "fail"]
CalibrationLookup = Callable[[SitePlan], Mapping[str, Calibration]]


class RuleGate(Frozen):
    """Whether one rule of a plan may be activated, and why not.

    Attributes:
        rule_id: The rule.
        status: `pass`, `amber` (activated, with caveats) or `fail` (not activated).
        reasons: The failing or missing checks (empty when `pass`).
    """

    rule_id: str
    status: GateStatus
    reasons: tuple[str, ...] = ()

    @property
    def activatable(self) -> bool:
        """Whether the rule may be enforced."""
        return self.status != "fail"


class Activation(Frozen):
    """What an approval put into force; persisted so a restart keeps enforcing it.

    Attributes:
        plan_id: The approved plan.
        version: Its version.
        gates: The gate result for every rule of the plan.
        active_plan: What the runtime enforces.
    """

    plan_id: str
    version: int
    gates: tuple[RuleGate, ...]
    active_plan: ActivePlan

    @property
    def rule_ids(self) -> list[str]:
        """The activated rules."""
        return [rule.id for rule in self.active_plan.rules]


class PlanActivationError(RulecamError):
    """Base class for approval failures."""


class NothingActivatableError(PlanActivationError):
    """No rule of the plan passes the activation gate."""

    def __init__(self, plan_id: str, gates: Sequence[RuleGate]) -> None:
        """Record the plan and every rule's gate result."""
        self.plan_id = plan_id
        self.gates = tuple(gates)
        super().__init__(refusal_detail(plan_id, gates))


class RuntimeNotRunningError(PlanActivationError):
    """There is no runtime loop to enforce the plan."""


def refusal_detail(plan_id: str, gates: Sequence[RuleGate]) -> str:
    """Explain why no rule of `plan_id` can be activated, one clause per rule."""
    if not gates:
        return f"plan {plan_id!r} has no rules to activate"
    reasons = "; ".join(f"{g.rule_id}: {', '.join(g.reasons)}" for g in gates)
    return f"no rule of plan {plan_id!r} passes the activation gate ({reasons})"


def gate_rule(plan: SitePlan, rule: RuleIR) -> RuleGate:
    """Gate one rule on the plan's acceptance trace.

    Args:
        plan: The plan holding the trace.
        rule: One of its rules.

    Returns:
        The rule's gate result.
    """
    checks = [c for c in plan.acceptance.checks if c.item == rule.id]
    if not checks:
        return RuleGate(rule_id=rule.id, status="fail", reasons=("no acceptance trace",))
    reasons: list[str] = []
    recorded = {c.check for c in checks}
    reasons.extend(f"{name} not run" for name in RULE_CI_CHECKS if name not in recorded)
    failing = [c for c in checks if not c.passed]
    blocking = [c for c in failing if c.check not in AMBER_CHECKS]
    reasons.extend(_reason(c.check, c.detail) for c in blocking)
    if reasons:
        reasons.extend(_reason(c.check, c.detail) for c in failing if c not in blocking)
        return RuleGate(rule_id=rule.id, status="fail", reasons=tuple(reasons))
    if failing:
        return RuleGate(
            rule_id=rule.id,
            status="amber",
            reasons=tuple(_reason(c.check, c.detail) for c in failing),
        )
    return RuleGate(rule_id=rule.id, status="pass")


def _reason(check: str, detail: str) -> str:
    return f"{check} failed" + (f" ({detail})" if detail else "")


def gate_rules(plan: SitePlan) -> tuple[RuleGate, ...]:
    """Gate every rule of `plan`, in plan order."""
    return tuple(gate_rule(plan, rule) for rule in plan.rules)


def zone_from_spec(name: str, spec: ZoneSpec) -> Zone:
    """Convert a rule's zone declaration into a contract `Zone` named `name`."""
    return Zone(
        zone_id=name,
        camera_id=spec.camera_id,
        kind=spec.kind,
        polygon_px=spec.polygon_px,
        buffer_of_concept=spec.buffer_of,
        buffer_m=spec.buffer_m,
    )


def plan_zones(rules: Sequence[RuleIR]) -> dict[str, Zone]:
    """The rules' zones by name.

    A name that two rules declare differently is left out: the engine then resolves it from
    each rule's own `ZoneSpec`, so neither rule is evaluated against the other's geometry.
    """
    zones: dict[str, Zone] = {}
    clashing: set[str] = set()
    for rule in rules:
        for name, spec in sorted(rule.zones.items()):
            zone = zone_from_spec(name, spec)
            if zones.setdefault(name, zone) != zone:
                clashing.add(name)
    for name in clashing:
        _log.warning("active_plan_zone_clash", zone=name)
        del zones[name]
    return zones


def rule_calibration_cameras(rule: RuleIR) -> set[str]:
    """The cameras a rule uses: its zones' cameras and those its `calibration` names."""
    named = (part.rsplit("@", 1)[-1] for part in (rule.calibration or "").split(",") if "@" in part)
    return {spec.camera_id for spec in rule.zones.values()} | set(named)


def pick_calibration(
    rules: Sequence[RuleIR], calibrations: Mapping[str, Calibration]
) -> Calibration | None:
    """The one ground calibration the `ActivePlan` can hold.

    `ActivePlan` carries a single calibration, so this takes the calibration of the camera the
    most activated rules use, through their zones or the calibration they name
    (`ground-plane@<camera>`, set by the planner for a metric rule; ties go to the smaller camera
    id). Cameras used by no rule are ignored.
    """
    uses: dict[str, int] = {}
    for rule in rules:
        for camera_id in rule_calibration_cameras(rule):
            if camera_id in calibrations:
                uses[camera_id] = uses.get(camera_id, 0) + 1
    if not uses:
        return None
    if len(uses) > 1:
        _log.warning("active_plan_one_calibration", cameras=sorted(uses))
    camera_id = min(uses, key=lambda cam: (-uses[cam], cam))
    return calibrations[camera_id]


def _norm(text: str) -> str:
    return " ".join(text.split()).lower()


def rule_verdicts(plan: SitePlan, rule: RuleIR) -> list[FeasibilityVerdict]:
    """The `observable`/`caveat` feasibility verdicts with evidence that belong to `rule`.

    Matched by the rule's source text against each verdict's concern; when none matches, by
    concepts: every probed concept is one of the rule's (key or prompt), and every dynamic
    concept prompt of the rule was probed.
    """
    usable = [v for v in plan.feasibility if v.verdict in SCOPING_VERDICTS and v.evidence]
    text = _norm(rule.source.text or "")
    by_text = [v for v in usable if text and _norm(v.concern) == text]
    if by_text:
        return by_text
    names = {_norm(n) for key, spec in rule.concepts.items() for n in (key, spec.prompt)}
    dynamic = {_norm(spec.prompt) for spec in rule.concepts.values() if not spec.static}
    matched = []
    for verdict in usable:
        probed = {_norm(p.concept) for p in verdict.evidence}
        if probed <= names and dynamic <= probed:
            matched.append(verdict)
    return matched


def rule_cameras(plan: SitePlan, rule: RuleIR) -> tuple[str, ...]:
    """The cameras `rule` runs on: its feasibility evidence, zones and calibration cameras.

    Returns:
        Sorted camera ids; empty when the plan holds no camera evidence for the rule.
    """
    cameras = rule_calibration_cameras(rule)
    for verdict in rule_verdicts(plan, rule):
        cameras |= {probe.camera_id for probe in verdict.evidence}
    return tuple(sorted(cameras))


def plan_rule_cameras(plan: SitePlan, rules: Sequence[RuleIR]) -> dict[str, tuple[str, ...]]:
    """Rule id -> its cameras, for every rule with camera evidence (the others are logged)."""
    scoped: dict[str, tuple[str, ...]] = {}
    for rule in rules:
        cameras = rule_cameras(plan, rule)
        if cameras:
            scoped[rule.id] = cameras
        else:
            _log.warning("active_rule_unscoped", rule_id=rule.id, runs_on="every camera")
    _log.info("active_plan_scoped", plan_id=plan.plan_id, rule_cameras=scoped)
    return scoped


def plan_version_hash(plan_id: str, version: int, active: ActivePlan) -> str:
    """SHA-256 (hex) of the canonical JSON of the plan id, version and what is enforced."""
    body = active.model_dump(mode="json", exclude={"plan_version_hash"})
    material = canonical_json({"plan_id": plan_id, "version": version, "active": body})
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def active_plan_from(
    plan: SitePlan, *, calibrations: Mapping[str, Calibration] | None = None
) -> Activation:
    """Build the runtime plan from the activatable rules of `plan`.

    Args:
        plan: The approved plan.
        calibrations: Camera id -> ground calibration, if known (a `SitePlan` holds none).

    Returns:
        The activation: the gate results and the `ActivePlan`.

    Raises:
        NothingActivatableError: When no rule passes the gate.
    """
    gates = gate_rules(plan)
    allowed = {g.rule_id for g in gates if g.activatable}
    rules = tuple(rule for rule in plan.rules if rule.id in allowed)
    if not rules:
        raise NothingActivatableError(plan.plan_id, gates)
    zones = plan_zones(rules)
    draft = ActivePlan(
        rules=rules,
        privacy_mode=plan.privacy_mode,
        zones=zones,
        calibration=pick_calibration(rules, calibrations or {}),
        rule_cameras=plan_rule_cameras(plan, rules),
    )
    active = draft.model_copy(
        update={"plan_version_hash": plan_version_hash(plan.plan_id, plan.version, draft)}
    )
    return Activation(plan_id=plan.plan_id, version=plan.version, gates=gates, active_plan=active)


def save_activation(path: Path, activation: Activation) -> None:
    """Persist `activation` atomically at `path`."""
    atomic_write_text(path, activation.model_dump_json(indent=2))


def load_activation(path: Path) -> Activation | None:
    """Read a persisted activation; `None` (logged) when absent or unreadable."""
    if not path.is_file():
        return None
    try:
        return Activation.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        _log.error("active_plan_unreadable", path=str(path), error=str(exc))
        return None


def restore_activation(runtime: RuntimeLoop, path: Path) -> Activation | None:
    """Make `runtime` enforce the persisted plan again, if there is one.

    Returns:
        The restored activation, or `None`.
    """
    activation = load_activation(path)
    if activation is not None:
        runtime.set_plan(activation.active_plan)
        _log.info("active_plan_restored", plan_id=activation.plan_id, rules=activation.rule_ids)
    return activation


class PlanActivator:
    """Performs an approval: gate, persist, enforce, and record it in the evidence chain."""

    def __init__(
        self,
        *,
        runtime: RuntimeLoop | None,
        chain: EventChain,
        state_path: Path,
        calibrations: CalibrationLookup | None = None,
    ) -> None:
        """Create the activator.

        Args:
            runtime: The loop that enforces plans; `None` refuses every activation.
            chain: Where the `plan` (approval) record is appended.
            state_path: Where the active plan is persisted.
            calibrations: Finds the ground calibrations recorded for a plan's run.
        """
        self._runtime = runtime
        self._chain = chain
        self._state_path = state_path
        self._calibrations = calibrations

    def activate(self, plan: SitePlan) -> Activation:
        """Activate `plan` in the runtime.

        Raises:
            NothingActivatableError: When no rule passes the gate.
            RuntimeNotRunningError: When there is no runtime loop.
        """
        calibrations = self._calibrations(plan) if self._calibrations is not None else {}
        activation = active_plan_from(plan, calibrations=calibrations)
        if self._runtime is None:
            msg = "the runtime loop is not running (start the API with RULECAM_RUNTIME=true)"
            raise RuntimeNotRunningError(msg)
        save_activation(self._state_path, activation)
        self._runtime.set_plan(activation.active_plan)
        self._chain.append(
            "plan",
            {
                "event": "plan_approved",
                "plan_id": activation.plan_id,
                "version": activation.version,
                "plan_version_hash": activation.active_plan.plan_version_hash,
                "rules": activation.rule_ids,
                "gates": [g.model_dump(mode="json") for g in activation.gates],
            },
        )
        _log.info("plan_activated", plan_id=plan.plan_id, rules=activation.rule_ids)
        return activation
