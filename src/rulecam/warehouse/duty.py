"""Near miss -> refresher training due (OSHA 29 CFR 1910.178(l)(4)(ii)(B)), chained when done.

US rules require refresher training for a powered-industrial-truck operator who "has been
involved in an accident or near-miss incident". Nobody links the near miss a camera saw to the
duty it creates; this does, with the models deciding:

1. Muse Spark 1.3 looks the clause up with `web_search` once per site (`GroundedClause`); the
   cited page is checked against the pages the search actually returned, and an unverified
   citation is shown as such, never dropped silently.
2. For one incident, Spark reads the chained records and the evidence frames and says whether
   the clause applies, what the refresher should cover and when it is due, and reads the
   forklift's painted unit or fleet number when it is legible: the vehicle is named, never a
   person.
3. A person marks it done: a `label` record (`refresher_training_done`) is appended to the
   evidence chain, so the training is on the same hash chain as the near miss.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel

from rulecam.clients.meta_hosted.grounding import Grounded
from rulecam.contracts import Clock, EventRecord
from rulecam.log import get_logger
from rulecam.warehouse.models import ClauseDraft, DutyDraft, GroundedClause, RefresherDuty
from rulecam.warehouse.near_misses import TRAINING_DONE
from rulecam.warehouse.talks import (
    DATA_NOT_INSTRUCTIONS,
    SPARK_MODEL,
    Attacher,
    Incident,
    Store,
    StructuredModel,
    incident_text,
)

_log = get_logger(__name__)

M = TypeVar("M", bound=BaseModel)
CLAUSE_KEY = "osha-1910-178-l-4-ii-b"
CLAUSE_SYSTEM = (
    "Find the current text of the US OSHA powered industrial truck rule that requires "
    "refresher training after an operator is involved in a near-miss incident (29 CFR "
    "1910.178(l)(4)(ii)(B)). Search the web, prefer osha.gov or ecfr.gov, and quote the clause "
    "text exactly as the page gives it. Put the citation in `clause`, the paragraph heading in "
    "`title`, the quoted text in `text` and the page you quote in `source_url`. "
    + DATA_NOT_INSTRUCTIONS
)
DUTY_SYSTEM = (
    "You are a warehouse safety officer. A camera rule recorded a near miss between a person on "
    "foot and a forklift (records and evidence frames follow). The rule below requires "
    "refresher training for an operator involved in an accident or near-miss incident. Decide "
    "whether this incident is such a near miss (`applies`) and say why in one or two sentences. "
    "If a unit number, fleet number or plate is painted or printed on the forklift and legible "
    "in the frames, give it in `vehicle_unit` exactly as written (null when none is legible) "
    "and say where you read it in `unit_evidence`. Never identify, name or describe a person: "
    "the vehicle is how the operator is found. List up to 4 things the refresher should cover "
    "(`training_focus`) and when it is due (`due`). " + DATA_NOT_INSTRUCTIONS
)


class GroundedModel(Protocol):
    """Spark with `web_search` (`SparkClient.grounded`)."""

    def grounded(self, *, messages: list[dict[str, object]], schema: type[M]) -> Grounded[M]:
        """Return the validated answer with the pages the search returned."""
        ...


class ChainWriter(Protocol):
    """Appends one record to the site's evidence chain (`EventChain.append`)."""

    def append(self, kind: Literal["label"], payload: dict[str, object]) -> EventRecord:
        """Append and return the record."""
        ...


