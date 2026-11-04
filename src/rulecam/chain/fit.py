"""Fit a rule pack to a store: bind slots, apply the store's knobs, lint and run Rule CI there.

For every template, in this order:

1. **Privacy floor.** A store below the pack's privacy floor runs none of it (`blocked`).
2. **Knobs and severity.** A store may set a knob anywhere inside HQ's proven range and may
   raise a rule's severity; anything else is refused with a warning and the pack's value runs.
3. **Slots.** Each slot binds to one of the store's zones (polygon) or cameras (buffer and
   camera slots), from the store's `bindings` or by `auto_binding`. A buffer slot, and a camera
   slot of a rule that measures metres, needs a calibrated camera. A store zone that names a
   private space can never be bound (`blocked`, rule R13).
4. **Roles.** Every action's role must have a channel in the store's site config; otherwise
   the alert would go nowhere (`needs_setup`).
5. **Lint** (`planner.lint.lint_rule`) with the store's cameras and privacy mode.
6. **Rule CI** (`rule_ci.run_rule_ci`) on the store's own zones and calibrations.

Only a rule that passes all six is `ready`. Nothing here calls a model or the network.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import yaml
from pydantic import ValidationError

from rulecam.chain.errors import StoreSetupError
from rulecam.chain.knobs import KnobError, knob_defaults, with_knobs
from rulecam.chain.models import (
    PRIVACY_ORDER,
    SEVERITY_ORDER,
    FitStatus,
    FittedRule,
    RulePack,
    RuleTemplate,
    Slot,
    StoreFit,
    StoreSetup,
)
from rulecam.chain.pack import is_metric, names_private_space
from rulecam.contracts.core import Severity
from rulecam.contracts.rule_ir import RuleIR, RuleSource, ZoneSpec
from rulecam.log import get_logger
from rulecam.planner.lint import lint_rule
from rulecam.rule_ci import run_rule_ci

_log = get_logger(__name__)

Problem = tuple[Literal["needs_setup", "blocked"], str]
_LINT_STATUS: dict[str, FitStatus] = {
    "zone_out_of_view": "needs_setup",
    "private_space_zone": "blocked",
    "privacy_inconsistency": "blocked",
}


# --- loading -------------------------------------------------------------------------------


def parse_store(text: str, *, source: str = "<store>") -> StoreSetup:
    """Parse and validate a store setup's YAML.

    Raises:
        StoreSetupError: The YAML is malformed or does not validate.
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        msg = f"{source} is not valid YAML: {exc}"
        raise StoreSetupError(msg) from exc
    if not isinstance(data, dict):
        msg = f"{source} must be a YAML mapping"
        raise StoreSetupError(msg)
    try:
        return StoreSetup.model_validate(data)
    except ValidationError as exc:
        msg = f"{source} is invalid: {exc}"
        raise StoreSetupError(msg) from exc


def load_store(path: Path) -> StoreSetup:
    """Load a store setup file.

    Raises:
        StoreSetupError: The file cannot be read or does not validate.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"cannot read store setup {path}: {exc}"
        raise StoreSetupError(msg) from exc
    return parse_store(text, source=str(path))


def load_stores(directory: Path) -> list[StoreSetup]:
    """Load every `*.yaml` store setup in a directory, in file name order.

    Raises:
        StoreSetupError: The directory has no store files, or one does not validate.
    """
    paths = sorted(directory.glob("*.yaml"))
    if not paths:
        msg = f"no store setups (*.yaml) in {directory}"
        raise StoreSetupError(msg)
    return [load_store(path) for path in paths]


# --- knobs, severity and roles ------------------------------------------------------------------


def resolve_knobs(template: RuleTemplate, store: StoreSetup) -> tuple[dict[str, float], list[str]]:
    """The knob values the store runs, and a warning for every value HQ does not allow.

    Raises:
        KnobError: A knob's path is broken (the pack was not proved).
    """
    values = knob_defaults(template.rule, template.knobs)
    wanted = store.knobs.get(template.rule_id, {})
    warnings = [f"unknown knob {n!r} ignored" for n in sorted(set(wanted) - set(values))]
    for knob in template.knobs:
        value = wanted.get(knob.name)
        if value is None:
            continue
        if knob.low <= value <= knob.high:
            values[knob.name] = value
        else:
            warnings.append(
                f"{knob.name}={value:g}{knob.unit} refused: HQ allows {knob.low:g}-{knob.high:g}"
                f"{knob.unit}; the pack's {values[knob.name]:g}{knob.unit} runs"
            )
    return values, warnings


def resolve_severity(template: RuleTemplate, store: StoreSetup) -> tuple[Severity, list[str]]:
    """The severity the store runs: its own if it is not lower than the pack's."""
    floor = template.rule.severity
    wanted = store.severity.get(template.rule_id)
    if wanted is None or SEVERITY_ORDER[wanted] >= SEVERITY_ORDER[floor]:
        return wanted or floor, []
    return floor, [f"severity {wanted} refused: the pack sets {floor} and only raising is allowed"]


