# `rulecam.planner`

The Site Planner turns multimodal requests into proven, verifiable Site Plans.

## Pipeline Steps
1. **Understand** (`planner/steps/understand.py`): Multimodal intake of text, photos/PDFs, voice, and video walk-throughs.
2. **Survey & Probes** (`planner/steps/survey.py`, `probe.py`): Camera inventory and SAM detectability probes.
3. **Gauge** (`planner/feasibility.py`): Per-concern feasibility verdicts from the probes.
4. **Ask & Standards** (`planner/steps/ask.py`, `planner/standards.py`): Clarifying questions with frame taps; cited standards with a quote check.
5. **Compile** (`planner/steps/compile.py`): Compiles concerns into typed RuleIR with guardrail validation, static concept tagging, and repair rounds.
6. **Guardrails** (`planner/guardrails.py`): Deterministic (English + Spanish) and model guardrails declining banned surveillance categories with alternatives.
7. **Lint & Acceptance** (`planner/lint.py`, `acceptance.py`): Static plan linter and executable acceptance checks.
8. **Audit** (`planner/audit.py`): Spark Open plan audit and Glimmer back-translation.

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

rule = compile_rule(
    concern,  # Concern or plain text
    spark=spark_client,  # Spark 1.3; also runs the model guardrail pass
    plan_id="main-st-kitchen",
    zones=declared_zones,
    inventory=surveyed_inventory,
)
```

- `check_request` runs first (deterministic patterns, then Spark); a decline raises
  `GuardrailDeclinedError` carrying the `Declined`. A model failure fails closed (it raises).
- The hosted structured-output API rejects recursive schemas, so the model fills in
  `RuleDraft`: the RuleIR fields with the predicate flattened into `predicate_nodes` that refer
  to children by id. `draft_to_rule` rebuilds the tree, validates it as `RuleIR` and checks that
  every concept and zone it names is declared. `rule_to_draft` is the inverse.
- An invalid draft gets one repair round with the errors attached; a second failure raises
  `RuleCompilationError`. Transport errors are not retried as "invalid output".
- `apply_static_hints` marks fixed objects (sink, door, racks, shelving, exits) static by whole
  word, plus any names in `static_concepts`.

## Survey, Probe and Gauge API

```python
from rulecam.planner.steps.survey import survey_cameras
from rulecam.planner.steps.probe import probe_concepts
from rulecam.planner.feasibility import gauge_plan_feasibility

sam = HostedSam31.from_settings(settings, clock=clock, bus=bus)  # replay cache inside
inventory = survey_cameras(camera_frames, spark=spark, files=spark, segmenter=sam, seed=7)
probes = probe_concepts(["sink", "gloves", "thermometer"], camera_frames, segmenter=sam, seed=7)
verdicts = gauge_plan_feasibility(
    {"handwashing": ["sink"], "gloves for ready-to-eat food": ["gloves"]}, probes, spark=spark
)
```

- `camera_frames` maps camera id -> that camera's frames (JPEG/PNG) from the last 60 s. Frame
  sampling is seeded (`numpy.random.default_rng`). The survey samples 8 frames per camera, shows
  4 of them (round-robin across cameras, via the Files API) to Spark 1.3 for up to 30 concepts,
  and runs SAM `segment_image` on every sampled frame, concepts in parallel.
- A probe frame is a hit when SAM returns any detection. `detect_lb` is the Wilson 95% lower
  bound (`rulecam.stats.wilson_interval`). Probes sample **24** frames by default: with the task
  book's 12, a perfect 12/12 gives a lower bound of only 0.758, so nothing could be observable.
- Verdicts: guardrail decline (P2-03) -> `declined`; a required concept missing on the chosen
  camera or `detect_lb < 0.5` -> `not_observable` with an alternative; `0.5 <= detect_lb < 0.8`
  -> `caveat` with the cheapest camera fix (Spark, template fallback); otherwise `observable`.
  All of a concern's concepts must be seen by one camera. `says` is always a fixed template;
  model text only goes into `alternative`.

## Ask and Standards API

```python
from rulecam.planner.steps.ask import propose_clarifying_questions, map_tap_to_zone
from rulecam.planner.standards import StandardsLibrary, check_citation, ground_concern_standard

questions = propose_clarifying_questions(concerns, {"schedule": "06-22"}, spark=spark)
zone = map_tap_to_zone(FrameTapAnswer(camera_id="cam1", x=412, y=230), zones)

library = StandardsLibrary.from_settings(settings)  # <data>/standards_cache, RULECAM_MODE
citation = ground_concern_standard(concern, spark=spark)  # Spark 1.3 + web_search
check = check_citation(citation, library) if citation else None  # CitationCheck(matched, detail)
```

- At most 5 questions, one per missing field: questions about `known_fields` or repeated
  fields are dropped in code. A tap maps to the smallest zone polygon (or object box) on that
  camera containing the point; person detections are never matched.
- A citation is only a claim until `check_citation` finds its quote (at least 20 characters,
  compared after NFKC, case-folding, quote/dash and whitespace normalisation) in the text of the
  cited page. Pages go through a `ReplayCache` at `<data>/standards_cache/` (provider `web`):
  `record` stores the text, `replay` serves it or raises `ReplayMiss`.
- Live fetches accept only `https` to public host names (redirects re-checked), HTML or plain
  text up to 10 MB. PDF pages are refused (`StandardsFetchError`), so the check fails with an
  `unverifiable: ...` detail.

## Tests

```bash
uv run pytest tests/planner
uv run pytest tests/guardrails/test_planner_declines.py
```
