"""A worker reports a near miss by voice, in any language; the cameras check it.

About one in four workers never report a near miss. Here a worker speaks (Spanish, English,
...): Muse Voice Transcribe writes it down, and Muse Spark 1.3 reads the transcript next to the
near misses the cameras measured in the time window, says which one it matches (or none, and
why), summarises it for the owner and proposes a plain-language rule. The owner can send that
proposal to the Site Planner, which compiles it and runs Rule CI before anyone can approve it.

The audio is converted to 16 kHz WAV for Voice Transcribe and never stored. A matched report
is chained on the near miss as a `label` record (`worker_report`, no transcript in it).
"""

from __future__ import annotations

import io
import json
import secrets
import subprocess
import wave
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from rulecam.contracts import Clock, Transcriber
from rulecam.errors import RulecamError
from rulecam.log import get_logger
from rulecam.voice_out.languages import primary_subtag
from rulecam.warehouse.duty import ChainWriter
from rulecam.warehouse.models import NearMiss, ReportDraft, VoiceReport
from rulecam.warehouse.talks import DATA_NOT_INSTRUCTIONS, SPARK_MODEL, Store, StructuredModel

_log = get_logger(__name__)

MAX_AUDIO_BYTES = 10 * 1024 * 1024
SAMPLE_RATE = 16_000
CONVERT_TIMEOUT_S = 60.0
WORKER_REPORT = "worker_report"
REPORT_SYSTEM = (
    "A warehouse worker reported a near miss with a forklift by voice; the transcript follows, "
    "with the near misses the cameras measured in the same time window (distances in metres, "
    "from calibrated cameras). Summarise the report in English (`summary_en`) and in the "
    "worker's language (`what_happened`, `lang` = its code); say where and how many minutes "
    "ago it happened if the worker said so, and the forklift's painted unit number if they "
    "said one. If one measured near miss is clearly the same incident, give its event id in "
    "`matches_event_id` and say why; otherwise null and why none fits. Propose one plain, "
    "checkable camera rule that would catch this next time, in the worker's language "
    "(`proposed_rule`) and in English (`proposed_rule_en`). Never identify anyone. "
    + DATA_NOT_INSTRUCTIONS
)

Converter = Callable[[bytes], bytes]
NearMissWindow = Callable[[datetime, datetime], Sequence[NearMiss]]


class ReportError(RulecamError):
    """The audio could not be read."""


