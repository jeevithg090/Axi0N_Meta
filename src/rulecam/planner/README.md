# `rulecam.planner`

The Site Planner turns multimodal requests into proven, verifiable Site Plans.

## Pipeline Steps
1. **Understand** (`planner/steps/understand.py`): Multimodal intake of text, photos/PDFs, voice, and video walk-throughs.
2. **Survey & Probes** (`planner/steps/survey.py`, `probes.py`): Camera inventory and SAM detectability probes.
3. **Compile** (`planner/steps/compile.py`): Compiles concerns into Rule IR with guardrail validation.
4. **Lint & Acceptance** (`planner/lint.py`, `acceptance.py`): Static plan linter and executable acceptance checks.
5. **Audit** (`planner/audit.py`): Spark Open plan audit and Glimmer back-translation.

## Understand Step API

```python
from rulecam.planner.steps.understand import understand
from rulecam.contracts.plan import RequestItem

items = [
    RequestItem(kind="photo", file="inspection_report.jpg"),
    RequestItem(kind="pdf", file="food_code_2026.pdf"),
    RequestItem(kind="text", text="quiero pasar la proxima inspeccion", lang="es"),
]
spark = SparkClient.from_settings(settings, clock=clock, bus=bus)
res = understand(items, spark=spark, files=spark, voice=voice_client)
# res.concerns -> deduplicated list of Concern objects with standards citations
# res.languages -> ('es', 'en')
# res.setting_guess -> 'commercial_kitchen'
```

- PDFs and photos are uploaded with the Files API (`files.attach`) and sent to Spark 1.3 as
  `input_file` / `input_image` parts; walk-through MP4s go to Spark 1.2 as `input_video` (video
  and narration in one call). Voice WAVs go to Voice Transcribe (diarized), then the transcript
  to Spark 1.3. Only file names reach the model, never local paths.
- A missing file, a wrong file type (`pdf`: .pdf; `photo`: .jpg/.jpeg/.png/.webp; `video`:
  .mp4; `voice`: .wav), or a missing uploader/transcriber raises `IntakeError`.
- Every system prompt carries the data-not-instructions clause.

## Tests

```bash
uv run pytest tests/planner
```
