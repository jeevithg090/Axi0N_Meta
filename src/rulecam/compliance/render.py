"""The pack as a printable page (HTML → PDF through Playwright), with the verify QR code."""

from __future__ import annotations

from collections import defaultdict
from typing import Final

from jinja2 import Environment, PackageLoader, select_autoescape

from rulecam.compliance.models import Metric, ProofPack
from rulecam.compliance.narrate import format_value
from rulecam.evidence.packs import qr_data_uri

HEADLINE: Final = (
    "alerts_raised",
    "glimmer_verified",
    "glimmer_rejected",
    "glimmer_unsure",
    "sent_to_person",
    "policy_alerted",
    "actions",
    "actions_dry_run",
    "acks",
    "time_to_verdict_median",
    "time_to_action_median",
    "time_to_action_p90",
    "time_to_ack_median",
    "disputes_filed",
    "disputes_upheld",
    "disputes_rejected",
    "false_alarm_labels",
    "tamper_attempts",
    "plan_approvals",
    "setting_proposals",
    "settings_applied",
    "settings_rejected",
    "settings_without_both_keys",
)
SCOPED: Final = (
    ("forklift", "Near misses (rules that measure a distance, e.g. forklift separation)"),
    ("hazard", "Hazards seen and cleared"),
    ("rule", "By rule"),
    ("shift", "By shift"),
    ("camera", "By camera"),
)
AUDIENCE_NAMES: Final = {
    "inspector": "For the inspector",
    "insurer": "For the insurer",
    "hq": "For head office",
    "worker_rep": "For the crew's representative",
}

_env = Environment(
    loader=PackageLoader("rulecam.compliance", "templates"),
    autoescape=select_autoescape(["html", "j2"]),
    keep_trailing_newline=True,
)


def _fmt(value: float | int | None, unit: str) -> str:
    return format_value(value, unit)


def render_html(pack: ProofPack, pack_hash: str, url: str | None) -> str:
    """The pack as one self-contained, printable HTML page (auto-escaped).

    Args:
        pack: The pack.
        pack_hash: Its hash, printed next to the QR code.
        url: The share link the QR code opens; `None` prints no code (an owner's draft).

    Returns:
        HTML.
    """
    by_id = {m.id: m for m in pack.metrics}
    by_section: dict[str, list[Metric]] = defaultdict(list)
    for metric in pack.metrics:
        if metric.section not in {"alerts", "response", "disputes", "governance"}:
            by_section[metric.section].append(metric)
    length = pack.period.end - pack.period.start
    previous_label = f"{length.days} day(s)" if length.days and not length.seconds else str(length)
    return _env.get_template("proof.html.j2").render(
        pack=pack,
        pack_hash=pack_hash,
        url=url,
        qr=qr_data_uri(url) if url else None,
        headline=[by_id[i] for i in HEADLINE if i in by_id],
        metrics=by_id,
        by_section=by_section,
        scoped=SCOPED,
        fmt=_fmt,
        audience_names=AUDIENCE_NAMES,
        previous_label=previous_label,
    )
