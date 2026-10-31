"""The PLAN-9.5 §2 scorecard: every area's targets, the measured values, pass or fail.

Inputs, any of which may be missing:

- an eval report (`rulecam eval run` / `rulecam eval score`): alert quality, latency, lag, cost,
  tracking and planner outcomes;
- live samples of `/runtime/metrics` (the 30-minute two-camera soak): lag and keyframe mode;
- a checks file (`eval/checks.yaml`) for what the harness cannot measure: owner login, deploy,
  demo, detection agreement from `scripts/sam_pod/compare.py`, and so on.

A criterion without data is **not measured**, never a pass. An area passes only when every one
of its criteria passes.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, ValidationError

from rulecam.contracts import Frozen
from rulecam.eval import metrics as m
from rulecam.eval.errors import EvalError
from rulecam.eval.score import EvalReport, RunScore

Status = Literal["pass", "fail", "not measured"]
STATUS_TEXT: dict[Status, str] = {"pass": "PASS", "fail": "FAIL", "not measured": "NOT MEASURED"}
DETERMINISM_RUNS = 20


class Check(Frozen):
    """One externally verified criterion: a yes/no, or a number compared with its target."""

    passed: bool | None = None
    value: float | None = None
    evidence: str = ""


class Checks(Frozen):
    """The checks file: criterion key -> check."""

    checks: dict[str, Check] = Field(default_factory=dict)

    def get(self, key: str) -> Check | None:
        """Return a check, or `None` when it is not in the file."""
        return self.checks.get(key)


def load_checks(path: Path) -> Checks:
    """Read a checks YAML file (`{key: {passed, value, evidence}}`).

    Raises:
        EvalError: If it is unreadable or malformed.
    """
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return Checks(checks={str(k): Check.model_validate(v or {}) for k, v in data.items()})
    except (OSError, yaml.YAMLError, AttributeError, ValidationError) as exc:
        msg = f"{path}: {exc}"
        raise EvalError(msg) from exc


class LiveSample(Frozen):
    """`/runtime/metrics` sampled on the live deployment."""

    lags_s: tuple[float, ...]
    keyframe_mode: bool
    source: str


class Row(Frozen):
    """One criterion of the scorecard."""

    area: str
    target: str
    measured: str
    status: Status


class Scorecard(Frozen):
    """All rows, in PLAN §2 order, with a note on where the numbers came from."""

    title: str
    sources: tuple[str, ...]
    rows: tuple[Row, ...]
    report: EvalReport | None = None

    def area_status(self, area: str) -> Status:
        """Pass if every row of the area passes, fail if any fails, else not measured."""
        statuses = {row.status for row in self.rows if row.area == area}
        if "fail" in statuses:
            return "fail"
        return "pass" if statuses == {"pass"} else "not measured"

    @property
    def areas(self) -> tuple[str, ...]:
        """Areas in row order."""
        return tuple(dict.fromkeys(row.area for row in self.rows))


@dataclass(frozen=True)
class _Inputs:
    report: EvalReport | None
    checks: Checks
    live: LiveSample | None

    @property
    def runs(self) -> tuple[RunScore, ...]:
        return self.report.runs if self.report else ()

    @property
    def activated(self) -> list[RunScore]:
        return [r for r in self.runs if r.outcome == "activated"]


def _fmt(value: float | None, unit: str = "", digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}{unit}"


def _at_least(value: float | None, target: float) -> Status:
    return "not measured" if value is None else ("pass" if value >= target else "fail")


def _at_most(value: float | None, target: float) -> Status:
    return "not measured" if value is None else ("pass" if value <= target else "fail")


def _check_row(
    inputs: _Inputs, area: str, target: str, key: str, at_least: float | None = None
) -> Row:
    check = inputs.checks.get(key)
    if check is None or (check.passed is None and check.value is None):
        return Row(
            area=area, target=target, measured=f"not checked (`{key}`)", status="not measured"
        )
    if check.value is not None and at_least is not None:
        status = _at_least(check.value, at_least)
        measured = f"{check.value:g}"
    else:
        status = "pass" if check.passed else "fail"
        measured = "yes" if check.passed else "no"
    if check.evidence:
        measured += f" ({check.evidence})"
    return Row(area=area, target=target, measured=measured, status=status)


def _per_rule(inputs: _Inputs, attr: str, target: float, label: str) -> Row:
    rules = inputs.report.rules if inputs.report else ()
    values = [(r.rule, getattr(r, attr)) for r in rules if getattr(r, attr) is not None]
    target_text = f"{label} ≥ {target:g} per rule"
    if not values:
        why = "no alerts yet" if attr == "precision" else "no labelled positives yet"
        return Row(
            area="Alert quality", target=target_text, measured=f"n/a ({why})", status="not measured"
        )
    measured = ", ".join(f"{rule} {value:.2f}" for rule, value in values)
    status: Status = "pass" if all(v >= target for _, v in values) else "fail"
    return Row(area="Alert quality", target=target_text, measured=measured, status=status)


def _alert_rows(inputs: _Inputs) -> list[Row]:
    area = "Alert quality"
    rules = inputs.report.rules if inputs.report else ()
    negatives = sum(r.negative_clips for r in rules)
    false_alerts = sum(r.false_alerts_on_negatives for r in rules)
    runs = inputs.activated
    decisive = sum(r.decisive for r in runs)
    total = sum(len(r.tier1) for r in runs)
    scored = [r for r in runs if r.scored]
    agree, judged = sum(r.agree for r in scored), sum(r.decisive for r in scored)
    candidates_on_negatives = sum(r.candidates for r in runs if r.status == "negative")
    return [
        _per_rule(inputs, "precision", 0.92, "Precision"),
        _per_rule(inputs, "recall", 0.90, "Recall"),
        Row(
            area=area,
            target="0 false alerts on the negative set",
            measured=(
                f"{false_alerts} on {negatives} negative clip runs "
                f"({candidates_on_negatives} unverified candidates)"
                if negatives
                else "n/a (no negative clips run)"
            ),
            status="not measured" if not negatives else ("pass" if false_alerts == 0 else "fail"),
        ),
        Row(
            area=area,
            target="Glimmer decisive (not unsure) on ≥ 85% of candidates",
            measured=f"{_pct(decisive, total)} ({decisive}/{total})",
            status=_at_least(m.ratio(decisive, total), 0.85),
        ),
        Row(
            area=area,
            target="Glimmer verdict matches the label on ≥ 90% (WS1a)",
            measured=f"{_pct(agree, judged)} ({agree}/{judged} decisive)",
            status=_at_least(m.ratio(agree, judged), 0.90),
        ),
    ]


def _pct(num: int, den: int) -> str:
    value = m.ratio(num, den)
    return "n/a" if value is None else f"{100 * value:.0f}%"


def _realtime_rows(inputs: _Inputs) -> list[Row]:
    area = "Real-time"
    realtime = [r for r in inputs.activated if r.realtime]
    if inputs.live is not None:
        lags, keyframe, source = (
            list(inputs.live.lags_s),
            inputs.live.keyframe_mode,
            inputs.live.source,
        )
    else:
        lags = [x for r in realtime for x in r.lags_s]
        keyframe = any(r.keyframe_mode for r in realtime)
        source = f"{len(realtime)} real-time eval runs"
    latencies = [x for r in realtime for x in r.latencies_s]
    lag_p95 = m.percentile(lags, 95)
    latency_p95 = m.percentile(latencies, 95)
    return [
        Row(
            area=area,
            target="p95 lag ≤ 6 s (2 cameras, 30 min)",
            measured=f"{_fmt(lag_p95, ' s', 1)} over {len(lags)} samples ({source})",
            status=_at_most(lag_p95, 6.0),
        ),
        Row(
            area=area,
            target="p95 alert latency (event start → verified alert) ≤ 10 s",
            measured=f"{_fmt(latency_p95, ' s', 1)} over {len(latencies)} matched alerts",
            status=_at_most(latency_p95, 10.0),
        ),
        Row(
            area=area,
            target="Keyframe mode never triggered",
            measured=("triggered" if keyframe else "never") + f" ({source})",
            status="not measured" if not lags else ("fail" if keyframe else "pass"),
        ),
    ]


def _cost(runs: Sequence[RunScore], inputs: _Inputs) -> float | None:
    config = inputs.report.config if inputs.report else None
    if config is None:
        return None
    return m.usd_per_camera_hour(
        sum(r.billed_frames or 0 for r in runs),
        sum(r.camera_hours for r in runs),
        gpu_usd_per_hour=config.gpu_usd_per_hour,
        cameras_per_gpu=config.cameras_per_gpu,
    )


def _usd(value: float | None) -> str:
    return "n/a" if value is None else f"${value:.3f}"


def _cost_rows(inputs: _Inputs) -> list[Row]:
    area = "Cost"
    metered = [r for r in inputs.activated if r.billed_frames is not None]
    idle = [r for r in metered if r.events == 0]
    active = [r for r in metered if r.events > 0]
    gpu = inputs.report.config.gpu_usd_per_hour if inputs.report else 0.0
    note = "" if gpu else "; GPU not priced (--gpu-usd-per-hour)"
    rows = []
    for runs, label, target in ((idle, "idle", 0.15), (active, "active", 0.60)):
        value = _cost(runs, inputs)
        rows.append(
            Row(
                area=area,
                target=f"≤ ${target:.2f} per camera-hour {label}",
                measured=f"{_usd(value)} over {len(runs)} metered runs{note}",
                status=_at_most(value, target),
            )
        )
    rows.append(_check_row(inputs, area, "Budget alarm works", "budget_alarm"))
    return rows


def _tracking_rows(inputs: _Inputs) -> list[Row]:
    area = "Perception"
    rows = [t for r in inputs.activated for t in r.tracking]
    switches = [t.id_switches_per_min for t in rows if t.id_switches_per_min is not None]
    worst_switch = max(switches, default=None)
    worst_hota = min((t.hota for t in rows), default=None)
    return [
        _check_row(
            inputs, area, "Detection agreement with hosted SAM ≥ 0.95", "sam_agreement", 0.95
        ),
        Row(
            area=area,
            target="ID switches ≤ 1 per minute per camera",
            measured=f"worst {_fmt(worst_switch, '/min')} over {len(rows)} labelled tracks",
            status=_at_most(worst_switch, 1.0),
        ),
        Row(
            area=area,
            target="HOTA ≥ 0.6 on our clips",
            measured=f"worst {_fmt(worst_hota)} over {len(rows)} labelled tracks",
            status=_at_least(worst_hota, 0.6),
        ),
    ]


def _determinism(inputs: _Inputs) -> Row:
    target = f"Same request → same canonical rule {DETERMINISM_RUNS}/{DETERMINISM_RUNS}"
    check = inputs.checks.get("planner_determinism")
    if check is not None and (check.passed is not None or check.value is not None):
        return _check_row(inputs, "Planner", target, "planner_determinism")
    groups: dict[str, list[tuple[str, ...]]] = defaultdict(list)
    for run in inputs.runs:
        if run.rule_hashes:
            groups[run.request].append(run.rule_hashes)
    if not groups:
        return Row(
            area="Planner", target=target, measured="n/a (no compiled rules)", status="not measured"
        )
    parts, statuses = [], []
    for hashes in groups.values():
        same = Counter(hashes).most_common(1)[0][1]
        parts.append(f"{same}/{len(hashes)}")
        if same < len(hashes):
            statuses.append("fail")
        elif len(hashes) < DETERMINISM_RUNS:
            statuses.append("not measured")
    status: Status = "fail" if "fail" in statuses else ("not measured" if statuses else "pass")
    return Row(
        area="Planner",
        target=target,
        measured=f"identical: {', '.join(parts)} (per request; needs {DETERMINISM_RUNS} runs)",
        status=status,
    )


def _planner_rows(inputs: _Inputs) -> list[Row]:
    runs = inputs.runs
    ok = sum(
        1
        for r in runs
        if (r.expect, r.outcome) in {("activatable", "activated"), ("refusal", "refused")}
    )
    return [
        _determinism(inputs),
        Row(
            area="Planner",
            target="100% activatable where the scene allows, honest refusals where it doesn't",
            measured=f"{_pct(ok, len(runs))} ({ok}/{len(runs)} runs as expected)",
            status="not measured" if not runs else ("pass" if ok == len(runs) else "fail"),
        ),
    ]


_CHECK_ROWS: tuple[tuple[str, str, str], ...] = (
    ("Safety and privacy", "Owner login", "owner_login"),
    ("Safety and privacy", "No public route exposes candidate traces", "evidence_routes_private"),
    ("Safety and privacy", "Guardrail tests pass", "guardrail_tests"),
    ("Safety and privacy", "Scene-text firewall live", "scene_text_firewall"),
    (
        "Deployment and ops",
        "One command brings up the stack on a clean account",
        "one_command_deploy",
    ),
    ("Deployment and ops", "Survives a pod restart", "survives_restart"),
    ("Deployment and ops", "Health checks and metrics on a dashboard", "metrics_dashboard"),
    ("Demo", "Recorded golden REPLAY on the site", "golden_replay_live"),
    ("Demo", "Scripted 3-minute live demo rehearsed 3 times", "live_demo_rehearsed"),
)

_SECTIONS: tuple[Callable[[_Inputs], list[Row]], ...] = (
    _alert_rows,
    _realtime_rows,
    _cost_rows,
    _tracking_rows,
    _planner_rows,
)


def build_scorecard(
    *,
    title: str,
    report: EvalReport | None = None,
    checks: Checks | None = None,
    live: LiveSample | None = None,
    sources: Sequence[str] = (),
) -> Scorecard:
    """Assemble every §2 criterion from whatever inputs exist."""
    inputs = _Inputs(report=report, checks=checks or Checks(), live=live)
    rows = [row for section in _SECTIONS for row in section(inputs)]
    rows += [_check_row(inputs, area, target, key) for area, target, key in _CHECK_ROWS]
    return Scorecard(title=title, sources=tuple(sources), rows=tuple(rows), report=report)


AREA_TARGETS: dict[str, str] = {
    "Alert quality": "9.5",
    "Real-time": "9.5",
    "Cost": "9",
    "Perception": "9.5",
    "Planner": "9.5",
    "Safety and privacy": "9.5",
    "Deployment and ops": "9.5",
    "Demo": "10",
}


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _rule_table(report: EvalReport) -> list[str]:
    lines = [
        "| Rule | Clips | TP | FP | FN | Precision | Recall | False alerts on negatives | "
        "Latency p95 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in report.rules:
        lines.append(
            f"| {r.rule} | {r.clips} | {r.tp} | {r.fp} | {r.fn} | {_fmt(r.precision)} | "
            f"{_fmt(r.recall)} | {r.false_alerts_on_negatives} | {_fmt(r.latency_p95_s, ' s', 1)} |"
        )
    return lines


def _run_table(report: EvalReport) -> list[str]:
    lines = [
        "| Clip | Rule | Status | Outcome | Speed | Candidates | Alerts | Events | TP/FP/FN | "
        "Tier 1 decisive | Lag p95 | $/camera-hour |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in report.runs:
        why = r.refused or r.error
        outcome = r.outcome if why is None else f"{r.outcome}: {_cell(why)[:80]}"
        lines.append(
            f"| {r.clip} | {r.rule} | {r.status} | {outcome} | {r.speed:g}x | {r.candidates} | "
            f"{r.alerts} | {r.events} | {r.tp}/{r.fp}/{r.fn} | {r.decisive}/{len(r.tier1)} | "
            f"{_fmt(m.percentile(r.lags_s, 95), ' s', 1)} | {_usd(r.usd_per_camera_hour)} |"
        )
    return lines


def render_markdown(card: Scorecard) -> str:
    """Render the scorecard as the `reports/eval/<date>.md` page."""
    lines = [f"# {card.title}", ""]
    if card.sources:
        lines += [*(f"- {source}" for source in card.sources), ""]
    lines += [
        "Targets are PLAN-9.5 §2. A criterion without data is NOT MEASURED, never a pass.",
        "",
        "## Areas",
        "",
        "| Area | Target score | Result |",
        "|---|---|---|",
    ]
    lines += [
        f"| {area} | {AREA_TARGETS.get(area, '')} | {STATUS_TEXT[card.area_status(area)]} |"
        for area in card.areas
    ]
    lines += ["", "## Criteria", "", "| Area | Target | Measured | Result |", "|---|---|---|---|"]
    lines += [
        f"| {row.area} | {_cell(row.target)} | {_cell(row.measured)} | {STATUS_TEXT[row.status]} |"
        for row in card.rows
    ]
    if card.report is not None and card.report.runs:
        lines += ["", "## Per rule", "", *_rule_table(card.report)]
        lines += ["", "## Per run", "", *_run_table(card.report)]
        config = card.report.config
        lines += [
            "",
            f"Matching: temporal IoU ≥ {config.iou:g}; alerts closer than {config.merge_gap_s:g} s "
            f"merge into one episode; episodes widened to ≥ {config.min_alert_s:g} s. "
            "Latency and lag use real-time runs only.",
        ]
    return "\n".join(lines) + "\n"