def to_wav16k(audio: bytes) -> bytes:
    """Any audio the browser or a phone records (WebM/Opus, Ogg, MP4, WAV) as 16 kHz mono WAV.

    Raises:
        ReportError: When ffmpeg cannot decode it.
    """
    # Raw PCM out of the pipe, wrapped here: a WAV written to a pipe has no length in its header.
    args = ["ffmpeg", "-v", "error", "-i", "pipe:0", "-ac", "1", "-ar", str(SAMPLE_RATE)]
    args += ["-f", "s16le", "pipe:1"]
    try:
        done = subprocess.run(  # noqa: S603 - fixed argument list, audio on stdin
            args, input=audio, capture_output=True, timeout=CONVERT_TIMEOUT_S, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        msg = f"could not convert the audio: {type(exc).__name__}"
        raise ReportError(msg) from exc
    if done.returncode != 0 or not done.stdout:
        msg = "could not decode the audio"
        raise ReportError(msg)
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(done.stdout)
    return out.getvalue()


class NearMissReporter:
    """Turns a spoken report into a checked, summarised near miss and a proposed rule."""

    def __init__(
        self,
        spark: StructuredModel,
        transcriber: Transcriber,
        *,
        store: Store,
        clock: Clock,
        chain: ChainWriter,
        near: NearMissWindow,
        convert: Converter = to_wav16k,
        window: timedelta = timedelta(minutes=30),
    ) -> None:
        """Create the reporter.

        Args:
            spark: Muse Spark 1.3.
            transcriber: Muse Voice Transcribe.
            store: Where reports are saved.
            clock: Time source.
            chain: The evidence chain (a matched report is chained on its near miss).
            near: The near misses measured between two instants.
            convert: Audio to 16 kHz WAV.
            window: How far back to look for the measured near miss.
        """
        self._spark = spark
        self._transcriber = transcriber
        self._store = store
        self._clock = clock
        self._chain = chain
        self._near = near
        self._convert = convert
        self._window = window

    def report(self, audio: bytes, *, lang: str | None = None) -> VoiceReport:
        """Transcribe, check against the cameras and summarise one spoken report (saved).

        Raises:
            ReportError: When the audio cannot be read or nothing was said.
        """
        wav = self._convert(audio)
        bias = [primary_subtag(lang)] if lang else None
        segments = self._transcriber.transcribe(wav, lang_bias=bias)
        transcript = " ".join(s.get("text", "").strip() for s in segments).strip()
        if not transcript:
            msg = "nothing was heard in the recording"
            raise ReportError(msg)
        now = self._clock.now()
        measured = list(self._near(now - self._window, now))
        rows = [
            n.model_dump(
                mode="json",
                include={
                    "event_id",
                    "ts",
                    "camera_id",
                    "separation_m",
                    "limit_m",
                    "vehicle_speed_mps",
                    "verdict",
                    "verdict_reason",
                },
            )
            for n in measured
        ]
        draft = self._spark.structured(
            model=SPARK_MODEL,
            messages=[
                {"role": "system", "content": REPORT_SYSTEM},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"now": now.isoformat(), "transcript": transcript, "measured": rows},
                        ensure_ascii=False,
                    ),
                },
            ],
            schema=ReportDraft,
        )
        by_id = {n.event_id: n for n in measured}
        matched = by_id.get(draft.matches_event_id or "")
        report = VoiceReport(
            report_id=f"nr-{secrets.token_hex(6)}",
            lang=draft.lang or (lang or ""),
            transcript=transcript,
            summary_en=draft.summary_en,
            what_happened=draft.what_happened,
            where=draft.where,
            minutes_ago=draft.minutes_ago,
            vehicle_unit=draft.vehicle_unit,
            candidates=tuple(by_id),
            matches_event_id=matched.event_id if matched else None,
            match_reason=draft.match_reason,
            measured_m=matched.separation_m if matched else None,
            proposed_rule=draft.proposed_rule,
            proposed_rule_en=draft.proposed_rule_en,
            model=SPARK_MODEL,
            created_at=now,
        )
        if matched is not None:
            record = self._chain.append(
                "label",
                {
                    "event_id": matched.event_id,
                    "rule_id": matched.rule_id,
                    "label": WORKER_REPORT,
                    "source": "worker_voice_report",
                    "report_id": report.report_id,
                    "lang": report.lang,
                },
            )
            report = report.model_copy(update={"chained_hash": record.hash})
        self._store.save("reports", report.report_id, report)
        _log.info("near_miss_voice_report", matched=matched is not None, lang=report.lang)
        return report

    def get(self, report_id: str) -> VoiceReport | None:
        """A saved report."""
        return self._store.load("reports", report_id, VoiceReport)

    def every(self) -> list[VoiceReport]:
        """Every saved report, newest first."""
        folder = self._store.root / "reports"
        paths = sorted(folder.glob("nr-*.json")) if folder.is_dir() else []
        loaded = [self._store.load("reports", p.stem, VoiceReport) for p in paths]
        return sorted(
            (r for r in loaded if r is not None), key=lambda r: r.created_at, reverse=True
        )

    def proposed(self, report_id: str, plan_id: str) -> VoiceReport | None:
        """Record that the proposal started a plan."""
        found = self.get(report_id)
        if found is None:
            return None
        updated = found.model_copy(update={"plan_id": plan_id})
        self._store.save("reports", report_id, updated)
        return updated
