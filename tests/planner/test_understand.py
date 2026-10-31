"""Unit tests for multimodal intake 'Understand' step (task P2-02, MP §3.1, §3.2)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, TypeVar

import pytest
from pydantic import BaseModel

from rulecam.clients.meta_hosted.spark import SparkClient
from rulecam.contracts.plan import RequestItem
from rulecam.planner.errors import IntakeError
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
from tests.clients.meta_hosted.transport_fakes import ScriptedTransport
from tests.fakes import FakeClock

M = TypeVar("M", bound=BaseModel)

SPARK_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "replay" / "spark"


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


class FakeAttacher:
    """Stands in for `SparkClient.attach`: records uploads and returns typed parts."""

    def __init__(self) -> None:
        self.attached: list[Path] = []

    def attach(self, path: Path) -> dict[str, object]:
        self.attached.append(path)
        kind = {".pdf": "input_file", ".mp4": "input_video"}.get(path.suffix, "input_image")
        return {"type": kind, "file_id": f"file-{path.stem}"}


def _user_parts(call: dict[str, object]) -> list[dict[str, object]]:
    messages = call["messages"]
    assert isinstance(messages, list)
    content = messages[1]["content"]
    assert isinstance(content, list)
    return content


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
    files = FakeAttacher()
    res = understand(items, spark=spark, files=files)

    assert files.attached == [photo_file, pdf_file]
    photo_parts, pdf_parts = (_user_parts(c) for c in spark.calls)
    assert photo_parts[1] == {"type": "input_image", "file_id": "file-inspection_report"}
    assert pdf_parts[1] == {"type": "input_file", "file_id": "file-food_code_2026"}
    assert str(tmp_path) not in str(photo_parts[0]["text"])  # only the file name is sent

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


def test_walkthrough_video_gives_areas_hazards_and_concerns(tmp_path: Path) -> None:
    """Walk-through video processed via Muse Spark 1.2 video+audio returns areas and hazards."""
    video = tmp_path / "walkthrough.mp4"
    video.write_bytes(b"fake_mp4")
    items = [RequestItem(kind="video", file=str(video))]

    spark_reply_video = Walkthrough(
        areas=["sink_station", "prep_line", "walkway"],
        hazards_mentioned=["wet floor near sink", "ladder blocking walkway"],
        concerns=[
            Concern(text="Wet floor slip hazard", source_ref="walkthrough.mp4"),
            Concern(text="Walkway blocked by ladder", source_ref="walkthrough.mp4"),
        ],
    )
    spark = MockSparkLLM([spark_reply_video])

    res = understand(items, spark=spark, files=FakeAttacher())

    assert len(spark.calls) == 1
    assert spark.calls[0]["model"] == "muse-spark-1.2"
    assert _user_parts(spark.calls[0])[1] == {"type": "input_video", "file_id": "file-walkthrough"}
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


def test_file_items_need_an_existing_file_of_the_right_type(tmp_path: Path) -> None:
    spark = MockSparkLLM([])
    with pytest.raises(IntakeError, match="does not exist"):
        understand([RequestItem(kind="pdf", file=str(tmp_path / "gone.pdf"))], spark=spark)
    wrong = tmp_path / "plan.docx"
    wrong.write_bytes(b"x")
    with pytest.raises(IntakeError, match=r"takes \.pdf"):
        understand([RequestItem(kind="pdf", file=str(wrong))], spark=spark)
    with pytest.raises(IntakeError, match="needs a file"):
        understand([RequestItem(kind="video")], spark=spark)
    assert spark.calls == []


def test_file_items_need_an_uploader_and_voice_needs_a_transcriber(tmp_path: Path) -> None:
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF")
    wav = tmp_path / "note.wav"
    wav.write_bytes(b"RIFF")
    with pytest.raises(IntakeError, match="uploader"):
        understand([RequestItem(kind="pdf", file=str(pdf))], spark=MockSparkLLM([]))
    with pytest.raises(IntakeError, match="transcriber"):
        understand([RequestItem(kind="voice", file=str(wav))], spark=MockSparkLLM([]))


def test_silent_voice_item_gives_no_concerns(tmp_path: Path) -> None:
    wav = tmp_path / "silence.wav"
    wav.write_bytes(b"RIFF")
    spark = MockSparkLLM([])
    res = understand(
        [RequestItem(kind="voice", file=str(wav))], spark=spark, voice=MockTranscriber([])
    )
    assert res.concerns == ()
    assert spark.calls == []


def test_spark_client_uploads_the_pdf_and_references_it(tmp_path: Path) -> None:
    """End to end through the real client: the PDF goes to /files and its id into /responses."""
    pdf = tmp_path / "food_code.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    extracted = ExtractedDocument(
        concerns=[Concern(text="Handwash", source_ref="p.3", standard_ref="§2-301.12")]
    )
    reply = {
        "status": 200,
        "headers": {},
        "json": {
            "id": "resp_1",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": extracted.model_dump_json()}],
                }
            ],
        },
    }
    transport = ScriptedTransport(reply)
    client = SparkClient(transport, clock=FakeClock())

    res = understand([RequestItem(kind="pdf", file=str(pdf))], spark=client, files=client)

    assert res.concerns[0].standard_ref == "§2-301.12"
    [upload] = transport.uploads
    assert upload["file"]["content"] == b"%PDF-1.4 fake"
    content = transport.bodies()[0]["input"][1]["content"]
    assert content[1] == {"type": "input_file", "file_id": "file-rec-0001"}


def test_deterministic_speaker_order_preserved(tmp_path: Path) -> None:
    """Speakers are listed in the order they first speak."""
    audio_file = tmp_path / "multi_speaker.wav"
    audio_file.write_bytes(b"wav_bytes")
    segments = [
        {"speaker": "charlie", "text": "c", "lang": "en"},
        {"speaker": "alice", "text": "a", "lang": "en"},
        {"speaker": "bob", "text": "b", "lang": "en"},
        {"speaker": "alice", "text": "a2", "lang": "en"},
    ]
    spark = MockSparkLLM([VoiceAnalysis(concerns=[], languages=["en"])])

    res = understand(
        [RequestItem(kind="voice", file=str(audio_file))],
        spark=spark,
        voice=MockTranscriber(segments),
    )
    assert res.speakers == ("charlie", "alice", "bob")


def _fixture(name: str) -> dict[str, object]:
    reply: dict[str, object] = json.loads((SPARK_FIXTURES / name).read_text(encoding="utf-8"))
    return reply


def test_uc1_photo_and_pdf_with_real_spark_client_and_replay_fixtures(tmp_path: Path) -> None:
    """UC1 through the real SparkClient: both files are uploaded and referenced by id."""
    transport = ScriptedTransport(_fixture("uc1_photo_reply.json"), _fixture("uc1_pdf_reply.json"))
    spark_client = SparkClient(transport, clock=FakeClock())
    photo_file = tmp_path / "inspection_report.jpg"
    photo_file.write_bytes(b"image_bytes_for_photo")
    pdf_file = tmp_path / "food_code_2026.pdf"
    pdf_file.write_bytes(b"pdf_bytes_for_code")
    items = [
        RequestItem(kind="photo", file=str(photo_file)),
        RequestItem(kind="pdf", file=str(pdf_file)),
    ]

    res = understand(items, spark=spark_client, files=spark_client)

    assert res.setting_guess == "commercial_kitchen"
    concerns = {c.text: c for c in res.concerns}
    assert concerns["Handwashing compliance before line entry"].standard_ref == "§2-301.12"
    assert concerns["Cold-holding temperature under 41F"].standard_ref == "§2-103.11(G)"
    assert [u["file"]["content_type"] for u in transport.uploads] == [
        "image/jpeg",
        "application/pdf",
    ]
    photo_body, pdf_body = transport.bodies()
    assert photo_body["input"][1]["content"][1]["type"] == "input_image"
    assert pdf_body["input"][1]["content"][1]["type"] == "input_file"


def test_walkthrough_video_with_real_spark_client_and_replay_fixture(tmp_path: Path) -> None:
    """Walk-through through the real SparkClient: Spark 1.2 gets the uploaded video."""
    transport = ScriptedTransport(_fixture("walkthrough_reply.json"))
    spark_client = SparkClient(transport, clock=FakeClock())
    video_file = tmp_path / "walkthrough.mp4"
    video_file.write_bytes(b"fake_mp4_bytes")

    res = understand(
        [RequestItem(kind="video", file=str(video_file))], spark=spark_client, files=spark_client
    )

    assert "sink_station" in res.areas
    assert "wet floor near sink" in res.hazards_mentioned
    assert len(res.concerns) == 2
    [body] = transport.bodies()
    assert body["model"] == "muse-spark-1.2"
    assert body["input"][1]["content"][1] == {"type": "input_video", "file_id": "file-rec-0001"}


def test_uc2_voice_with_real_spark_client_and_replay_fixture(tmp_path: Path) -> None:
    """UC2 through the real SparkClient: the diarized transcript is analysed as text."""
    transport = ScriptedTransport(_fixture("voice_analysis_reply.json"))
    spark_client = SparkClient(transport, clock=FakeClock())
    audio_file = tmp_path / "crew_briefing.wav"
    audio_file.write_bytes(b"RIFF")
    segments = [
        {"speaker": "foreman_es", "text": "Los trabajadores deben llevar casco", "lang": "es"},
        {"speaker": "supervisor_en", "text": "Safety vest is mandatory", "lang": "en"},
    ]

    res = understand(
        [RequestItem(kind="voice", file=str(audio_file))],
        spark=spark_client,
        voice=MockTranscriber(segments),
    )

    assert res.speakers == ("foreman_es", "supervisor_en")
    assert len(res.concerns) >= 2
    assert transport.uploads == []  # audio goes to Voice Transcribe, not to Spark
    [body] = transport.bodies()
    assert "[foreman_es] (es): Los trabajadores" in body["input"][1]["content"]
