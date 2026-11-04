"""Build a proof pack for one site and period from its evidence chain.

Steps (no step invents a number):

1. Read every chain day up to the period's end (`<chain_root>/<site>/<day>.jsonl`), verify each
   with `evidence.chain.verify_chain`, and compute each day's Merkle root and every record's
   inclusion proof.
2. **Disclose** the records the numbers need: every record whose time falls in either window
   (current or previous), every record of an event that started in a window, the two-key
   records of every proposal applied in a window, the last coverage change of each camera before
   a window, and the records just outside each window's edges in each day (so the browser can
   see that no record inside the window was left out).
3. Compute every metric from the disclosed records with `engine.evaluate`, the same recipe the
   browser runs.
4. Rules in force from the chained approvals and plan files (`rules.py`), with clauses grounded
   by Muse Spark 1.3 and checked on the cited page; summaries per audience (`narrate.py`).

Privacy: metrics are per rule, camera and shift only (never per track, person or worker);
records hold no images; stills are face-blurred or skeleton renders as the privacy mode allows.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Final, Literal

from rulecam.compliance.engine import (
    Located,
    MetricSpec,
    Period,
    Scalar,
    Shift,
    evaluate,
    event_of,
    get_path,
    matches,
    micros,
    micros_of,
    record_key,
)
from rulecam.compliance.models import (
    DayProof,
    DisclosedRecord,
    Incident,
    Metric,
    PackRequest,
    ProofPack,
    RuleInForce,
    Section,
    Still,
    Summary,
    Unit,
)
from rulecam.compliance.narrate import facts, summarise
from rulecam.compliance.proofs import day_root, inclusion_proofs
from rulecam.compliance.rules import ClauseFinder, rules_in_force, with_clauses
from rulecam.contracts import PrivacyMode, StructuredLLM
from rulecam.evidence.chain import read_chain, verify_chain
from rulecam.evidence.merkle import read_day_roots
from rulecam.evidence.packs import IMAGE_TYPES, Thumbnail, allowed_thumbnails
from rulecam.log import get_logger
from rulecam.planner.pipeline import PlanStore

_log = get_logger(__name__)

DEFAULT_SHIFTS: Final = (
    Shift(name="early", start_hour=6),
    Shift(name="late", start_hour=14),
    Shift(name="night", start_hour=22),
)
MAX_STILLS: Final = 6
STILLS_PER_INCIDENT: Final = 2
CLEARED: Final[tuple[dict[str, Scalar], ...]] = (
    {"kind": "label", "payload.label": "hazard_cleared"},
    {"kind": "action", "payload.kind": "cleared"},
)


@dataclass(frozen=True)
class ChainDay:
    """One verified chain day."""

    day: date
    records: list[Located]
    proof: DayProof
    proofs: list[list[tuple[str, Literal["L", "R"]]]]


@dataclass
class BuildInputs:
    """What a pack is built from.

    Attributes:
        chain_root: Directory of per-site chain folders.
        site: The site.
        plans: The plan files (`plans/`).
        roots_dir: Committed daily roots (`evidence/roots`).
        privacy_mode: Which stills may be shown.
        stills_root: Where the runtime writes strips; only stills under it are inlined.
        spark: Muse Spark for summaries; `None` writes none.
        clauses: Finds each rule's clause; `None` grounds none.
        timezone: The site's IANA time zone for shifts.
        clock: Now (UTC).
        new_id: Makes a pack id.
    """

    chain_root: Path
    site: str
    plans: PlanStore
    roots_dir: Path
    privacy_mode: PrivacyMode = PrivacyMode.STRICT
    stills_root: Path | None = None
    spark: StructuredLLM | None = None
    clauses: ClauseFinder | None = None
    timezone: str = "UTC"
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    new_id: Callable[[], str] = field(default=lambda: "pp-" + secrets.token_hex(6))


def period_for(request: PackRequest, timezone: str) -> Period:
    """The current window and the previous window of the same length right before it."""
    start, end = request.start.astimezone(UTC), request.end.astimezone(UTC)
    if end <= start:
        msg = "the period must end after it starts"
        raise ValueError(msg)
    length = end - start
    return Period(
        start=start,
        end=end,
        previous_start=start - length,
        previous_end=start,
        timezone=timezone,
        shifts=request.shifts if request.shifts is not None else DEFAULT_SHIFTS,
    )


def load_days(chain_dir: Path, roots_dir: Path, site: str, last: date) -> list[ChainDay]:
    """Read and verify every chain day up to `last`, with roots and inclusion proofs."""
    days: list[ChainDay] = []
    for path in sorted(chain_dir.glob("*.jsonl")) if chain_dir.is_dir() else []:
        try:
            day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if day > last:
            continue
        report = verify_chain(path)
        records = read_chain(path)
        hashes = [r.hash for r in records]
        roots_file = roots_dir / f"{day.isoformat()}.txt"
        committed = read_day_roots(roots_file).get(site) if roots_file.is_file() else None
        located = [
            Located(
                key=record_key(day.isoformat(), r.seq),
                day=day.isoformat(),
                record=r.model_dump(mode="json"),
            )
            for r in records
        ]
        days.append(
            ChainDay(
                day=day,
                records=located,
                proof=DayProof(
                    day=day.isoformat(),
                    n=len(records),
                    root=day_root(hashes),
                    chain_ok=report.ok,
                    reason=report.reason,
                    committed_root=committed[0] if committed else None,
                    committed_n=committed[1] if committed else None,
                ),
                proofs=inclusion_proofs(hashes),
            )
        )
    return days


def _in(item: Located, start: int, end: int) -> bool:
    return start <= micros(str(item.record["ts"])) < end


def disclose(days: Sequence[ChainDay], period: Period) -> tuple[set[str], list[DayProof]]:
    """The keys of the records the pack discloses, and each day's window edges."""
    windows = (
        (micros_of(period.previous_start), micros_of(period.previous_end)),
        (micros_of(period.start), micros_of(period.end)),
    )
    everything = [item for d in days for item in d.records]
    keys: set[str] = set()
    events: set[str] = set()
    proposals: set[str] = set()
    for item in everything:
        if any(_in(item, lo, hi) for lo, hi in windows):
            keys.add(item.key)
            event = event_of(item.record)
            if item.record.get("kind") == "candidate" and event is not None:
                events.add(event)
            proposal = get_path(item.record, "payload.proposal_id")
            if item.record.get("kind") == "rule_change" and isinstance(proposal, str):
                proposals.add(proposal)
    for item in everything:
        event = event_of(item.record)
        proposal = get_path(item.record, "payload.proposal_id")
        if (event is not None and event in events) or (
            item.record.get("kind") == "rule_change" and proposal in proposals
        ):
            keys.add(item.key)
    keys |= _context_before(everything, windows[0][0])
    proofs: list[DayProof] = []
    for d in days:
        inside = [
            i for i, item in enumerate(d.records) if any(_in(item, lo, hi) for lo, hi in windows)
        ]
        first = inside[0] if inside else None
        last = inside[-1] if inside else None
        for edge in (first - 1 if first else None, last + 1 if last is not None else None):
            if edge is not None and 0 <= edge < len(d.records):
                keys.add(d.records[edge].key)
        proofs.append(
            d.proof.model_copy(
                update={
                    "window_first": first + 1 if first is not None else None,
                    "window_last": last + 1 if last is not None else None,
                }
            )
        )
    return keys, proofs


