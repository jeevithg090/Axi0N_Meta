"""Unit tests for multimodal intake 'Understand' step (task P2-02, MP §3.1, §3.2)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, TypeVar

from pydantic import BaseModel

from rulecam.contracts.plan import RequestItem
from rulecam.planner.models import (
    DATA_NOT_INSTRUCTIONS_CLAUSE,
    Concern,
    ExtractedDocument,
    VoiceAnalysis,
    Walkthrough,
)
from rulecam.planner.steps.understand import (
    ALL_SYSTEM_PROMPTS,
    deduplicate_concerns,
    understand,
)

M = TypeVar("M", bound=BaseModel)


class MockSparkLLM:
    """Mock StructuredLLM returning preset models in sequence and recording calls."""

    def __init__(self, responses: list[BaseModel]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def structured(
        self,
        *,
        model: str,
        messages: list[dict[str, object]],
        schema: type[M],
        reasoning_effort: Literal["low", "medium", "high"] = "low",
        background: bool = False,
    ) -> M:
        self.calls.append(
            {
                "model": model,
                "messages": messages,
                "schema": schema,
                "reasoning_effort": reasoning_effort,
                "background": background,
            }
        )
        if not self._responses:
            msg = f"No queued mock response for schema {schema}"
            raise AssertionError(msg)
        resp = self._responses.pop(0)
        assert isinstance(resp, schema)
        return resp


class MockTranscriber:
    """Mock Transcriber returning preset diarized segments."""

    def __init__(self, segments: list[dict[str, str]]) -> None:
        self.segments = segments
        self.transcribe_calls: list[dict[str, object]] = []

    def transcribe(
        self,
        audio: bytes,
        *,
        lang_bias: list[str] | None = None,
        keywords: list[str] | None = None,
        diarize: bool = False,
    ) -> list[dict[str, str]]:
        self.transcribe_calls.append(
            {
                "audio_len": len(audio),
                "lang_bias": lang_bias,
                "keywords": keywords,
                "diarize": diarize,
            }
        )
        return self.segments


def test_prompt_hygiene_clause_present_in_every_system_prompt() -> None:
    """Verify non-negotiable prompt hygiene: data-not-instructions must be in all system prompts."""
    assert len(ALL_SYSTEM_PROMPTS) >= 3
    for prompt in ALL_SYSTEM_PROMPTS:
        assert DATA_NOT_INSTRUCTIONS_CLAUSE in prompt


def test_uc1_photo_and_pdf_gives_handwashing_and_coldholding_with_section_refs(
    tmp_path: Path,
) -> None:
    """UC1: photo + PDF yields handwash and cold-holding with citations."""
    photo_file = tmp_path / "inspection_report.jpg"
    photo_file.write_bytes(b"fake_jpeg")
    pdf_file = tmp_path / "food_code_2026.pdf"
    pdf_file.write_bytes(b"fake_pdf")

    items = [
        RequestItem(kind="photo", file=str(photo_file)),
        RequestItem(kind="pdf", file=str(pdf_file)),
        RequestItem(kind="text", text="quiero pasar la proxima inspeccion", lang="es"),
    ]

    spark_reply_photo = ExtractedDocument(
        concerns=[
            Concern(
                text="Handwashing compliance before line entry",
                source_ref=str(photo_file),
                standard_ref="§2-301.12",
            )
        ],
        languages=["en"],
        setting_guess="commercial_kitchen",
    )
    spark_reply_pdf = ExtractedDocument(
        concerns=[
            Concern(
                text="Cold-holding temperature under 41F",
                source_ref=str(pdf_file),
                standard_ref="§2-103.11(G)",
            )
        ],
        languages=["en"],
        setting_guess="commercial_kitchen",
    )

    spark = MockSparkLLM([spark_reply_photo, spark_reply_pdf])
    res = understand(items, spark=spark)

    assert res.setting_guess == "commercial_kitchen"
    assert "es" in res.languages
    assert "en" in res.languages

    concerns_by_text = {c.text: c for c in res.concerns}
    assert "Handwashing compliance before line entry" in concerns_by_text
    assert concerns_by_text["Handwashing compliance before line entry"].standard_ref == "§2-301.12"
    assert "Cold-holding temperature under 41F" in concerns_by_text
    assert concerns_by_text["Cold-holding temperature under 41F"].standard_ref == "§2-103.11(G)"
    assert "quiero pasar la proxima inspeccion" in concerns_by_text


def test_uc2_spanish_and_english_voice_gives_concerns_with_two_speakers(
    tmp_path: Path,
) -> None:
    """UC2: Spanish and English speech diarizes into two distinct speakers."""
    audio_file = tmp_path / "crew_briefing.wav"
    audio_file.write_bytes(b"fake_wav_audio")

    items = [RequestItem(kind="voice", file=str(audio_file))]

    mock_segments = [
        {"speaker": "foreman_es", "text": "Los trabajadores deben llevar casco", "lang": "es"},
        {
            "speaker": "supervisor_en",
            "text": "And safety vest is mandatory in active zone",
            "lang": "en",
        },
    ]
    transcriber = MockTranscriber(mock_segments)

    spark_reply_voice = VoiceAnalysis(
        concerns=[
            Concern(text="Hard hat required", source_ref=str(audio_file), speaker="foreman_es"),
            Concern(
                text="Safety vest required", source_ref=str(audio_file), speaker="supervisor_en"
            ),
        ],
        languages=["es", "en"],
    )
    spark = MockSparkLLM([spark_reply_voice])

    res = understand(items, spark=spark, voice=transcriber)

    assert len(transcriber.transcribe_calls) == 1
    assert set(res.speakers) == {"foreman_es", "supervisor_en"}
    assert "es" in res.languages
    assert "en" in res.languages
    assert len(res.concerns) == 2

    c_map = {c.text: c for c in res.concerns}
    assert c_map["Hard hat required"].speaker == "foreman_es"
    assert c_map["Safety vest required"].speaker == "supervisor_en"


def test_walkthrough_video_gives_areas_hazards_and_concerns() -> None:
    """Walk-through video processed via Muse Spark 1.2 video+audio returns areas and hazards."""
    items = [RequestItem(kind="video", file="walkthrough.mp4")]

    spark_reply_video = Walkthrough(
        areas=["sink_station", "prep_line", "walkway"],
        hazards_mentioned=["wet floor near sink", "ladder blocking walkway"],
        concerns=[
            Concern(text="Wet floor slip hazard", source_ref="walkthrough.mp4"),
            Concern(text="Walkway blocked by ladder", source_ref="walkthrough.mp4"),
        ],
    )
    spark = MockSparkLLM([spark_reply_video])

    res = understand(items, spark=spark)

    assert len(spark.calls) == 1
    assert spark.calls[0]["model"] == "muse-spark-1.2"
    assert res.areas == ("sink_station", "prep_line", "walkway")
    assert res.hazards_mentioned == ("wet floor near sink", "ladder blocking walkway")
    assert len(res.concerns) == 2


def test_deduplication_combines_sources_and_preserves_standard_citations() -> None:
    """Verify deduplication groups identical worries and combines sources."""
    c1 = Concern(text="Handwash at sink 20s", source_ref="inspection.pdf", standard_ref="§2-301.12")
    c2 = Concern(
        text="handwash at sink 20s", source_ref="voice_note.wav", standard_ref=None, speaker="spk1"
    )
    c3 = Concern(text="Unique concern", source_ref="text")

    deduped = deduplicate_concerns([c1, c2, c3])
    assert len(deduped) == 2

    handwash = next(c for c in deduped if "Handwash" in c.text or "handwash" in c.text)
    assert handwash.standard_ref == "§2-301.12"
    assert "inspection.pdf" in handwash.source_ref
    assert "voice_note.wav" in handwash.source_ref
    assert handwash.speaker == "spk1"


def test_text_item_passthrough() -> None:
    """Plain text requests pass through directly with source references."""
    items = [
        RequestItem(kind="text", text="Ensure gloves are worn for raw meat", lang="en"),
        RequestItem(kind="text", extracted=("Spill sign needed within 60s",)),
    ]
    spark = MockSparkLLM([])

    res = understand(items, spark=spark)

    assert len(spark.calls) == 0
    assert len(res.concerns) == 2
    assert "en" in res.languages
    texts = {c.text for c in res.concerns}
    assert "Ensure gloves are worn for raw meat" in texts
    assert "Spill sign needed within 60s" in texts
