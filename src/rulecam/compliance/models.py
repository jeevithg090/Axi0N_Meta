"""The proof pack's shape: what an inspector, insurer, HQ or worker representative receives."""

from __future__ import annotations

from datetime import datetime
from typing import Final, Literal

from pydantic import Field

from rulecam.compliance.engine import MetricSpec, Period, Shift
from rulecam.compliance.proofs import ProofStep
from rulecam.contracts import Frozen, PrivacyMode

PACK_FORMAT: Final = "rulecam-proof-pack/1"
Audience = Literal["inspector", "insurer", "hq", "worker_rep"]
AUDIENCES: Final[tuple[Audience, ...]] = ("inspector", "insurer", "hq", "worker_rep")
Unit = Literal["count", "seconds"]
Section = Literal[
    "alerts", "response", "disputes", "governance", "rule", "shift", "camera", "forklift", "hazard"
]
DISCLAIMER: Final = (
    "Evidence for the period shown, computed from the site's tamper-evident log. It is not a "
    "certificate of legal compliance: an inspector, insurer or court decides what it proves."
)


class Clause(Frozen):
    """The clause a rule gives evidence for, with a quote that was found on the cited page.

    `source` says who named it: the owner's plan, or Muse Spark 1.3 searching the web for the
    rule's text when the plan cited none. `checked` is True only when the quote was found on the
    page by fetching it (`planner.standards.check_citation`).
    """

    ref: str
    url: str
    quote: str
    checked: bool
    detail: str
    source: Literal["plan", "muse-spark-1.3"]


class RuleInForce(Frozen):
    """One rule that was enforced during the period, with how it was proved before going live.

    `gate` and `gate_reasons` come from the latest chained `plan_approved` record that lists the
    rule (the acceptance gate, Rule CI included: `pass` or `amber` with reasons), so the browser
    checks them; `ci_detail` is the plan file's acceptance trace (`ci_green`), not chained.
    """

    rule_id: str
    text: str
    severity: str
    plan_ids: tuple[str, ...]
    approvals: tuple[str, ...]
    gate: str
    gate_reasons: tuple[str, ...] = ()
    ci_detail: str | None = None
    active_s: float = Field(ge=0)
    standards: tuple[str, ...] = ()
    clause: Clause | None = None
    clause_note: str = ""
    distance_rule: bool = False


class Metric(Frozen):
    """One number, the recipe it follows, and the records it was computed from (both windows)."""

    id: str
    section: Section
    label: str
    unit: Unit
    spec: MetricSpec
    value: float | int | None
    previous: float | int | None
    refs: tuple[str, ...]
    previous_refs: tuple[str, ...]


class DayProof(Frozen):
    """One chain day: its Merkle root over the first `n` records when the pack was made.

    `committed_root` / `committed_n` are the day's root in `evidence/roots/<day>.txt` (committed
    to git) when there is one.
    """

    day: str
    n: int = Field(ge=0)
    root: str
    chain_ok: bool
    reason: str = ""
    committed_root: str | None = None
    committed_n: int | None = None
    window_first: int | None = None
    window_last: int | None = None


class DisclosedRecord(Frozen):
    """A chain record the pack discloses, with its Merkle inclusion proof to its day's root."""

    key: str
    day: str
    record: dict[str, object]
    proof: tuple[ProofStep, ...]


class Incident(Frozen):
    """An alert in the period that Glimmer confirmed or that the policy sent on as an alert."""

    event_id: str
    key: str
    rule_id: str
    camera_id: str
    severity: str
    ts: datetime
    verdict: str | None
    route: str | None


class Still(Frozen):
    """An anonymised still of an incident (face-blurred or skeleton, by privacy mode).

    Stills are illustrations, not chain records: `sha256` lets a reader match the image with the
    file the runtime wrote, but the chain does not commit to it.
    """

    event_id: str
    kind: Literal["skeleton", "masked"]
    sha256: str
    data_uri: str


class Summary(Frozen):
    """Muse Spark's short summary for one audience.

    `template` holds `{metric_id}` (or `{metric_id.previous}`) placeholders where numbers go;
    `text` is the template with the pack's values filled in. Spark writes the words; every number
    comes from the pack, and a sentence with a digit outside a placeholder is dropped.
    """

    audience: Audience
    lang: str
    template: tuple[str, ...]
    text: tuple[str, ...]
    model: str
    dropped: int = 0


class ProofPack(Frozen):
    """Everything a proof pack holds. Its SHA-256 (canonical JSON) is the pack hash."""

    format: str = PACK_FORMAT
    pack_id: str
    site: str
    created_at: datetime
    title: str
    period: Period
    audiences: tuple[Audience, ...]
    crew_lang: str = "en"
    privacy_mode: PrivacyMode
    rules: tuple[RuleInForce, ...]
    metrics: tuple[Metric, ...]
    incidents: tuple[Incident, ...]
    days: tuple[DayProof, ...]
    records: tuple[DisclosedRecord, ...]
    summaries: tuple[Summary, ...] = ()
    stills: tuple[Still, ...] = ()
    notes: tuple[str, ...] = ()
    disclaimer: str = DISCLAIMER


PackStatus = Literal["building", "ready", "failed"]


class PackEntry(Frozen):
    """A stored pack: its status while Spark works, then the pack and its hash."""

    pack_id: str
    status: PackStatus
    created_at: datetime
    site: str
    title: str
    error: str | None = None
    pack: ProofPack | None = None
    pack_hash: str | None = None


class PackRequest(Frozen):
    """Body of `POST /proof/packs`: the period, who it is for, and whether Spark takes part."""

    start: datetime
    end: datetime
    title: str = Field(default="", max_length=120)
    audiences: tuple[Audience, ...] = AUDIENCES
    crew_lang: str = Field(default="en", pattern=r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")
    shifts: tuple[Shift, ...] | None = None
    models: bool = True
    ground_rules: int = Field(default=6, ge=0, le=12)


class ShareRequest(Frozen):
    """Body of `POST /proof/packs/{id}/share`."""

    hours: float = Field(default=72, gt=0, le=24 * 90)
    ui_origin: str | None = Field(default=None, pattern=r"^https?://[A-Za-z0-9.\-]+(:[0-9]{1,5})?$")


class ShareLink(Frozen):
    """A signed, expiring link to one pack, with its QR code."""

    pack_id: str
    pack_hash: str
    token: str
    url: str
    expires_at: datetime
    qr: str


class SharedPack(Frozen):
    """Body of the public `GET /proof/shared/{token}`."""

    pack: ProofPack
    pack_hash: str
    expires_at: datetime
