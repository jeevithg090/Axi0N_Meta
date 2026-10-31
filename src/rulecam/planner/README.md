# `rulecam.planner`

The Site Planner turns multimodal requests into proven, verifiable Site Plans.

## Pipeline Steps
1. **Understand** (`planner/steps/understand.py`): Multimodal intake of text, photos/PDFs, voice, and video walk-throughs.
2. **Survey & Probes** (`planner/steps/survey.py`, `probes.py`): Camera inventory and SAM detectability probes.
3. **Compile** (`planner/steps/compile.py`): Compiles concerns into typed RuleIR with guardrail validation, static concept tagging, and repair rounds.
4. **Guardrails** (`planner/guardrails.py`): Deterministic (English + Spanish) and model guardrails declining banned surveillance categories with alternatives.
5. **Lint & Acceptance** (`planner/lint.py`, `acceptance.py`): Static plan linter and executable acceptance checks.
6. **Audit** (`planner/audit.py`): Spark Open plan audit and Glimmer back-translation.

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

## Guardrail Declines API

```python
from rulecam.planner.guardrails import check_request, Allowed, Declined

result = check_request("who is slowest")
if isinstance(result, Declined):
    print(result.category)  # GuardrailCategory.PER_PERSON_PRODUCTIVITY
    print(result.reason)
    print(result.alternative)  # Suggests station throughput instead
    verdict = result.to_feasibility_verdict(concern="who is slowest")
```

## Compile Step API

```python
from rulecam.planner.steps.compile import compile_rule
from rulecam.planner.models import Concern

concern = Concern(
    text="Everyone entering the line must wash hands for at least 20 seconds",
    source_ref="plan",
    standard_ref="Food Code 2026 §2-301.12",
)

rule = compile_rule(
    concern,
    spark=spark_client,
    plan_id="main-st-kitchen",
    zones=declared_zones,
    inventory=surveyed_inventory,
)
# Returns typed RuleIR with static tags applied to known static objects (sink, door, racks)
# If initial schema validation fails, automatically runs one repair round with error detail.
```

## Tests

```bash
uv run pytest tests/planner
uv run pytest tests/guardrails/test_planner_declines.py
```
