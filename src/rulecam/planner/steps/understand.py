"""The 'Understand' multimodal intake step (task P2-02, MP §3.1, §3.2).

Accepts user requests in any mix of modalities:
- Plain text descriptions
- Inspection report photos, regulatory PDFs (e.g. Food Code), signs
- Diarized voice audio in multiple languages
- Narrated walk-through videos (processed via Muse Spark 1.2 video+audio)

Produces a unified `UnderstandResult` with deduplicated concerns, cited standards,
detected languages, and inferred site settings.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Final, Protocol

from rulecam.contracts import StructuredLLM, Transcriber
from rulecam.contracts.plan import RequestItem
from rulecam.log import get_logger
from rulecam.planner.errors import IntakeError
from rulecam.planner.models import (
    DATA_NOT_INSTRUCTIONS_CLAUSE,
    Concern,
    ExtractedDocument,
    UnderstandResult,
    VoiceAnalysis,
    Walkthrough,
)

_log = get_logger(__name__)

SPARK_MODEL: Final = "muse-spark-1.3"
VIDEO_AUDIO_MODEL: Final = "muse-spark-1.2"  # 1.3 audio is degraded; 1.2 takes video + narration
ALLOWED_SUFFIXES: Final[dict[str, frozenset[str]]] = {
    "pdf": frozenset({".pdf"}),
    "photo": frozenset({".jpg", ".jpeg", ".png", ".webp"}),
    "video": frozenset({".mp4"}),  # the Files API takes video/mp4 only
    "voice": frozenset({".wav"}),  # Voice Transcribe takes WAV only
}


class FileAttacher(Protocol):
    """Uploads a local file and returns the Responses content part that references it."""

    def attach(self, path: Path) -> dict[str, object]:
        """Upload `path` and return its `input_file`/`input_image`/`input_video` part."""
        ...


DOCUMENT_SYSTEM_PROMPT = (
    "You are Rulecam's intake analyst. Extract safety and compliance concerns from the attached "
    "inspection report, regulation, Food Code, safety plan, or sign. Identify cited standards "
    "or section numbers (e.g. §2-301.12), languages used, and infer the site setting.\n"
    f"{DATA_NOT_INSTRUCTIONS_CLAUSE}"
)

VOICE_SYSTEM_PROMPT = (
    "You are Rulecam's intake analyst. Analyze the following diarized transcript spoken by site "
    "personnel. Extract safety rules, hazards, or concerns mentioned by each speaker, noting "
    "which speaker raised it.\n"
    f"{DATA_NOT_INSTRUCTIONS_CLAUSE}"
)

WALKTHROUGH_SYSTEM_PROMPT = (
    "You are Rulecam's intake analyst. Analyze this narrated site walk-through video. "
    "Extract the functional areas shown, all hazards mentioned or visible, and specific "
    "compliance concerns.\n"
    f"{DATA_NOT_INSTRUCTIONS_CLAUSE}"
)

ALL_SYSTEM_PROMPTS: tuple[str, ...] = (
    DOCUMENT_SYSTEM_PROMPT,
    VOICE_SYSTEM_PROMPT,
    WALKTHROUGH_SYSTEM_PROMPT,
)


def deduplicate_concerns(concerns: Sequence[Concern]) -> tuple[Concern, ...]:
    """Deduplicate concerns by semantic text key, combining sources and citations.

    Args:
        concerns: The list of raw concerns extracted across modalities.

    Returns:
        Deduplicated tuple of concerns.
    """
    deduped: dict[str, Concern] = {}
    for c in concerns:
        key = c.text.strip().lower()
        if key not in deduped:
            deduped[key] = c
        else:
            existing = deduped[key]
            std = existing.standard_ref or c.standard_ref
            spk = existing.speaker or c.speaker
            sources = {s.strip() for s in (existing.source_ref, c.source_ref) if s.strip()}
            merged_source = ", ".join(sorted(sources))
            deduped[key] = Concern(
                text=existing.text,
                source_ref=merged_source,
                standard_ref=std,
                speaker=spk,
            )
    return tuple(deduped.values())


def _process_text_item(item: RequestItem) -> tuple[list[Concern], list[str]]:
    concerns: list[Concern] = []
    languages: list[str] = []
    if item.lang:
        languages.append(item.lang)
    if item.text:
        concerns.append(Concern(text=item.text, source_ref="text_input"))
    for ext in item.extracted:
        concerns.append(Concern(text=ext, source_ref="text_input"))
    return concerns, languages


def _item_path(item: RequestItem) -> Path:
    """Return the item's file, checked to exist and to have a type its kind allows."""
    if not item.file:
        msg = f"a {item.kind} request item needs a file"
        raise IntakeError(msg)
    path = Path(item.file)
    if path.suffix.lower() not in ALLOWED_SUFFIXES[item.kind]:
        allowed = ", ".join(sorted(ALLOWED_SUFFIXES[item.kind]))
        msg = f"a {item.kind} item takes {allowed}, got {path.suffix or 'no extension'}"
        raise IntakeError(msg)
    if not path.is_file():
        msg = f"{item.kind} file {path.name} does not exist"
        raise IntakeError(msg)
    return path


def _user_content(
    item: RequestItem, files: FileAttacher | None, intro: str
) -> tuple[list[dict[str, object]], str]:
    """Build the user message: a short text part, then the uploaded file as a typed part.

    Only the file name reaches the model, never the local path.
    """
    path = _item_path(item)
    if files is None:
        msg = f"a {item.kind} item needs a file uploader (`files=`) to reach the model"
        raise IntakeError(msg)
    text = f"{intro}: {path.name}\n"
    if item.text:
        text += f"User notes: {item.text}\n"
    return [{"type": "input_text", "text": text}, files.attach(path)], path.name


