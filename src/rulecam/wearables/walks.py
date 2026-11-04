"""Safety walks: the frames the glasses send between start and finish become one report.

`WalkStore` keeps each walk under `<root>/<walk_id>/` (`walk.json`, `answers.jsonl`,
`report.json`). `WalkReporter` asks Muse Spark to merge the findings into hazards with the
frames that show them, then appends them to the hash-chained evidence log with the runtime's
record shapes, so `GET /incidents` and the one-click incident report cover walk hazards too:
one `candidate` per hazard, a `verdict` when two models verified it live, and one `action`
holding the report.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final

from rulecam.ask.watch import FileAttacher
from rulecam.contracts import Clock, StructuredLLM
from rulecam.errors import RulecamError
from rulecam.evidence.chain import EventChain
from rulecam.log import get_logger
from rulecam.wearables.assist import CAMERA_ID, SPARK_MODEL
from rulecam.wearables.prompts import walk_prompt
from rulecam.wearables.schemas import Mode, PovAnswer, Walk, WalkHazard, WalkReport, WalkSummary

_log = get_logger(__name__)

WALK_ID: Final = re.compile(r"^walk-[0-9A-Za-z-]{6,64}$")
MAX_REPORT_FRAMES: Final = 6
RULE_ID: Final = "safety-walk"


class WalkError(RulecamError):
    """No such walk, or it is already finished."""


def finding_line(answer: PovAnswer) -> str:
    """One answer as a line of findings for the report."""
    parts = [f"{answer.answer_id} at {answer.at.isoformat()} [{answer.kind}]"]
    if answer.question:
        parts.append(f"asked: {answer.question}")
    said = answer.spoken or answer.detail
    if said:
        parts.append(f"said: {said}")
    if answer.hazards:
        hazards = "; ".join(f"{h.what} ({h.where}, {h.severity})" for h in answer.hazards)
        parts.append(f"hazards: {hazards}")
    elif answer.look is not None and answer.look.hazards:
        parts.append(f"first look hazards: {'; '.join(answer.look.hazards)}")
    if answer.alert:
        backing = ", ".join(answer.verified_by)
        parts.append(f"alert verified by {backing}" if answer.verified else "alert NOT verified")
    if answer.gaps:
        gaps = "; ".join(
            f"{g.section}: ~{g.facings} facings by {g.neighbours}" for g in answer.gaps
        )
        parts.append(f"shelf gaps: {gaps}")
    return " | ".join(parts)


def notable(answers: Sequence[PovAnswer]) -> list[PovAnswer]:
    """The answers whose frames Spark should see: alerts first, then hazards, then gaps."""
    ranked = sorted(
        (a for a in answers if a.alert or a.hazards or a.gaps or (a.look and a.look.hazards)),
        key=lambda a: (not a.verified, not a.alert, not a.hazards),
    )
    return ranked[:MAX_REPORT_FRAMES]


class WalkStore:
    """Keeps safety walks on disk."""

    def __init__(self, root: Path, *, clock: Clock) -> None:
        """Create the store under `root` (`<data>/wearables/walks`)."""
        self._root = root
        self._clock = clock
        self._lock = threading.Lock()

    def start(self, mode: Mode, note: str = "") -> Walk:
        """Start a walk."""
        now = self._clock.now()
        walk = Walk(
            walk_id=f"walk-{now:%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}",
            mode=mode,
            note=note[:500],
            started_at=now,
        )
        self._write(walk)
        return walk

    def get(self, walk_id: str) -> Walk:
        """The walk.

        Raises:
            WalkError: When there is no such walk.
        """
        path = self._dir(walk_id) / "walk.json"
        if not path.is_file():
            msg = f"no walk {walk_id}"
            raise WalkError(msg)
        return Walk.model_validate_json(path.read_text(encoding="utf-8"))

    def add(self, walk_id: str, answer: PovAnswer) -> None:
        """Append an answer to an open walk.

        Raises:
            WalkError: When there is no such walk or it is finished.
        """
        with self._lock:
            walk = self.get(walk_id)
            if walk.finished_at is not None:
                msg = f"walk {walk_id} is finished"
                raise WalkError(msg)
            with (self._dir(walk_id) / "answers.jsonl").open("a", encoding="utf-8") as out:
                out.write(answer.model_dump_json() + "\n")
            self._write(walk.model_copy(update={"frames": walk.frames + 1}))

    def answers(self, walk_id: str) -> list[PovAnswer]:
        """The walk's answers, oldest first."""
        path = self._dir(walk_id) / "answers.jsonl"
        if not path.is_file():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        return [PovAnswer.model_validate_json(line) for line in lines if line.strip()]

    def finish(self, walk_id: str) -> Walk:
        """Mark the walk finished (idempotent) and return it."""
        with self._lock:
            walk = self.get(walk_id)
            if walk.finished_at is None:
                walk = walk.model_copy(update={"finished_at": self._clock.now()})
                self._write(walk)
            return walk

    def save_report(self, report: WalkReport) -> None:
        """Keep the finished walk's report."""
        path = self._dir(report.walk.walk_id) / "report.json"
        path.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    def report(self, walk_id: str) -> WalkReport | None:
        """The walk's report, if it was finished."""
        path = self._dir(walk_id) / "report.json"
        if not path.is_file():
            return None
        return WalkReport.model_validate_json(path.read_text(encoding="utf-8"))

    def _dir(self, walk_id: str) -> Path:
        if not WALK_ID.match(walk_id):
            msg = f"no walk {walk_id[:80]}"
            raise WalkError(msg)
        return self._root / walk_id

    def _write(self, walk: Walk) -> None:
        folder = self._dir(walk.walk_id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "walk.json").write_text(walk.model_dump_json(indent=2), encoding="utf-8")