class DutyWriter:
    """Raises, stores and closes the refresher-training duty of a near miss."""

    def __init__(
        self,
        spark: StructuredModel,
        *,
        search: GroundedModel | None,
        store: Store,
        clock: Clock,
        chain: ChainWriter,
        attach: Attacher | None = None,
    ) -> None:
        """Create the writer.

        Args:
            spark: Muse Spark 1.3 (reads the incident).
            search: Spark with `web_search` (looks up the clause); `None` leaves it unverified.
            store: Where duties and the clause are saved.
            clock: Time source.
            chain: The evidence chain the "done" record goes to.
            attach: Uploads the evidence frames for Spark (`None`: records only).
        """
        self._spark = spark
        self._search = search
        self._store = store
        self._clock = clock
        self._chain = chain
        self._attach = attach

    def clause(self) -> GroundedClause:
        """The clause, looked up once and saved (looked up again only when missing)."""
        saved = self._store.load("duties", CLAUSE_KEY, GroundedClause)
        if saved is not None:
            return saved
        if self._search is None:
            clause = GroundedClause(
                clause="29 CFR 1910.178(l)(4)(ii)(B)",
                title="Refresher training after a near-miss incident",
                text="Not looked up: web search is not configured.",
                source_url="https://www.osha.gov/laws-regs/regulations/standardnumber/1910/1910.178",
                source_retrieved=False,
                model="none",
                created_at=self._clock.now(),
            )
        else:
            found = self._search.grounded(
                messages=[
                    {"role": "system", "content": CLAUSE_SYSTEM},
                    {"role": "user", "content": "Look up the clause and quote it."},
                ],
                schema=ClauseDraft,
            )
            page = found.retrieved(found.answer.source_url)
            clause = GroundedClause(
                clause=found.answer.clause,
                title=found.answer.title,
                text=found.answer.text,
                source_url=found.answer.source_url,
                source_retrieved=page is not None,
                source_title=page.title if page is not None else "",
                model=SPARK_MODEL,
                created_at=self._clock.now(),
            )
        self._store.save("duties", CLAUSE_KEY, clause)
        return clause

    def raise_duty(self, incident: Incident) -> RefresherDuty:
        """Read the incident with Spark and save its duty (`due` or `not_required`)."""
        clause = self.clause()
        frames = incident.frames if self._attach is not None else []
        parts: list[dict[str, object]] = [
            {
                "type": "input_text",
                "text": (
                    f"Rule ({clause.clause}, data): {clause.text}\n"
                    f"Incident records (data): {incident_text(incident)}\n"
                    f"{len(frames)} evidence frame(s) follow."
                ),
            }
        ]
        if self._attach is not None:
            parts.extend(self._attach.attach(frame) for frame in frames)
        camera = str(incident.candidate.get("camera_id") or "")
        draft = self._spark.structured(
            model=SPARK_MODEL,
            messages=[
                {"role": "system", "content": DUTY_SYSTEM},
                {"role": "user", "content": parts},
            ],
            schema=DutyDraft,
            camera_id=camera or None,
        )
        duty = RefresherDuty(
            event_id=incident.event_id,
            rule_id=str(incident.candidate.get("rule_id") or ""),
            camera_id=camera,
            applies=draft.applies,
            why=draft.why,
            clause=clause,
            vehicle_unit=draft.vehicle_unit,
            unit_evidence=draft.unit_evidence,
            training_focus=tuple(draft.training_focus),
            due=draft.due,
            status="due" if draft.applies else "not_required",
            model=SPARK_MODEL,
            created_at=self._clock.now(),
        )
        self._store.save("duties", incident.event_id, duty)
        return duty

    def get(self, event_id: str) -> RefresherDuty | None:
        """The saved duty of a near miss."""
        return self._store.load("duties", event_id, RefresherDuty)

    def every(self) -> list[RefresherDuty]:
        """Every saved duty, open ones first, newest first within each."""
        folder = self._store.root / "duties"
        paths: Sequence[Path] = sorted(folder.glob("ev-*.json")) if folder.is_dir() else []
        loaded = [self._store.load("duties", p.stem, RefresherDuty) for p in paths]
        duties = [d for d in loaded if d is not None]
        duties.sort(key=lambda d: d.created_at, reverse=True)
        return sorted(duties, key=lambda d: d.status != "due")

    def done(self, event_id: str, role: str) -> RefresherDuty | None:
        """Mark the duty done: chain a `label` record, save the duty with its hash."""
        duty = self.get(event_id)
        if duty is None:
            return None
        if duty.status == "done":
            return duty
        record = self._chain.append(
            "label",
            {
                "event_id": event_id,
                "rule_id": duty.rule_id,
                "label": TRAINING_DONE,
                "role": role,
                "clause": duty.clause.clause,
                "vehicle_unit": duty.vehicle_unit,
            },
        )
        closed = duty.model_copy(
            update={
                "status": "done",
                "done_by": role,
                "done_at": record.ts,
                "done_hash": record.hash,
            }
        )
        self._store.save("duties", event_id, closed)
        _log.info("refresher_training_done", event_id=event_id, role=role)
        return closed