def _context_before(everything: Sequence[Located], start: int) -> set[str]:
    """The last coverage change per camera and the last plan approval before the windows."""
    keys: set[str] = set()
    last_cover: dict[str, str] = {}
    last_plan: str | None = None
    for item in everything:
        if micros(str(item.record["ts"])) >= start:
            break
        kind = item.record.get("kind")
        if kind == "coverage":
            camera = get_path(item.record, "payload.camera_id")
            if isinstance(camera, str):
                last_cover[camera] = item.key
        elif kind == "plan" and get_path(item.record, "payload.event") == "plan_approved":
            last_plan = item.key
    keys.update(last_cover.values())
    if last_plan is not None:
        keys.add(last_plan)
    return keys


@dataclass(frozen=True)
class _Def:
    id: str
    section: Section
    label: str
    spec: MetricSpec
    unit: Unit = "count"


def _count(where: dict[str, Scalar], **scope: str | None) -> MetricSpec:
    return MetricSpec(op="count", where=where, **scope)


CANDIDATE: Final[dict[str, Scalar]] = {"kind": "candidate"}
VERIFIED: Final[dict[str, Scalar]] = {
    "kind": "verdict",
    "payload.tier": "glimmer",
    "payload.verdict": "violation",
}
REJECTED: Final[dict[str, Scalar]] = {**VERIFIED, "payload.verdict": "no_violation"}
UNSURE: Final[dict[str, Scalar]] = {**VERIFIED, "payload.verdict": "unsure"}


