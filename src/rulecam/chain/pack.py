"""Rule packs: load, lint and prove the rules head office writes once for every store.

A pack is released only when it is lint clean and every template passes Rule CI at its
default **and at both ends of every knob's allowed range**. A store can then pick any allowed
value and still run a rule that was attacked with boundary traces and mutants first.

Rule CI runs on a reference fit: every slot is laid out on a synthetic HQ camera
(`REFERENCE_CAMERA`, 100 px = 1 m), polygon slots side by side, buffers on the synthetic
ground plane. Each store's own fit runs Rule CI again on its real zones and calibrations
(`rulecam.chain.fit`).

Guardrails apply to packs exactly as to requests: a concern the planner would decline, or a
slot that names a private space, stops the pack (rule R13). Prototype mode does not relax them.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Final

import yaml
from pydantic import ValidationError

from rulecam.chain.errors import PackError
from rulecam.chain.knobs import KnobError, knob_defaults, knob_value, with_knobs
from rulecam.chain.models import (
    PackFinding,
    PackFindingCode,
    PackProof,
    RulePack,
    RuleTemplate,
    SettingProof,
    Slot,
    TemplateProof,
)
from rulecam.contracts.core import PrivacyMode
from rulecam.contracts.rule_ir import DistanceM, Moving, Predicate, RuleIR, ZoneSpec
from rulecam.log import get_logger
from rulecam.planner.guardrails import GuardrailCategory, deterministic_check
from rulecam.planner.lint import face_concepts
from rulecam.planner.steps.compile import walk_predicates
from rulecam.rule_ci import run_rule_ci

_log = get_logger(__name__)

REFERENCE_CAMERA: Final = "hq-ref"
_SLOT_W_PX: Final = 400.0
_SLOT_H_PX: Final = 300.0
_SLOT_GAP_PX: Final = 100.0
_PRIVATE_MODES: Final = frozenset({PrivacyMode.ANONYMOUS, PrivacyMode.STRICT})


# --- loading -------------------------------------------------------------------------------


def parse_pack(text: str, *, source: str = "<pack>") -> RulePack:
    """Parse and validate a pack's YAML.

    Args:
        text: The YAML.
        source: Where it came from, for error messages.

    Returns:
        The pack.

    Raises:
        PackError: The YAML is malformed or does not validate.
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        msg = f"{source} is not valid YAML: {exc}"
        raise PackError(msg) from exc
    if not isinstance(data, dict):
        msg = f"{source} must be a YAML mapping"
        raise PackError(msg)
    try:
        return RulePack.model_validate(data)
    except ValidationError as exc:
        msg = f"{source} is invalid: {exc}"
        raise PackError(msg) from exc


