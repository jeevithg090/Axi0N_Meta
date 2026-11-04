"""The supermarket use case in one service: checks, watches, staff alerts, advice, the brief.

The story it serves, end to end:

1. the owner asks a question (`POST /ask`); the answer and its evidence land in the Ask run log;
2. `draft_advice` turns that answer into a Site Advisor card with a pre-registered check;
3. `accept` pre-registers the check on the evidence chain and turns on a standing watch;
4. the watch runs its check on the camera every few seconds (`tick`), and staff get an alert
   with the marked photo whenever the models say to act (`StaffDesk`);
5. `brief` writes the owner's brief from everything stored, including the check's progress.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from concurrent.futures import Executor, ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final, Literal, Protocol

from rulecam.advisor.api import AdviceStore
from rulecam.advisor.ledger import AdviceLedger, PreregistrationError
from rulecam.advisor.recompute import NumberCheck, RecomputeReport
from rulecam.contracts import Clock
from rulecam.evidence.chain import EventChain
from rulecam.log import get_logger
from rulecam.retail.advice import AdviceWriter, Receipt, RetailCard, latest_runs, receipt
from rulecam.retail.brief import BriefWriter, day_stats, since_day
from rulecam.retail.checks import RetailError, new_id
from rulecam.retail.models import Brief, CheckKind, Finding, StaffTask, Watch, WatchRequest
from rulecam.retail.proof import ProofChecker
from rulecam.retail.staff import StaffDesk
from rulecam.retail.store import FindingLog, TaskBoard, WatchBook, _JsonBook, minutes

_log = get_logger(__name__)

LIVE_LAG_S: Final = 2.0  # check this far behind the newest recorded instant (its chunk is whole)
WORKERS: Final = 3


class Checker(Protocol):
    """One check on a camera's recorded instant."""

    def check(self, camera_id: str, at: datetime, watch: Watch | None = None) -> Finding:
        """Run the check."""
        ...


class ImageChecker(Protocol):
    """One check on an uploaded image."""

    def check_image(self, image: Path) -> Finding:
        """Run the check on a photo."""
        ...


class CameraFrames(Protocol):
    """Which cameras have recorded video (`ask.vision.FrameGrabber.cameras`)."""

    def cameras(self) -> dict[str, tuple[datetime, datetime]]:
        """Camera id -> (first, last) recorded instant."""
        ...


class CardBook(_JsonBook[RetailCard]):
    """The retail advice cards (`cards.json`)."""

    def __init__(self, path: Path) -> None:
        """Load `path` (a missing file is an empty book)."""
        super().__init__(path, RetailCard, lambda c: c.recommendation.id)