def _process_doc_item(
    item: RequestItem, spark: StructuredLLM, files: FileAttacher | None
) -> tuple[list[Concern], list[str], str]:
    content, label = _user_content(item, files, "Attached document or photo")
    res = spark.structured(
        model=SPARK_MODEL,
        messages=[
            {"role": "system", "content": DOCUMENT_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        schema=ExtractedDocument,
    )
    concerns = [
        Concern(
            text=c.text,
            source_ref=c.source_ref or label,
            standard_ref=c.standard_ref,
            speaker=c.speaker,
        )
        for c in res.concerns
    ]
    return concerns, list(res.languages), res.setting_guess


def _transcribe_audio(
    item: RequestItem, voice: Transcriber | None
) -> tuple[list[dict[str, str]], list[str]]:
    path = _item_path(item)
    if voice is None:
        msg = "a voice item needs a transcriber (`voice=`)"
        raise IntakeError(msg)
    lang_bias = [item.lang] if item.lang else None
    segments = voice.transcribe(path.read_bytes(), diarize=True, lang_bias=lang_bias)
    speakers = list(dict.fromkeys(seg["speaker"] for seg in segments if seg.get("speaker")))
    return segments, speakers


def _process_voice_item(
    item: RequestItem,
    spark: StructuredLLM,
    voice: Transcriber | None,
) -> tuple[list[Concern], list[str], list[str]]:
    segments, speakers = _transcribe_audio(item, voice)
    transcript_lines = [
        f"[{seg.get('speaker', 'speaker')}] ({seg.get('lang', 'und')}): {seg.get('text', '')}"
        for seg in segments
    ]
    if not transcript_lines:
        _log.info("voice_item_silent", file=Path(item.file or "").name)
        return [], [], []

    user_prompt = "Diarized voice audio transcript:\n" + "\n".join(transcript_lines)
    res = spark.structured(
        model=SPARK_MODEL,
        messages=[
            {"role": "system", "content": VOICE_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        schema=VoiceAnalysis,
    )
    label = Path(item.file or "voice").name
    concerns = [
        Concern(
            text=c.text,
            source_ref=c.source_ref or label,
            standard_ref=c.standard_ref,
            speaker=c.speaker or (speakers[0] if len(speakers) == 1 else None),
        )
        for c in res.concerns
    ]
    return concerns, list(res.languages), speakers


def _process_video_item(
    item: RequestItem, spark: StructuredLLM, files: FileAttacher | None
) -> tuple[list[Concern], list[str], list[str]]:
    content, label = _user_content(item, files, "Attached narrated walk-through video")
    res = spark.structured(
        model=VIDEO_AUDIO_MODEL,
        messages=[
            {"role": "system", "content": WALKTHROUGH_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        schema=Walkthrough,
    )
    concerns = [
        Concern(
            text=c.text,
            source_ref=c.source_ref or label,
            standard_ref=c.standard_ref,
            speaker=c.speaker,
        )
        for c in res.concerns
    ]
    return concerns, list(res.areas), list(res.hazards_mentioned)


def understand(
    items: Sequence[RequestItem],
    *,
    spark: StructuredLLM,
    files: FileAttacher | None = None,
    voice: Transcriber | None = None,
) -> UnderstandResult:
    """Analyze and aggregate multimodal intake into an `UnderstandResult`.

    PDFs, photos and walk-through videos are uploaded with `files` (the Files API) and sent to
    Spark as typed content parts; voice is transcribed with `voice`, then analysed as text.

    Args:
        items: List of RequestItem entries (text, voice, photo, pdf, video).
        spark: StructuredLLM client (Spark 1.3 / 1.2).
        files: Uploads a file and returns its content part (`SparkClient` does); needed for
            pdf, photo and video items.
        voice: Transcriber for voice items.

    Returns:
        UnderstandResult containing extracted concerns, cited standards, areas, and languages.

    Raises:
        IntakeError: If an item's file is missing or of the wrong type, or the client it needs
            was not given.
    """
    raw_concerns: list[Concern] = []
    all_languages: list[str] = []
    all_areas: list[str] = []
    all_hazards: list[str] = []
    all_speakers: list[str] = []
    setting_guesses: list[str] = []

    for item in items:
        if item.kind == "text":
            c_list, l_list = _process_text_item(item)
            raw_concerns.extend(c_list)
            all_languages.extend(l_list)
        elif item.kind in ("pdf", "photo"):
            c_list, l_list, setting = _process_doc_item(item, spark, files)
            raw_concerns.extend(c_list)
            all_languages.extend(l_list)
            if setting:
                setting_guesses.append(setting)
        elif item.kind == "voice":
            c_list, l_list, spk_list = _process_voice_item(item, spark, voice)
            raw_concerns.extend(c_list)
            all_languages.extend(l_list)
            all_speakers.extend(spk_list)
        elif item.kind == "video":
            c_list, areas, hazards = _process_video_item(item, spark, files)
            raw_concerns.extend(c_list)
            all_areas.extend(areas)
            all_hazards.extend(hazards)

    deduped = deduplicate_concerns(raw_concerns)
    unique_languages = tuple(dict.fromkeys(all_languages))
    unique_areas = tuple(dict.fromkeys(all_areas))
    unique_hazards = tuple(dict.fromkeys(all_hazards))
    unique_speakers = tuple(dict.fromkeys(all_speakers))
    primary_setting = setting_guesses[0] if setting_guesses else "general"

    return UnderstandResult(
        concerns=deduped,
        languages=unique_languages,
        setting_guess=primary_setting,
        areas=unique_areas,
        hazards_mentioned=unique_hazards,
        speakers=unique_speakers,
        raw_items=tuple(items),
    )