def _core_metrics() -> list[_Def]:
    glimmer_verdict: tuple[dict[str, Scalar], ...] = (
        {"kind": "verdict", "payload.tier": "glimmer"},
    )
    return [
        _Def("alerts_raised", "alerts", "Alerts raised by the rules", _count(CANDIDATE)),
        _Def("glimmer_verified", "alerts", "Confirmed by Muse Glimmer", _count(VERIFIED)),
        _Def("glimmer_rejected", "alerts", "Rejected by Muse Glimmer", _count(REJECTED)),
        _Def("glimmer_unsure", "alerts", "Muse Glimmer unsure", _count(UNSURE)),
        _Def(
            "sent_to_person",
            "alerts",
            "Sent to a person to review",
            _count({"kind": "verdict", "payload.decision.route": "human_queue"}),
        ),
        _Def(
            "policy_alerted",
            "alerts",
            "Passed on as alerts by the policy",
            _count({"kind": "verdict", "payload.decision.alert": True}),
        ),
        _Def(
            "tamper_attempts",
            "alerts",
            "Tamper attempts logged",
            _count({"kind": "tamper_attempt"}),
        ),
        _Def("actions", "response", "Actions taken", _count({"kind": "action"})),
        _Def(
            "actions_dry_run",
            "response",
            "Of which dry-run (logged, not sent)",
            MetricSpec(op="count", where={"kind": "action"}, prefix={"payload.detail": "dry-run"}),
        ),
        _Def("acks", "response", "Acknowledgements by a role", _count({"kind": "ack"})),
        _Def(
            "time_to_verdict_median",
            "response",
            "Median time from alert to Glimmer's verdict",
            MetricSpec(op="median_s", to=glimmer_verdict),
            "seconds",
        ),
        _Def(
            "time_to_action_median",
            "response",
            "Median time from alert to first action",
            MetricSpec(op="median_s", to=({"kind": "action"},)),
            "seconds",
        ),
        _Def(
            "time_to_action_p90",
            "response",
            "90th percentile time from alert to first action",
            MetricSpec(op="p90_s", to=({"kind": "action"},)),
            "seconds",
        ),
        _Def(
            "time_to_ack_median",
            "response",
            "Median time from alert to acknowledgement",
            MetricSpec(op="median_s", to=({"kind": "ack"},)),
            "seconds",
        ),
        _Def(
            "disputes_filed",
            "disputes",
            "Disputes filed by workers",
            _count({"kind": "dispute", "payload.dispute.outcome": "pending"}),
        ),
        _Def(
            "disputes_upheld",
            "disputes",
            "Disputes upheld (false alarm)",
            _count({"kind": "dispute", "payload.dispute.outcome": "upheld"}),
        ),
        _Def(
            "disputes_rejected",
            "disputes",
            "Disputes rejected",
            _count({"kind": "dispute", "payload.dispute.outcome": "rejected"}),
        ),
        _Def(
            "false_alarm_labels",
            "disputes",
            "Alerts labelled false alarm",
            _count({"kind": "label", "payload.label": "false_alarm"}),
        ),
        _Def(
            "plan_approvals",
            "governance",
            "Plans approved (Rule CI gate at approval)",
            _count({"kind": "plan", "payload.event": "plan_approved"}),
        ),
        _Def(
            "setting_proposals",
            "governance",
            "Protected privacy settings proposed",
            _count({"kind": "rule_change", "payload.action": "setting_proposal"}),
        ),
        _Def(
            "settings_applied",
            "governance",
            "Protected settings changed (both keys)",
            _count({"kind": "rule_change", "payload.action": "setting_applied"}),
        ),
        _Def(
            "settings_rejected",
            "governance",
            "Protected setting changes refused",
            _count({"kind": "rule_change", "payload.action": "setting_rejected"}),
        ),
        _Def(
            "settings_without_both_keys",
            "governance",
            "Protected settings changed without both keys",
            MetricSpec(op="two_key_unapproved"),
        ),
    ]