class WalkReporter:
    """Turns a walk's answers into a report and chains its hazards as evidence."""

    def __init__(
        self,
        *,
        spark: StructuredLLM,
        files: FileAttacher,
        chain: EventChain | None,
        frame_path: Callable[[str], Path],
        site_profile: str = "",
        monotonic: Callable[[], float] = time.perf_counter,
    ) -> None:
        """Create the reporter.

        Args:
            spark: Muse Spark 1.3.
            files: Uploads frames for Spark.
            chain: The evidence chain; `None` writes no records.
            frame_path: The local path of a frame or marked frame by its name.
            site_profile: One line about the site for the prompt.
            monotonic: Timer (injected in tests).
        """
        self._spark = spark
        self._files = files
        self._chain = chain
        self._frame_path = frame_path
        self._site = site_profile
        self._monotonic = monotonic

    def report(self, walk: Walk, answers: Sequence[PovAnswer]) -> WalkReport:
        """Write the walk's report and chain its hazards.

        Args:
            walk: The finished walk.
            answers: Its answers, oldest first.

        Returns:
            The report, with the hashes of the chain records it wrote.
        """
        started = self._monotonic()
        summary = (
            self._summarise(walk, answers)
            if answers
            else WalkSummary(
                summary="No frames were sent during this walk.", spoken="The walk had no frames."
            )
        )
        hashes = self._chain_records(walk, summary, answers)
        _log.info("wearables_walk_report", walk_id=walk.walk_id, hazards=len(summary.hazards))
        return WalkReport(
            walk=walk,
            summary=summary,
            answers=list(answers),
            chain_hashes=hashes,
            model=SPARK_MODEL,
            ms=round((self._monotonic() - started) * 1000),
        )

    def _summarise(self, walk: Walk, answers: Sequence[PovAnswer]) -> WalkSummary:
        findings = "\n".join(finding_line(a) for a in answers)
        intro = f"Walk {walk.walk_id}, {len(answers)} frames." + (
            f" Note from the wearer: {walk.note}" if walk.note else ""
        )
        content: list[dict[str, object]] = [
            {"type": "input_text", "text": f"{intro}\nFindings in order:\n{findings}"}
        ]
        for answer in notable(answers):
            content.append({"type": "input_text", "text": f"Frame of {answer.answer_id}:"})
            content.append(self._files.attach(self._frame_path(answer.marked or answer.frame)))
        return self._spark.structured(
            model=SPARK_MODEL,
            messages=[
                {"role": "system", "content": walk_prompt(walk.mode, self._site)},
                {"role": "user", "content": content},
            ],
            schema=WalkSummary,
        )

    def _chain_records(
        self, walk: Walk, summary: WalkSummary, answers: Sequence[PovAnswer]
    ) -> list[str]:
        if self._chain is None:
            return []
        by_id = {a.answer_id: a for a in answers}
        chain = self._chain
        hashes = [
            chain_hazard(
                chain,
                walk,
                number=k,
                hazard=hazard,
                shown=[by_id[i] for i in hazard.answer_ids if i in by_id],
                frame_path=self._frame_path,
            )
            for k, hazard in enumerate(summary.hazards, 1)
        ]
        record = chain.append(
            "action",
            {
                "event_id": walk.walk_id,
                "kind": "report",
                "role": "supervisor",
                "detail": f"safety walk report: {summary.summary}",
                "ok": True,
                "frames": len(answers),
                "hazards": len(summary.hazards),
                "restock": list(summary.restock),
                "models": [SPARK_MODEL],
            },
        )
        return [*hashes, record.hash]


def chain_hazard(
    chain: EventChain,
    walk: Walk,
    *,
    number: int,
    hazard: WalkHazard,
    shown: Sequence[PovAnswer],
    frame_path: Callable[[str], Path],
) -> str:
    """Append one walk hazard as a `candidate` (and a `verdict` when it was verified live).

    Returns:
        The candidate record's hash.
    """
    event_id = f"{walk.walk_id}-h{number}"
    times = sorted(a.at for a in shown) or [walk.finished_at or walk.started_at]
    verified = [a for a in shown if a.verified]
    record = chain.append(
        "candidate",
        {
            "candidate": {
                "event_id": event_id,
                "rule_id": RULE_ID,
                "camera_id": CAMERA_ID,
                "severity": hazard.severity,
                "ts_start": times[0].isoformat(),
                "ts_end": times[-1].isoformat(),
                "measurement": None,
                "strip_paths": [str(frame_path(a.marked or a.frame)) for a in shown],
            },
            "context": {
                "rule": {
                    "source": {
                        "text": f"Safety walk ({walk.mode}): {hazard.what}",
                        "standard": ["Safety walk with smart glasses"],
                    }
                }
            },
            "hazard": hazard.model_dump(mode="json"),
            "models": sorted({m for a in shown for m in a.verified_by} | {SPARK_MODEL}),
        },
    )
    if verified:
        chain.append(
            "verdict",
            {
                "event_id": event_id,
                "tier": " + ".join(verified[0].verified_by),
                "verdict": "confirmed",
                "score": verified[0].confidence,
                "reason": verified[0].detail[:500],
            },
        )
    return record.hash
