"""Rules in force during a period: from the chained plan approvals, the plan files and Spark.

Approving a plan replaces the active one, so each chained `plan_approved` record starts an
interval that the next approval ends. A rule is in force when an interval that lists it overlaps
the period. For each rule the pack shows:

- the plan(s) and approval records (chained, so the recipient's browser checks them);
- Rule CI's gate per rule from those approval records (`gates[].status`), and the plan file's
  acceptance detail (`ci_green`, e.g. "boundary 16/16, mutations 6/6"), which is not chained;
- the clause it gives evidence for: the plan's own citation when it has one; otherwise Muse Spark
  1.3 searches the web for the governing clause of the rule's text
  (`planner.standards.ground_concern_standard`) and the quote is kept as checked only when it is
  found on the fetched page (`check_citation`). Nothing about a clause is written by hand here.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from rulecam.clients.meta_hosted.grounding import Grounded
from rulecam.compliance.engine import Located, get_path, micros, micros_of
from rulecam.compliance.engine import Period as PackPeriod
from rulecam.compliance.models import Clause, RuleInForce
from rulecam.contracts import SitePlan
from rulecam.log import get_logger
from rulecam.planner.pipeline import PlanStore
from rulecam.planner.standards import (
    GROUNDING_MODEL,
    MAX_SEARCH_CALLS,
    MIN_QUOTE_CHARS,
    STANDARDS_SYSTEM_PROMPT,
    Citation,
    CitationCheck,
    GroundedCitation,
    GroundingLLM,
    StandardsLibrary,
    check_citation,
    ground_concern_standard,
    normalize_text,
)

_log = get_logger(__name__)

CitationChecker = Callable[[Citation], CitationCheck]
CONCERN_VERSION = "v3"
# The rule is the owner's words; the clause is whatever published requirement its alerts give
# evidence about (a general duty counts), so Spark is asked for that, not for a clause that
# repeats the rule's own number.
CONCERN = (
    'A camera rule enforced at this site: "{text}".{hint} Which published safety, health or '
    "hygiene clause sets the requirement that this rule's alerts give evidence about?"
)


@dataclass(frozen=True)
class Approval:
    """One chained plan approval and the interval it was active."""

    key: str
    plan_id: str
    rules: tuple[str, ...]
    gates: Mapping[str, tuple[str, tuple[str, ...]]]
    start_us: int
    end_us: int | None


def approvals(records: Sequence[Located]) -> list[Approval]:
    """Every chained `plan_approved` record, each active until the next one."""
    found = [
        item
        for item in records
        if item.record.get("kind") == "plan"
        and get_path(item.record, "payload.event") == "plan_approved"
    ]
    out: list[Approval] = []
    for position, item in enumerate(found):
        gates_raw = get_path(item.record, "payload.gates")
        gates = {
            str(g.get("rule_id")): (
                str(g.get("status")),
                tuple(str(r) for r in g.get("reasons") or []),
            )
            for g in (gates_raw if isinstance(gates_raw, list) else [])
            if isinstance(g, dict)
        }
        rules_raw = get_path(item.record, "payload.rules")
        nxt = found[position + 1] if position + 1 < len(found) else None
        out.append(
            Approval(
                key=item.key,
                plan_id=str(get_path(item.record, "payload.plan_id") or ""),
                rules=tuple(str(r) for r in rules_raw) if isinstance(rules_raw, list) else (),
                gates=gates,
                start_us=micros(str(item.record["ts"])),
                end_us=micros(str(nxt.record["ts"])) if nxt is not None else None,
            )
        )
    return out


def in_force(found: Sequence[Approval], period: PackPeriod) -> list[Approval]:
    """The approvals whose active interval overlaps the current window."""
    start, end = micros_of(period.start), micros_of(period.end)
    return [a for a in found if a.start_us < end and (a.end_us is None or a.end_us > start)]


def _overlap_s(approval: Approval, period: PackPeriod) -> float:
    start, end = micros_of(period.start), micros_of(period.end)
    hi = min(approval.end_us if approval.end_us is not None else end, end)
    return max(0, hi - max(approval.start_us, start)) / 1_000_000


def _chained_rule(records: Sequence[Located], rule_id: str) -> Mapping[str, object]:
    """The rule as the latest candidate for it chained it (`payload.context.rule`)."""
    for item in reversed(records):
        rule = get_path(item.record, "payload.context.rule")
        if isinstance(rule, dict) and rule.get("id") == rule_id:
            return rule
    return {}


def _ci_detail(plan: SitePlan | None, rule_id: str) -> str | None:
    if plan is None:
        return None
    for check in plan.acceptance.checks:
        if check.check == "ci_green" and check.item == rule_id:
            return f"{'passed' if check.passed else 'failed'}: {check.detail}"
    return None


def _has_distance(rule: Mapping[str, object]) -> bool:
    return '"op": "distance_m"' in json.dumps(rule.get("predicate", {}), sort_keys=True)


def rules_in_force(
    records: Sequence[Located], period: PackPeriod, plans: PlanStore
) -> list[RuleInForce]:
    """Group the approvals active in the period by rule, with text, gates and Rule CI detail.

    Args:
        records: Chain records in order (all days read, so approvals before the period count).
        period: The pack's windows.
        plans: Where plan files live (`plans/`); a missing plan file only loses the CI detail.

    Returns:
        One entry per rule id, the most active first.
    """
    active = in_force(approvals(records), period)
    loaded: dict[str, SitePlan | None] = {}
    for approval in active:
        if approval.plan_id and approval.plan_id not in loaded:
            try:
                loaded[approval.plan_id] = plans.load_plan(approval.plan_id)
            except (ValueError, OSError) as exc:
                _log.warning("proof_plan_unreadable", plan_id=approval.plan_id, error=str(exc))
                loaded[approval.plan_id] = None
    by_rule: dict[str, list[Approval]] = {}
    for approval in active:
        for rule_id in approval.rules:
            by_rule.setdefault(rule_id, []).append(approval)
    out: list[RuleInForce] = []
    for rule_id, group in by_rule.items():
        latest = loaded.get(group[-1].plan_id)
        plan_rule = next((r for r in latest.rules if r.id == rule_id), None) if latest else None
        chained = _chained_rule(records, rule_id)
        source = chained.get("source") if isinstance(chained.get("source"), dict) else {}
        text = plan_rule.source.text if plan_rule else str(_dict(source).get("text", ""))
        standards = plan_rule.source.standard if plan_rule else _strings(_dict(source), "standard")
        out.append(
            RuleInForce(
                rule_id=rule_id,
                text=text,
                severity=plan_rule.severity if plan_rule else str(chained.get("severity", "")),
                plan_ids=tuple(dict.fromkeys(a.plan_id for a in group)),
                approvals=tuple(a.key for a in group),
                gate=group[-1].gates.get(rule_id, ("unknown", ()))[0],
                gate_reasons=group[-1].gates.get(rule_id, ("unknown", ()))[1],
                ci_detail=_ci_detail(latest, rule_id),
                active_s=sum(_overlap_s(a, period) for a in group),
                standards=tuple(standards),
                distance_rule=_has_distance(
                    plan_rule.model_dump(mode="json") if plan_rule else chained
                ),
            )
        )
    return sorted(out, key=lambda r: -r.active_s)


def _dict(value: object) -> Mapping[str, object]:
    return value if isinstance(value, dict) else {}


def _strings(source: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = source.get(key)
    return tuple(str(v) for v in value) if isinstance(value, list) else ()


class ClauseFinder:
    """Finds and checks the clause each rule gives evidence for, cached by rule text.

    The cache (`<data>/compliance/clauses.json`) keeps each answer with its check, so the same
    rule is not searched again for every pack; the model and page calls also go through the
    replay cache.
    """

    def __init__(
        self,
        spark: GroundingLLM | None,
        check: CitationChecker | None,
        cache_path: Path | None = None,
    ) -> None:
        """Create the finder.

        Args:
            spark: Muse Spark with web search; `None` finds no new clauses.
            check: Fetches the cited page and looks for the quote (`check_citation`).
            cache_path: Where answers are kept between packs.
        """
        self._spark = spark
        self._check = check
        self._cache_path = cache_path
        self._cache: dict[str, dict[str, object]] = self._load()

    @classmethod
    def live(
        cls, spark: GroundingLLM | None, library: StandardsLibrary, cache_path: Path | None
    ) -> ClauseFinder:
        """A finder that checks quotes on pages fetched through `library`."""
        return cls(spark, lambda c: check_citation(c, library), cache_path)

    def _load(self) -> dict[str, dict[str, object]]:
        if self._cache_path is None or not self._cache_path.is_file():
            return {}
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self) -> None:
        if self._cache_path is None:
            return
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._cache, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(self._cache_path)

    def find(self, rule: RuleInForce) -> tuple[Clause | None, str]:
        """The rule's clause and a note on where it came from (or why there is none)."""
        key = hashlib.sha256(
            f"{CONCERN_VERSION}|{rule.text}|{','.join(rule.standards)}".encode()
        ).hexdigest()
        cached = self._cache.get(key)
        if cached is not None:
            clause = cached.get("clause")
            return (
                Clause.model_validate(clause) if isinstance(clause, dict) else None,
                str(cached.get("note", "")),
            )
        if self._spark is None or self._check is None:
            return None, "no clause: the plan cites none and model grounding is off for this pack"
        hint = f" The owner's plan names: {', '.join(rule.standards)}." if rule.standards else ""
        try:
            citation, found = self._ask(CONCERN.format(text=rule.text, hint=hint))
        except Exception as exc:  # a model failure must not fail the pack
            _log.warning("proof_clause_failed", rule_id=rule.rule_id, error=type(exc).__name__)
            return None, f"no clause: Muse Spark grounding failed ({type(exc).__name__})"
        if citation is None:
            clause, note = None, "Muse Spark 1.3 searched and found no governing clause"
        else:
            clause = self._checked(citation, found, rule)
            note = {
                "page": "clause found by Muse Spark 1.3; quote found on the fetched page",
                "search": "clause found by Muse Spark 1.3; quote found in the search result for "
                "the cited page (the page itself could not be fetched)",
                "none": "clause found by Muse Spark 1.3; quote NOT confirmed",
            }[clause.check]
        self._cache[key] = {
            "clause": clause.model_dump(mode="json") if clause else None,
            "note": note,
        }
        self._save()
        return clause, note

    def _ask(self, concern: str) -> tuple[Citation | None, Grounded[GroundedCitation] | None]:
        """Spark's citation, with the pages its searches returned when the client reports them."""
        grounded = getattr(self._spark, "grounded", None)
        if not callable(grounded):
            return ground_concern_standard(concern, spark=self._spark), None  # type: ignore[arg-type]
        found: Grounded[GroundedCitation] = grounded(
            model=GROUNDING_MODEL,
            messages=[
                {"role": "system", "content": STANDARDS_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps({"concern": concern}, ensure_ascii=False)},
            ],
            schema=GroundedCitation,
            max_tool_calls=MAX_SEARCH_CALLS,
        )
        return found.answer.citation, found

    def _checked(
        self, citation: Citation, found: Grounded[GroundedCitation] | None, rule: RuleInForce
    ) -> Clause:
        """Check the quote on the fetched page, else in the search result of that page."""
        assert self._check is not None  # noqa: S101 - find() returns early without a checker
        result = self._check(citation)
        check: Literal["page", "search", "none"] = "page" if result.matched else "none"
        detail = result.detail
        source = found.retrieved(citation.url) if found is not None else None
        if not result.matched and source is not None:
            seen = normalize_text(f"{source.title} {source.snippet}")
            needle = normalize_text(citation.quoted_text).rstrip(" .;:,")
            if len(needle) >= MIN_QUOTE_CHARS and needle in seen:
                check = "search"
                detail = f"quote found in the search result for the cited page; {detail}"
        return Clause(
            ref=citation.ref,
            url=citation.url,
            quote=citation.quoted_text,
            checked=check != "none",
            check=check,
            detail=detail,
            source="plan" if rule.standards else "muse-spark-1.3",
        )


def with_clauses(
    rules: Sequence[RuleInForce], finder: ClauseFinder, limit: int, alerts: Mapping[str, int]
) -> list[RuleInForce]:
    """Ground up to `limit` rules (most alerts first); the rest keep a note saying so."""
    ranked = sorted(rules, key=lambda r: (-alerts.get(r.rule_id, 0), -r.active_s))
    chosen = {r.rule_id for r in ranked[:limit]}
    out: list[RuleInForce] = []
    for rule in rules:
        if rule.rule_id in chosen and rule.text:
            clause, note = finder.find(rule)
        else:
            clause, note = None, "not grounded in this pack (beyond the per-pack limit)"
        out.append(rule.model_copy(update={"clause": clause, "clause_note": note}))
    return out