def load_pack(path: Path) -> RulePack:
    """Load a pack file.

    Raises:
        PackError: The file cannot be read or does not validate.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"cannot read pack {path}: {exc}"
        raise PackError(msg) from exc
    return parse_pack(text, source=str(path))


# --- guardrails and lint -----------------------------------------------------------------------


def names_private_space(text: str) -> bool:
    """Whether a slot or zone name (or its description) names a private space.

    Underscores, dashes and dots count as spaces, so `staff_break_room` is caught.
    """
    declined = deterministic_check(re.sub(r"[_\-.]+", " ", text.lower()))
    return declined is not None and declined.category is GuardrailCategory.PRIVATE_SPACE


def zone_refs(pred: Predicate) -> set[str]:
    """Every zone name a predicate tree uses."""
    return {
        zone
        for node in walk_predicates(pred)
        if isinstance(zone := getattr(node, "zone", None), str)
    }


def is_metric(rule: RuleIR) -> bool:
    """Whether the rule measures metres (a distance or a speed), so it needs a calibration."""
    return any(isinstance(n, DistanceM | Moving) for n in walk_predicates(rule.predicate))


def _finding(code: PackFindingCode, template: RuleTemplate, message: str) -> PackFinding:
    return PackFinding(code=code, rule_id=template.rule_id, message=message)


def _guardrail_findings(template: RuleTemplate) -> Iterator[PackFinding]:
    for text in filter(None, (template.concern, template.rule.source.text)):
        declined = deterministic_check(text)
        if declined is not None:
            yield _finding(
                "declined_concern",
                template,
                f"{declined.reason} Alternative: {declined.alternative or 'none offered'}",
            )
    for name, slot in template.slots.items():
        if names_private_space(name) or names_private_space(slot.describe):
            yield _finding("private_space_slot", template, f"slot {name!r} names a private space")


def _slot_findings(template: RuleTemplate) -> Iterator[PackFinding]:
    rule = template.rule
    if rule.zones:
        yield _finding("template_zones", template, "zones come from slots; leave `zones` empty")
    zone_slots = {n for n, s in template.slots.items() if s.kind != "camera"}
    refs = zone_refs(rule.predicate)
    for name in sorted(refs - zone_slots):
        yield _finding("undefined_slot", template, f"the predicate uses zone {name!r}, no slot")
    for name in sorted(zone_slots - refs):
        yield _finding("unused_slot", template, f"slot {name!r} is not used by the predicate")
    for name, slot in template.slots.items():
        if slot.kind == "buffer" and slot.buffer_of not in rule.concepts:
            yield _finding(
                "buffer_concept", template, f"slot {name!r} buffers undeclared {slot.buffer_of!r}"
            )


def _knob_findings(template: RuleTemplate) -> Iterator[PackFinding]:
    names = [k.name for k in template.knobs]
    for name in sorted({n for n in names if names.count(n) > 1}):
        yield _finding("bad_knob", template, f"knob {name!r} is defined twice")
    for knob in template.knobs:
        try:
            default = knob_value(template.rule, knob)
        except KnobError as exc:
            yield _finding("bad_knob", template, str(exc))
            continue
        if not knob.low <= default <= knob.high:
            yield _finding(
                "bad_knob",
                template,
                f"knob {knob.name!r}: the template's {default:g} is outside "
                f"[{knob.low:g}, {knob.high:g}]",
            )


def lint_template(template: RuleTemplate, privacy_floor: PrivacyMode) -> list[PackFinding]:
    """Every problem with one template.

    Args:
        template: The template.
        privacy_floor: The pack's privacy floor.

    Returns:
        The findings, empty when the template is clean.
    """
    findings = [
        *_guardrail_findings(template),
        *_slot_findings(template),
        *_knob_findings(template),
    ]
    if privacy_floor in _PRIVATE_MODES:
        findings += [
            _finding("privacy_inconsistency", template, f"concept {c!r} needs a face")
            for c in face_concepts(template.rule)
        ]
    if not template.rule.actions:
        findings.append(_finding("missing_action", template, "the rule has no actions"))
    return findings


def lint_pack(pack: RulePack) -> tuple[PackFinding, ...]:
    """Every problem that stops a pack being released, in template order."""
    ids = [t.rule_id for t in pack.templates]
    findings = [
        PackFinding(code="duplicate_rule", rule_id=rid, message=f"rule id {rid!r} is repeated")
        for rid in sorted({i for i in ids if ids.count(i) > 1})
    ]
    for template in pack.templates:
        findings += lint_template(template, pack.privacy_floor)
    return tuple(findings)


# --- the reference fit and Rule CI across every knob's range -------------------------------------


def _reference_zone(index: int, slot: Slot) -> ZoneSpec:
    if slot.kind == "buffer":
        return ZoneSpec(
            kind="buffer",
            camera_id=REFERENCE_CAMERA,
            buffer_of=slot.buffer_of,
            buffer_m=slot.buffer_m,
        )
    left = index * (_SLOT_W_PX + _SLOT_GAP_PX)
    right = left + _SLOT_W_PX
    return ZoneSpec(
        kind="polygon",
        camera_id=REFERENCE_CAMERA,
        polygon_px=((left, 0.0), (right, 0.0), (right, _SLOT_H_PX), (left, _SLOT_H_PX)),
    )


def reference_rule(template: RuleTemplate, values: dict[str, float]) -> RuleIR:
    """The template laid out on the synthetic HQ camera with these knob values.

    Raises:
        KnobError: A value does not fit the predicate.
    """
    zone_slots = [(n, s) for n, s in template.slots.items() if s.kind != "camera"]
    zones = {name: _reference_zone(i, slot) for i, (name, slot) in enumerate(zone_slots)}
    needs_plane = any(s.kind != "polygon" for s in template.slots.values())
    needs_plane = needs_plane or is_metric(template.rule)
    rule = template.rule.model_copy(
        update={
            "zones": zones,
            "calibration": f"ground-plane@{REFERENCE_CAMERA}" if needs_plane else None,
        }
    )
    return with_knobs(rule, template.knobs, values)


def knob_settings(template: RuleTemplate) -> list[tuple[str, dict[str, float]]]:
    """The default, then each knob alone at its low and its high end (duplicates dropped).

    Raises:
        KnobError: A knob's path is broken.
    """
    default = knob_defaults(template.rule, template.knobs)
    settings = [("default", default)]
    for knob in template.knobs:
        for value in (knob.low, knob.high):
            if value != default[knob.name]:
                settings.append((f"{knob.name}={value:g}", {**default, knob.name: value}))
    return settings


def _prove_setting(template: RuleTemplate, label: str, values: dict[str, float]) -> SettingProof:
    try:
        rule = reference_rule(template, values)
    except KnobError as exc:
        return SettingProof(
            label=label, knobs=values, ci_green=False, summary="not run", failures=(str(exc),)
        )
    report = run_rule_ci(rule)
    return SettingProof(
        label=label,
        knobs=values,
        ci_green=report.ci_green,
        summary=report.summary(),
        failures=report.failures,
    )


def prove_template(template: RuleTemplate) -> TemplateProof:
    """Rule CI on the template's reference fit at every setting from `knob_settings`."""
    settings = knob_settings(template)
    return TemplateProof(
        rule_id=template.rule_id,
        settings=tuple(_prove_setting(template, label, values) for label, values in settings),
    )


def prove_pack(pack: RulePack) -> PackProof:
    """Lint the pack, then run Rule CI on every clean template across its knobs' ranges.

    Args:
        pack: The pack.

    Returns:
        The proof; `releasable` says whether stores may be given this version.
    """
    findings = lint_pack(pack)
    dirty = {f.rule_id for f in findings}
    proofs = tuple(prove_template(t) for t in pack.templates if t.rule_id not in dirty)
    proof = PackProof(
        pack_id=pack.pack_id,
        version=pack.version,
        digest=pack.digest(),
        findings=findings,
        templates=proofs,
    )
    _log.info(
        "chain_pack_proved",
        pack_id=pack.pack_id,
        version=pack.version,
        findings=len(findings),
        releasable=proof.releasable,
    )
    return proof
