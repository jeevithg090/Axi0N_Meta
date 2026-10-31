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

import base64
from collections.abc import Sequence
from pathlib import Path

from rulecam.clients.meta_hosted.spark import SPARK_1_2, SPARK_1_3
from rulecam.contracts import StructuredLLM, Transcriber
from rulecam.contracts.plan import RequestItem
from rulecam.log import get_logger
from rulecam.planner.models import (
    DATA_NOT_INSTRUCTIONS_CLAUSE,
    Concern,
    ExtractedDocument,
    UnderstandResult,
    VoiceAnalysis,
    Walkthrough,
)

_log = get_logger(__name__)

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


MIN_LINE_LEN = 3
MAX_SAMPLE_LINES = 30


def _extract_text_sample(raw_bytes: bytes) -> str:
    try:
        text_sample = raw_bytes.decode("utf-8", errors="ignore")
        lines = [
            line.strip() for line in text_sample.splitlines() if len(line.strip()) > MIN_LINE_LEN
        ]
        if lines and any(c.isalnum() for c in lines[0]):
            joined = "\n".join(lines[:MAX_SAMPLE_LINES])
            return f"\nExtracted document text sample:\n{joined}\n"
    except (UnicodeDecodeError, ValueError):
        _log.debug("document_text_extract_failed")
    return ""


def _build_doc_user_content(
    item: RequestItem,
) -> tuple[str | list[dict[str, object]], str]:
    file_label = item.file or item.kind
    user_prompt = f"Document/Photo source: {file_label}\n"
    if item.text:
        user_prompt += f"Attached user notes: {item.text}\n"

    media_parts: list[dict[str, object]] = []
    if item.file:
        file_path = Path(item.file)
        if file_path.exists():
            raw_bytes = file_path.read_bytes()
            b64_str = base64.b64encode(raw_bytes).decode("ascii")
            suffix = file_path.suffix.lower()
            if item.kind == "photo" or suffix in (".jpg", ".jpeg", ".png", ".webp"):
                mime = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"
                media_parts.append(
                    {"type": "input_image", "image_url": f"data:{mime};base64,{b64_str}"}
                )
            elif item.kind == "pdf" or suffix == ".pdf":
                media_parts.append(
                    {
                        "type": "input_file",
                        "file_data": f"data:application/pdf;base64,{b64_str}",
                        "filename": file_path.name,
                    }
                )
                sample = _extract_text_sample(raw_bytes)
                if sample:
                    user_prompt += sample

    if media_parts:
        content_parts: list[dict[str, object]] = [
            {"type": "input_text", "text": user_prompt},
            *media_parts,
        ]
        return content_parts, file_label
    return user_prompt, file_label


def _process_doc_item(
    item: RequestItem, spark: StructuredLLM
) -> tuple[list[Concern], list[str], str]:
    content, file_label = _build_doc_user_content(item)
    messages: list[dict[str, object]] = [
        {"role": "system", "content": DOCUMENT_SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]

    res = spark.structured(
        model=SPARK_1_3,
        messages=messages,
        schema=ExtractedDocument,
    )
    concerns: list[Concern] = []
    for c in res.concerns:
        src = c.source_ref or file_label
        concerns.append(
            Concern(text=c.text, source_ref=src, standard_ref=c.standard_ref, speaker=c.speaker)
        )
    return concerns, list(res.languages), res.setting_guess


def _transcribe_audio(
    item: RequestItem, voice: Transcriber | None
) -> tuple[list[dict[str, str]], list[str]]:
    if not item.file:
        _log.warning("voice_audio_missing_file", item=item)
        return [], []
    if not voice:
        _log.warning(
            "voice_transcriber_missing",
            item_file=item.file,
            hint="Voice audio supplied but no Transcriber configured",
        )
        return [], []
    audio_path = Path(item.file)
    if not audio_path.exists():
        _log.warning("voice_audio_missing", path=str(audio_path))
        return [], []

    audio_bytes = audio_path.read_bytes()
    lang_bias = [item.lang] if item.lang else None
    segments = voice.transcribe(audio_bytes, diarize=True, lang_bias=lang_bias)
    speakers = list(dict.fromkeys(seg["speaker"] for seg in segments if seg.get("speaker")))
    return segments, speakers


def _process_voice_item(
    item: RequestItem,
    spark: StructuredLLM,
    voice: Transcriber | None,
) -> tuple[list[Concern], list[str], list[str]]:
    segments, speakers = _transcribe_audio(item, voice)
    transcript_lines: list[str] = []
    for seg in segments:
        spk = seg.get("speaker", "speaker")
        lang = seg.get("lang", "en")
        txt = seg.get("text", "")
        transcript_lines.append(f"[{spk}] ({lang}): {txt}")

    if not transcript_lines and item.text:
        transcript_lines.append(f"[speaker_0]: {item.text}")

    if not transcript_lines:
        return [], [], []

    user_prompt = "Diarized voice audio transcript:\n" + "\n".join(transcript_lines)
    res = spark.structured(
        model=SPARK_1_3,
        messages=[
            {"role": "system", "content": VOICE_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        schema=VoiceAnalysis,
    )
    file_label = item.file or "voice"
    concerns = [
        Concern(
            text=c.text,
            source_ref=c.source_ref or file_label,
            standard_ref=c.standard_ref,
            speaker=c.speaker or (speakers[0] if len(speakers) == 1 else None),
        )
        for c in res.concerns
    ]
    return concerns, list(res.languages), speakers


def _process_video_item(
    item: RequestItem, spark: StructuredLLM
) -> tuple[list[Concern], list[str], list[str]]:
    file_label = item.file or "video"
    user_prompt = f"Walk-through video: {file_label}\n"
    if item.text:
        user_prompt += f"Notes: {item.text}\n"

    file_id = ""
    if item.file:
        file_path = Path(item.file)
        uploader = getattr(spark, "upload_file", None)
        if callable(uploader) and file_path.exists():
            file_id = str(uploader(file_path))
        elif file_path.exists():
            file_id = f"file_{file_path.stem}"
        elif item.file.startswith("file_"):
            file_id = item.file
        else:
            file_id = f"file_{item.file}"

    user_content: str | list[dict[str, object]]
    if file_id:
        user_content = [
            {"type": "input_text", "text": user_prompt},
            {"type": "input_video", "file_id": file_id},
        ]
    else:
        user_content = user_prompt

    res = spark.structured(
        model=SPARK_1_2,
        messages=[
            {"role": "system", "content": WALKTHROUGH_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        schema=Walkthrough,
    )
    concerns = [
        Concern(
            text=c.text,
            source_ref=c.source_ref or file_label,
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
    voice: Transcriber | None = None,
) -> UnderstandResult:
    """Analyze and aggregate multimodal intake into an `UnderstandResult`.

    Args:
        items: List of RequestItem entries (text, voice, photo, pdf, video).
        spark: StructuredLLM client (Spark 1.3 / 1.2).
        voice: Optional Transcriber client for voice audio.

    Returns:
        UnderstandResult containing extracted concerns, cited standards, areas, and languages.
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
            c_list, l_list, setting = _process_doc_item(item, spark)
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
            c_list, areas, hazards = _process_video_item(item, spark)
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