def missing_roles(rule: RuleIR, store: StoreSetup) -> list[str]:
    """Action roles with no alert channel at the store, in action order."""
    roles = [a.role for a in rule.actions if a.role]
    return [r for i, r in enumerate(roles) if r not in store.site.roles and r not in roles[:i]]


# --- slots -----------------------------------------------------------------------------------


def auto_binding(
    slot_name: str, slot: Slot, store: StoreSetup, bound_cameras: Sequence[str]
) -> str | None:
    """The store zone or camera a slot binds to when the store did not say.

    A polygon slot binds to a store zone of the same name. A buffer or camera slot binds to the
    one camera the template's polygon slots are on, else to the store's only camera.

    Args:
        slot_name: The slot.
        slot: What it is.
        store: The store.
        bound_cameras: Cameras of the template's polygon slots bound so far.

    Returns:
        The zone or camera, or None when it is not clear.
    """
    if slot.kind == "polygon":
        names = {zone.zone_id for zone in store.site.zones()}
        return slot_name if slot_name in names else None
    if len(set(bound_cameras)) == 1:
        return bound_cameras[0]
    return store.cameras[0] if len(store.cameras) == 1 else None


def _polygon_zone(
    slot_name: str, slot: Slot, zone_name: str, store: StoreSetup
) -> ZoneSpec | Problem:
    if names_private_space(zone_name):
        return "blocked", f"zone {zone_name!r} names a private space and can never be watched"
    zone = next((z for z in store.site.zones() if z.zone_id == zone_name), None)
    if zone is None:
        return "needs_setup", f"slot {slot_name!r} ({slot.describe}) maps to unknown {zone_name!r}"
    return ZoneSpec(kind="polygon", camera_id=zone.camera_id, polygon_px=zone.polygon_px)


def _camera_problem(
    slot_name: str, slot: Slot, camera: str, store: StoreSetup, metric: bool
) -> Problem | None:
    if camera not in store.cameras:
        return "needs_setup", f"slot {slot_name!r} maps to camera {camera!r}, which is not set up"
    needs_plane = slot.kind == "buffer" or metric
    if needs_plane and camera not in store.site.calibrations():
        return (
            "needs_setup",
            f"camera {camera!r} needs a ground calibration to measure metres for {slot_name!r}",
        )
    return None


def bind_slots(
    template: RuleTemplate, store: StoreSetup
) -> tuple[dict[str, ZoneSpec], list[str], list[Problem]]:
    """Bind every slot of a template to the store's zones and cameras.

    Args:
        template: The template.
        store: The store.

    Returns:
        The rule's zones by slot name, the cameras the rule runs on (in slot order), and the
        problems that stop it.
    """
    wanted = store.bindings.get(template.rule_id, {})
    metric = is_metric(template.rule)
    zones: dict[str, ZoneSpec] = {}
    cameras: list[str] = []
    problems: list[Problem] = []
    ordered = sorted(template.slots.items(), key=lambda item: item[1].kind != "polygon")
    for name, slot in ordered:
        target = wanted.get(name) or auto_binding(name, slot, store, cameras)
        if target is None:
            problems.append(("needs_setup", f"map slot {name!r} ({slot.describe})"))
            continue
        if slot.kind == "polygon":
            bound = _polygon_zone(name, slot, target, store)
            if isinstance(bound, ZoneSpec):
                zones[name] = bound
                cameras.append(bound.camera_id)
            else:
                problems.append(bound)
            continue
        problem = _camera_problem(name, slot, target, store, metric)
        if problem is not None:
            problems.append(problem)
            continue
        cameras.append(target)
        if slot.kind == "buffer":
            zones[name] = ZoneSpec(
                kind="buffer", camera_id=target, buffer_of=slot.buffer_of, buffer_m=slot.buffer_m
            )
    return zones, list(dict.fromkeys(cameras)), problems


# --- one rule, one store -------------------------------------------------------------------


def _calibration(template: RuleTemplate, zones: dict[str, ZoneSpec], cameras: list[str]) -> str:
    planes = [z.camera_id for z in zones.values() if z.kind == "buffer"]
    if is_metric(template.rule) or any(s.kind == "camera" for s in template.slots.values()):
        planes += cameras
    return ",".join(f"ground-plane@{c}" for c in dict.fromkeys(planes))


def build_rule(
    pack: RulePack,
    template: RuleTemplate,
    zones: dict[str, ZoneSpec],
    cameras: list[str],
    *,
    knobs: dict[str, float],
    severity: Severity,
) -> RuleIR:
    """The store's concrete rule: the template with its zones, knobs and severity.

    Raises:
        KnobError: A knob value does not fit the predicate.
    """
    source = template.rule.source
    rule = template.rule.model_copy(
        update={
            "version": pack.version,
            "zones": zones,
            "calibration": _calibration(template, zones, cameras) or None,
            "severity": severity,
            "source": RuleSource(
                kind="plan",
                text=source.text or template.concern,
                lang=source.lang,
                plan_id=f"pack:{pack.pack_id}@v{pack.version}",
                standard=source.standard,
            ),
        }
    )
    return with_knobs(rule, template.knobs, knobs)