class RetailService:
    """Runs the supermarket checks, watches, advice and brief."""

    def __init__(
        self,
        *,
        checkers: dict[CheckKind, Checker],
        image_checkers: dict[CheckKind, ImageChecker],
        frames: CameraFrames,
        findings: FindingLog,
        watches: WatchBook,
        tasks: TaskBoard,
        desk: StaffDesk,
        cards: CardBook,
        writer: AdviceWriter,
        briefs: BriefWriter,
        advice: AdviceStore,
        chain: EventChain | None,
        clock: Clock,
        ask_runs: Path,
        camera_names: dict[str, str],
        site_profile: str,
        proof: ProofChecker | None = None,
        image_path: Callable[[str], Path | None] | None = None,
        pool: Executor | None = None,
    ) -> None:
        """Create the service (see `rulecam.retail.build.build_retail` for the wiring)."""
        self._checkers = checkers
        self._image_checkers = image_checkers
        self._frames = frames
        self.findings = findings
        self.watches = watches
        self.tasks = tasks
        self.desk = desk
        self.cards = cards
        self._writer = writer
        self._briefs = briefs
        self._advice = advice
        self._ledger = AdviceLedger(chain) if chain is not None else None
        self._clock = clock
        self._runs = ask_runs
        self.camera_names = camera_names
        self._profile = site_profile
        self._pool = pool or ThreadPoolExecutor(
            max_workers=WORKERS, thread_name_prefix="retail-watch"
        )
        self._running: set[str] = set()
        self._lock = threading.Lock()
        self.last_brief: Brief | None = None
        self._proof = proof
        self._image = image_path

    def now(self) -> datetime:
        """The service clock's time."""
        return self._clock.now()

    def close(self) -> None:
        """Wait for running watch checks and stop the worker threads."""
        self._pool.shutdown(wait=True)

    # -- cameras -------------------------------------------------------------------------------

    def cameras(self) -> list[dict[str, object]]:
        """Every camera with recorded video: id, the owner's name, first and last instant."""
        return [
            {
                "camera_id": cam,
                "name": self.camera_names.get(cam, cam),
                "first": first.isoformat(),
                "last": last.isoformat(),
            }
            for cam, (first, last) in self._frames.cameras().items()
        ]

    def latest(self, camera_id: str) -> datetime:
        """The newest whole recorded instant of a camera.

        Raises:
            RetailError: When the camera has recorded nothing.
        """
        span = self._frames.cameras().get(camera_id)
        if span is None:
            msg = f"camera {camera_id} has no recorded video"
            raise RetailError(msg)
        return max(span[0], span[1] - timedelta(seconds=LIVE_LAG_S))

    # -- checks --------------------------------------------------------------------------------

    def run_check(
        self,
        kind: CheckKind,
        camera_id: str,
        at: datetime | None = None,
        *,
        watch: Watch | None = None,
        role: str | None = None,
    ) -> Finding:
        """Run one check now, store it, and alert staff when the models say to act.

        Raises:
            RetailError: Without recorded video there, or for a kind with no checker.
        """
        checker = self._checkers.get(kind)
        if checker is None:
            msg = f"no {kind} check is available"
            raise RetailError(msg)
        instant = at or self.latest(camera_id)
        finding = checker.check(camera_id, instant, watch)
        if not finding.act:
            self._close_seen_fixed(finding)
        finding = self.desk.dispatch(finding, role or (watch.role if watch else None))
        return self.findings.add(finding)

    def _close_seen_fixed(self, finding: Finding) -> None:
        """A clean re-check of a camera closes its open tasks of that kind: seen fixed."""
        for task in self.tasks_for(open_only=True):
            if task.kind == finding.kind and task.camera_id == finding.camera_id:
                self._close(
                    task,
                    "camera",
                    {"finding_id": finding.id, "headline": finding.headline},
                    finding.at or finding.checked_at,
                )

    def run_image(self, kind: CheckKind, image: Path, role: str | None = None) -> Finding:
        """Run a check on a staff photo.

        Raises:
            RetailError: For a kind with no photo check, or an unreadable image.
        """
        checker = self._image_checkers.get(kind)
        if checker is None:
            msg = f"no {kind} photo check is available"
            raise RetailError(msg)
        finding = self.desk.dispatch(checker.check_image(image), role)
        return self.findings.add(finding)

    def history(self, camera_id: str, kind: str) -> list[Finding]:
        """Earlier findings of `kind` on a camera, newest first."""
        return [f for f in self.findings.recent(limit=200, kind=kind) if f.camera_id == camera_id]

    # -- watches -------------------------------------------------------------------------------

    def add_watch(self, request: WatchRequest, advice_id: str | None = None) -> Watch:
        """Turn on a standing watch."""
        watch = Watch(
            id=new_id("w"),
            kind=request.kind,
            camera_id=request.camera_id,
            every_s=request.every_s,
            role=request.role,
            focus=request.focus,
            advice_id=advice_id,
            created_at=self._clock.now(),
        )
        _log.info("retail_watch_on", watch_id=watch.id, kind=watch.kind, camera_id=watch.camera_id)
        return self.watches.put(watch)

    def stop_watch(self, watch_id: str) -> Watch | None:
        """Turn a watch off (it is kept, inactive)."""
        watch = self.watches.get(watch_id)
        if watch is None:
            return None
        return self.watches.put(watch.model_copy(update={"active": False}))

    def run_watch(self, watch_id: str) -> Finding:
        """Run a watch's check now (also what the ticker does when it is due).

        Raises:
            RetailError: For an unknown watch or a camera without video.
        """
        watch = self.watches.get(watch_id)
        if watch is None:
            msg = f"no watch {watch_id}"
            raise RetailError(msg)
        self.watches.put(watch.model_copy(update={"last_run": self._clock.now()}))
        return self.run_check(watch.kind, watch.camera_id, watch=watch)

    def tick(self) -> int:
        """Start every due watch in the background; never blocks the ticker loop.

        Returns:
            How many watch runs were started.
        """
        now = self._clock.now()
        started = 0
        for watch in self.watches.all():
            if not watch.active:
                continue
            due = watch.last_run is None or now - watch.last_run >= timedelta(seconds=watch.every_s)
            with self._lock:
                if not due or watch.id in self._running:
                    continue
                self._running.add(watch.id)
            self.watches.put(watch.model_copy(update={"last_run": now}))
            self._pool.submit(self._background, watch.id)
            started += 1
        return started

    def _background(self, watch_id: str) -> None:
        try:
            watch = self.watches.get(watch_id)
            if watch is not None:
                self.run_check(watch.kind, watch.camera_id, watch=watch)
        except Exception:  # a failed run is logged; the next tick tries again
            _log.exception("retail_watch_failed", watch_id=watch_id)
        finally:
            with self._lock:
                self._running.discard(watch_id)

    # -- restock tasks -------------------------------------------------------------------------

    def tasks_for(self, role: str | None = None, *, open_only: bool = False) -> list[StaffTask]:
        """Restock tasks, newest first, optionally for one role or only the open ones."""
        tasks = sorted(self.tasks.all(), key=lambda t: t.created_at, reverse=True)
        return [
            t
            for t in tasks
            if (role is None or t.role == role)
            and (not open_only or t.status in {"open", "on_my_way"})
        ]

    def prove(self, task_id: str, photo: Path) -> StaffTask:
        """Close a restock task with a photo, if the models agree every gap is filled.

        Raises:
            RetailError: For an unknown task, one without a before image, or no proof checker.
        """
        task = self.tasks.get(task_id)
        if task is None:
            msg = f"no task {task_id}"
            raise RetailError(msg)
        before = self._image(task.marked) if self._image and task.marked else None
        if before is None or self._proof is None:
            msg = "this task has no marked before-image to compare with"
            raise RetailError(msg)
        result = self._proof.check(before, photo, task.kind)
        proof = result.model_dump(mode="json")
        _log.info("retail_task_proof", task_id=task_id, closed=result.closed)
        if result.closed:
            return self._close(task, "photo", proof, self._clock.now(), photo.name)
        return self.tasks.put(task.model_copy(update={"proof_image": photo.name, "proof": proof}))

    def _close(
        self,
        task: StaffTask,
        how: Literal["photo", "camera"],
        proof: dict[str, object],
        at: datetime,
        photo: str | None = None,
    ) -> StaffTask:
        """Close a task as fixed and write the fix, with its time to fix, to the chain."""
        took = minutes(task, at)
        seq = self.desk.record_fixed(task, how=how, minutes_to_fix=took, at=at)
        return self.tasks.put(
            task.model_copy(
                update={
                    "status": "done",
                    "done_at": at,
                    "closed_by": how,
                    "minutes_to_fix": took,
                    "proof": proof,
                    "proof_image": photo or task.proof_image,
                    "fixed_seq": seq,
                }
            )
        )

    # -- advice --------------------------------------------------------------------------------

    def receipt(self, sha: str) -> Receipt:
        """An Ask answer's receipt with the text that was hashed.

        Raises:
            RetailError: For an unknown receipt.
        """
        return receipt(self._runs, sha)

    def asks(self, limit: int = 20) -> list[dict[str, object]]:
        """The owner's newest questions with their receipts."""
        return latest_runs(self._runs, limit)

    def draft_advice(self, receipt: str, camera_id: str | None = None) -> RetailCard:
        """Draft an advice card from an Ask answer (and show it on `/advice` too).

        Raises:
            RetailError: When the answer is not in the run log or yields no grounded card.
        """
        history = self.history(camera_id, "queue") if camera_id else []
        card = self._writer.draft(receipt, history)
        self.cards.put(card)
        checks = tuple(
            NumberCheck(
                result_id=n.id,
                metric=n.label,
                catalogue_value=n.value,
                unit=n.unit,
                recomputed=n.value if n.grounded else None,
                sql=f"matched in Ask run {receipt[:12]}" if n.grounded else "",
                ok=n.grounded,
                error="" if n.grounded else "not found in the Ask run",
            )
            for n in card.numbers
        )
        report = RecomputeReport(recommendation_id=card.recommendation.id, checks=checks)
        self._advice.add(card.recommendation, report)
        return card

    def accept(self, card_id: str) -> RetailCard:
        """Pre-register the card's check on the chain, then turn on its standing watch.

        Raises:
            RetailError: For an unknown card, no evidence chain, or a card already accepted.
        """
        card = self.cards.get(card_id)
        if card is None:
            msg = f"no advice card {card_id}"
            raise RetailError(msg)
        if self._ledger is None:
            msg = "no evidence chain to pre-register on"
            raise RetailError(msg)
        try:
            rec, plan, record = self._ledger.accept(card.recommendation, card.plan)
        except PreregistrationError as exc:
            raise RetailError(str(exc)) from exc
        watch = self.add_watch(
            WatchRequest(
                kind=card.watch.kind,
                camera_id=card.watch.camera_id,
                every_s=card.watch.every_s,
                role=card.watch.role,
                focus=card.watch.focus,
            ),
            advice_id=card_id,
        )
        accepted = card.model_copy(
            update={
                "recommendation": rec,
                "plan": plan,
                "preregistered_seq": record.seq,
                "watch_id": watch.id,
            }
        )
        self.cards.put(accepted)
        try:
            shared = self._advice.get(card_id)
            self._advice.replace(
                shared.model_copy(
                    update={
                        "recommendation": rec,
                        "measurement_plan": plan,
                        "preregistered_seq": record.seq,
                    }
                )
            )
        except Exception:  # the shared /advice store may have dropped the card; ours is kept
            _log.info("retail_advice_not_shared", card_id=card_id)
        return accepted

    def card_progress(self, card: RetailCard) -> dict[str, object]:
        """A card's check as the brief and the page see it."""
        readings = self.findings.recent(limit=500, watch_id=card.watch_id) if card.watch_id else []
        values = [f.metric for f in readings if f.metric is not None]
        return {
            "card": card.title,
            "change": card.change,
            "status": card.recommendation.status,
            "metric": card.plan.primary_metric,
            "pairs_planned": card.plan.n_pairs,
            "block_minutes": card.plan.block_minutes,
            "pairs_done": 0,
            "watch_readings": len(values),
            "mean_reading": round(sum(values) / len(values), 2) if values else None,
            "preregistered_seq": card.preregistered_seq,
        }

    # -- brief ---------------------------------------------------------------------------------

    def brief(self, lang: str = "en") -> Brief:
        """Write the owner's brief from the last 24 hours."""
        now = self._clock.now()
        since = since_day(now)
        day = self.findings.recent(limit=2000, since=since)
        tasks = [t for t in self.tasks.all() if t.created_at >= since]
        asks = [a for a in self.asks(50) if str(a.get("ts") or "") >= since.isoformat()]
        answered = {f.id: self.desk.answered(f) for f in day if f.alert_id}
        stats = day_stats(day, tasks, len(asks), answered)
        brief = self._briefs.write(
            stats=stats,
            acted=[f for f in day if f.alert_id],
            tasks=[t for t in tasks if t.status in {"open", "on_my_way"}],
            cards=[self.card_progress(c) for c in self.cards.all()],
            asks=[
                {"question": a.get("question"), "answer": str(a.get("answer") or "")[:300]}
                for a in asks
            ],
            watches=self.watches.all(),
            lang=lang,
            store=self._profile,
        )
        self.last_brief = brief
        return brief

    # -- overview ------------------------------------------------------------------------------

    def overview(self) -> dict[str, object]:
        """Everything the supermarket page shows, in one call."""
        recent = self.findings.recent(limit=30)
        return {
            "cameras": self.cameras(),
            "watches": [w.model_dump(mode="json") for w in self.watches.all()],
            "findings": [
                {**f.model_dump(mode="json"), "alert_status": self.desk.answered(f)} for f in recent
            ],
            "tasks": [t.model_dump(mode="json") for t in self.tasks_for()[:20]],
            "cards": [
                {**c.model_dump(mode="json"), "progress": self.card_progress(c)}
                for c in self.cards.all()
            ],
            "asks": self.asks(8),
            "brief": self.last_brief.model_dump(mode="json") if self.last_brief else None,
        }


def image_resolver(dirs: Sequence[Path]) -> Callable[[str], Path | None]:
    """Find an evidence file by name in the allowed folders (names only, no paths)."""

    def resolve(name: str) -> Path | None:
        if Path(name).name != name or not name.endswith((".jpg", ".png")):
            return None
        for folder in dirs:
            path = folder / name
            if path.is_file():
                return path
        return None

    return resolve