def _scoped_metrics(
    records: Sequence[Located], rules: Sequence[RuleInForce], period: Period
) -> list[_Def]:
    defs: list[_Def] = []
    rule_ids = sorted(
        {
            str(get_path(i.record, "payload.candidate.rule_id"))
            for i in records
            if i.record.get("kind") == "candidate"
        }
        | {r.rule_id for r in rules}
    )
    distance = {r.rule_id for r in rules if r.distance_rule}
    for rule_id in rule_ids:
        section: Section = "forklift" if rule_id in distance else "rule"
        raised = "Near misses recorded" if rule_id in distance else "Alerts raised"
        defs += [
            _Def(f"rule:{rule_id}:alerts_raised", section, raised, _count(CANDIDATE, rule=rule_id)),
            _Def(
                f"rule:{rule_id}:glimmer_verified",
                section,
                "Confirmed by Muse Glimmer"
                + (" (refresher-training review due)" if rule_id in distance else ""),
                _count(VERIFIED, rule=rule_id),
            ),
            _Def(
                f"rule:{rule_id}:glimmer_rejected",
                section,
                "Rejected by Muse Glimmer",
                _count(REJECTED, rule=rule_id),
            ),
        ]
    for shift in period.shifts:
        defs += [
            _Def(
                f"shift:{shift.name}:alerts_raised",
                "shift",
                f"Alerts raised, {shift.name} shift",
                _count(CANDIDATE, shift=shift.name),
            ),
            _Def(
                f"shift:{shift.name}:glimmer_verified",
                "shift",
                f"Confirmed by Glimmer, {shift.name} shift",
                _count(VERIFIED, shift=shift.name),
            ),
        ]
    cameras = sorted(
        {
            str(c)
            for i in records
            if (c := get_path(i.record, "payload.candidate.camera_id")) is not None
        }
    )
    covered = sorted(
        {
            str(c)
            for i in records
            if i.record.get("kind") == "coverage"
            and (c := get_path(i.record, "payload.camera_id")) is not None
        }
    )
    defs += [
        _Def(
            f"camera:{c}:alerts_raised",
            "camera",
            f"Alerts raised on {c}",
            _count(CANDIDATE, camera=c),
        )
        for c in cameras
    ]
    for camera in covered:
        defs += [
            _Def(
                f"camera:{camera}:watched_s",
                "camera",
                f"Time {camera} could see (coverage OK)",
                MetricSpec(op="state_s", camera=camera, state="ok"),
                "seconds",
            ),
            _Def(
                f"camera:{camera}:blind_s",
                "camera",
                f"Time {camera} was BLIND",
                MetricSpec(op="state_s", camera=camera, state="blind"),
                "seconds",
            ),
        ]
    if any(matches(i.record, option, {}) for i in records for option in CLEARED):
        defs += [
            _Def(
                "time_to_clear_median",
                "hazard",
                "Median time from hazard seen to cleared",
                MetricSpec(op="median_s", to=CLEARED),
                "seconds",
            ),
            _Def(
                "time_to_clear_max",
                "hazard",
                "Longest time from hazard seen to cleared",
                MetricSpec(op="max_s", to=CLEARED),
                "seconds",
            ),
        ]
    return defs


