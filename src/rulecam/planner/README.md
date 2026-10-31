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
res = understand(items, spark=spark_client, voice=voice_client)
# res.concerns -> deduplicated list of Concern objects with standards citations
# res.languages -> ('es', 'en')
# res.setting_guess -> 'commercial_kitchen'
```

## Tests

```bash
uv run pytest tests/planner
```