def _lint_status(rule: RuleIR, store: StoreSetup) -> tuple[FitStatus, list[str]] | None:
    views = {camera: store.frames.get(camera) for camera in store.cameras}
    findings = lint_rule(rule, views=views, privacy_mode=store.site.privacy_mode)
    if not findings:
        return None
    statuses = {_LINT_STATUS.get(f.code, "failed") for f in findings}
    status: FitStatus = next(s for s in ("blocked", "needs_setup", "failed") if s in statuses)
    return status, [f"[{f.code}] {f.message}" for f in findings]


def _says(status: FitStatus, problems: Sequence[str], cameras: Sequence[str], ci: str) -> str:
    if status == "ready":
        return f"Runs on {', '.join(cameras) or 'every camera'}; Rule CI {ci} on this store."
    label = {"needs_setup": "Needs setup", "blocked": "Blocked", "failed": "Failed checks"}
    return f"{label[status]}: {problems[0]}"


def _privacy_block(pack: RulePack, store: StoreSetup) -> str | None:
    mode = store.site.privacy_mode
    if PRIVACY_ORDER[mode] >= PRIVACY_ORDER[pack.privacy_floor]:
        return None
    return f"the store runs {mode} privacy; this pack needs {pack.privacy_floor} or stricter"


def _verdict(
    rule: RuleIR, store: StoreSetup, problems: list[Problem]
) -> tuple[FitStatus, list[str], str]:
    """Status, problems and the Rule CI summary of a built rule."""
    texts = [text for _, text in problems]
    if problems:
        status: FitStatus = "blocked" if any(k == "blocked" for k, _ in problems) else "needs_setup"
        return status, texts, ""
    linted = _lint_status(rule, store)
    if linted is not None:
        return linted[0], linted[1], ""
    report = run_rule_ci(rule, calibrations=store.site.calibrations())
    if not report.ci_green:
        return "failed", list(report.failures) or ["Rule CI is red"], report.summary()
    return "ready", [], report.summary()


def fit_rule(pack: RulePack, template: RuleTemplate, store: StoreSetup) -> FittedRule:
    """Fit one template to one store (see the module docstring for the checks).

    Args:
        pack: The pack.
        template: One of its templates.
        store: The store.

    Returns:
        The fit; `rule` is set whenever the rule could be built.
    """
    knobs, warnings = resolve_knobs(template, store)
    severity, refused = resolve_severity(template, store)
    warnings += refused
    zones, cameras, problems = bind_slots(template, store)
    blocked = _privacy_block(pack, store)
    if blocked is not None:
        problems.insert(0, ("blocked", blocked))
    problems += [
        ("needs_setup", f"no alert channel for role {role!r}: the alert would go nowhere")
        for role in missing_roles(template.rule, store)
    ]
    try:
        rule = build_rule(pack, template, zones, cameras, knobs=knobs, severity=severity)
    except KnobError as exc:
        return FittedRule(
            rule_id=template.rule_id,
            status="failed",
            says=f"Failed checks: {exc}",
            problems=(str(exc),),
            warnings=tuple(warnings),
            knobs=knobs,
            severity=severity,
            cameras=tuple(cameras),
        )
    status, texts, ci = _verdict(rule, store, problems)
    return FittedRule(
        rule_id=template.rule_id,
        status=status,
        says=_says(status, texts, cameras, ci),
        problems=tuple(texts),
        warnings=tuple(warnings),
        knobs=knobs,
        severity=severity,
        cameras=tuple(cameras),
        ci_summary=ci,
        rule=rule,
    )


def _store_warnings(pack: RulePack, store: StoreSetup) -> tuple[str, ...]:
    known = {t.rule_id for t in pack.templates}
    named = {*store.bindings, *store.knobs, *store.severity}
    return tuple(
        f"settings for {rid!r} ignored: the pack has no such rule" for rid in sorted(named - known)
    )


def fit_store(pack: RulePack, store: StoreSetup) -> StoreFit:
    """Fit every template of a pack to one store."""
    fit = StoreFit(
        store_id=store.store_id,
        store_name=store.name,
        region=store.region,
        pack_id=pack.pack_id,
        pack_version=pack.version,
        pack_digest=pack.digest(),
        rules=tuple(fit_rule(pack, template, store) for template in pack.templates),
        warnings=_store_warnings(pack, store),
    )
    _log.info(
        "chain_store_fitted",
        store_id=store.store_id,
        pack_id=pack.pack_id,
        version=pack.version,
        ready=fit.ready_count,
        rules=len(fit.rules),
    )
    return fit


def fit_chain(pack: RulePack, stores: Sequence[StoreSetup]) -> tuple[StoreFit, ...]:
    """Fit a pack to every store, in store id order.

    Raises:
        StoreSetupError: Two stores share an id.
    """
    ids = [s.store_id for s in stores]
    repeated = sorted({i for i in ids if ids.count(i) > 1})
    if repeated:
        msg = f"store ids must be unique: {repeated}"
        raise StoreSetupError(msg)
    return tuple(fit_store(pack, store) for store in sorted(stores, key=lambda s: s.store_id))