def compute(defs: Sequence[_Def], records: Sequence[Located], period: Period) -> list[Metric]:
    """Evaluate every metric in both windows over the disclosed records."""
    out: list[Metric] = []
    for d in defs:
        now = evaluate(d.spec, records, period, "current")
        before = evaluate(d.spec, records, period, "previous")
        out.append(
            Metric(
                id=d.id,
                section=d.section,
                label=d.label,
                unit=d.unit,
                spec=d.spec,
                value=now.value,
                previous=before.value,
                refs=now.refs,
                previous_refs=before.refs,
            )
        )
    return out


def incidents(records: Sequence[Located], period: Period) -> list[Incident]:
    """Alerts in the current window that Glimmer confirmed or the policy passed on."""
    start, end = micros_of(period.start), micros_of(period.end)
    verdict: dict[str, str] = {}
    route: dict[str, tuple[str | None, bool]] = {}
    for item in records:
        event = event_of(item.record)
        if event is None or item.record.get("kind") != "verdict":
            continue
        if get_path(item.record, "payload.tier") == "glimmer":
            verdict.setdefault(event, str(get_path(item.record, "payload.verdict")))
        decision = get_path(item.record, "payload.decision")
        if isinstance(decision, dict):
            route.setdefault(event, (str(decision.get("route")), decision.get("alert") is True))
    out: list[Incident] = []
    for item in records:
        event = event_of(item.record)
        if item.record.get("kind") != "candidate" or event is None or not _in(item, start, end):
            continue
        confirmed = verdict.get(event) == "violation"
        alerted = route.get(event, (None, False))[1]
        if not (confirmed or alerted):
            continue
        candidate = get_path(item.record, "payload.candidate")
        c = candidate if isinstance(candidate, dict) else {}
        out.append(
            Incident(
                event_id=event,
                key=item.key,
                rule_id=str(c.get("rule_id", "")),
                camera_id=str(c.get("camera_id", "")),
                severity=str(c.get("severity", "")),
                ts=datetime.fromisoformat(str(item.record["ts"]).replace("Z", "+00:00")),
                verdict=verdict.get(event),
                route=route.get(event, (None, False))[0],
            )
        )
    return out


def stills(
    found: Sequence[Incident], records: Sequence[Located], inputs: BuildInputs
) -> list[Still]:
    """Up to `MAX_STILLS` anonymised stills of the incidents, only from under `stills_root`."""
    if inputs.stills_root is None:
        return []
    kind: Literal["skeleton", "masked"] = (
        "masked" if inputs.privacy_mode is PrivacyMode.STANDARD else "skeleton"
    )
    base = inputs.stills_root.resolve()
    by_key = {i.key: i for i in records}
    out: list[Still] = []
    for incident in found:
        paths = get_path(by_key[incident.key].record, "payload.candidate.strip_paths")
        for raw in (paths if isinstance(paths, list) else [])[:STILLS_PER_INCIDENT]:
            path = Path(str(raw)).resolve()
            mime = IMAGE_TYPES.get(path.suffix.lower())
            if mime is None or not path.is_relative_to(base) or not path.is_file():
                continue
            if not allowed_thumbnails([Thumbnail(path=str(path), kind=kind)], inputs.privacy_mode):
                continue
            data = path.read_bytes()
            out.append(
                Still(
                    event_id=incident.event_id,
                    kind=kind,
                    sha256=hashlib.sha256(data).hexdigest(),
                    data_uri=f"data:{mime};base64," + base64.b64encode(data).decode("ascii"),
                )
            )
            if len(out) >= MAX_STILLS:
                return out
    return out


def _notes(metrics: Sequence[Metric], records: Sequence[Located], period: Period) -> list[str]:
    by_id = {m.id: m for m in metrics}
    notes: list[str] = []
    if not any(m.id.endswith(":watched_s") for m in metrics):
        notes.append(
            "No camera coverage records were chained in this period, so the pack makes no claim "
            "about how much of the period the cameras could see; BLIND time is unknown, not zero."
        )
    actions, dry = by_id["actions"].value or 0, by_id["actions_dry_run"].value or 0
    if actions and dry == actions:
        notes.append(
            "Every action in this period ran in dry-run mode: it was logged but no one was "
            "notified, spoken to or interlocked."
        )
    if not (by_id["acks"].value or 0):
        notes.append("No acknowledgements were chained, so there is no acknowledgement time.")
    if not any(r.record.get("kind") == "rule_change" for r in records):
        notes.append(
            "No protected privacy setting (privacy mode, retention, the per-person metrics ban) "
            "was proposed or changed in the disclosed records."
        )
    notes.append(
        "Numbers are per rule, camera and shift "
        f"({', '.join(s.name for s in period.shifts) or 'no shifts'}, {period.timezone}); nothing "
        "is measured or grouped per person or per tracked individual."
    )
    return notes


def build_pack(
    request: PackRequest, inputs: BuildInputs, *, pack_id: str | None = None
) -> ProofPack:
    """Build the pack.

    Args:
        request: The period, audiences, crew language, shifts and model options.
        inputs: Where the chain, plans and roots are, and the models.
        pack_id: Use this id (the API reserves one before building).

    Returns:
        The pack.

    Raises:
        ValueError: If the period is empty or reversed.
    """
    period = period_for(request, inputs.timezone)
    days = load_days(
        inputs.chain_root / inputs.site,
        inputs.roots_dir,
        inputs.site,
        (period.end - timedelta(microseconds=1)).date(),
    )
    everything = [item for d in days for item in d.records]
    keys, day_proofs = disclose(days, period)
    disclosed = [item for item in everything if item.key in keys]
    rules = rules_in_force(everything, period, inputs.plans)
    metrics = compute(
        _core_metrics() + _scoped_metrics(disclosed, rules, period), disclosed, period
    )
    alerts = {
        m.id.split(":")[1]: int(m.value or 0)
        for m in metrics
        if m.id.startswith("rule:") and m.id.endswith(":alerts_raised")
    }
    if inputs.clauses is not None and request.models:
        rules = with_clauses(rules, inputs.clauses, request.ground_rules, alerts)
    found = incidents(disclosed, period)
    notes = _notes(metrics, disclosed, period)
    summaries: list[Summary] = []
    if inputs.spark is not None and request.models:
        context = facts(metrics, rules, notes)
        for audience in request.audiences:
            lang = request.crew_lang if audience == "worker_rep" else "en"
            try:
                summaries.append(summarise(inputs.spark, audience, lang, context, metrics))
            except Exception as exc:  # a model failure leaves the numbers standing
                _log.warning("proof_summary_failed", audience=audience, error=type(exc).__name__)
                notes.append(f"No {audience} summary: Muse Spark failed ({type(exc).__name__}).")
    proof_by_key = {item.key: tuple(d.proofs[i]) for d in days for i, item in enumerate(d.records)}
    return ProofPack(
        pack_id=pack_id or inputs.new_id(),
        site=inputs.site,
        created_at=inputs.clock(),
        title=request.title
        or f"Proof pack {period.start:%Y-%m-%d %H:%M} to {period.end:%Y-%m-%d %H:%M} UTC",
        period=period,
        audiences=request.audiences,
        crew_lang=request.crew_lang,
        privacy_mode=inputs.privacy_mode,
        rules=tuple(rules),
        metrics=tuple(metrics),
        incidents=tuple(found),
        days=tuple(day_proofs),
        records=tuple(
            DisclosedRecord(
                key=item.key, day=item.day, record=dict(item.record), proof=proof_by_key[item.key]
            )
            for item in disclosed
        ),
        summaries=tuple(summaries),
        stills=tuple(stills(found, disclosed, inputs)),
        notes=tuple(notes),
    )
