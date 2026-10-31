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

## Pipeline and persistence (P2-06a)

```python
from rulecam.planner.pipeline import PipelineInput, PlannerPipeline, PlanStore

pipeline = PlannerPipeline(
    spark=spark, files=spark, segmenter=sam, voice=voice, bus=bus, store=PlanStore(Path("plans"))
)
plan = pipeline.run(
    PipelineInput(
        plan_id="main-st",
        site_name="Main St",
        request=items,
        camera_frames=frames,
        zones=zones,
        seed=7,
    )
)
plan = pipeline.resume("main-st")  # after a crash: first step without an output
plan = pipeline.resume("main-st", "compile")  # re-run steps 6-10 on the saved steps 1-5
```

- Steps, always in this order: understand, survey, probe, gauge, ask, compile, lint, accept,
  audit, prove. Inputs go to `plans/<id>/input.json`, each step's typed output to
  `plans/<id>/steps/<NN>_<step>.json`, the plan to `plans/<id>.plan.yaml`; all writes are
  atomic. Running a step deletes its own and every later output first, so nothing stale
  survives a crash. Plan ids must be safe file names (`PipelineError` otherwise).
- Probe asks Spark 1.3 which concepts each concern needs (`concepts_for_concerns`), then probes
  them. Compile runs only for `observable` and `caveat` concerns; a compile-time guardrail
  decline turns the concern's verdict into `declined`.
- Step 7 lints the draft plan (below) against the site's cameras and saves
  `LintResult{plan, findings}`; its detail counts the findings by code.
- Step 6 keeps a concern whose rule fails to compile (`CompileResult.planned[i].error`) instead
  of failing the run; step 8 repairs it or asks about it.
- Step 8 runs the acceptance checks and repair loop (below) and saves `AcceptResult{plan,
  questions}`. Steps 9-10 are `SitePlan -> SitePlan` hooks (`audit`, `prove`) for P2-07 and
  P2-08/09. Until one is wired it passes the plan through and its step detail says "not wired
  yet". `PipelineInput.calibrations` feeds `calibration_err<=tol`; `grounding` (Spark with web
  search) and `standards` (`StandardsLibrary`) feed `citation_text_match`.
- Every step publishes `planner.step` (`PlannerStepEvent{plan_id, step, status, detail}`,
  status `running` then `done` or `failed`; resumed steps report `done`, "loaded from the
  saved output"). The plan is saved with version = saved version + 1 (never overwriting the
  same or a newer version), then published on `plan.updated`.
- `planner/runner.py::PlannerService` is the `PlannerRunner` behind `POST /plans/requests`: it
  runs one plan at a time in a worker thread and puts finished plans in the `/plans` registry.
  `ChunkFrames` gives the survey the last frame of each camera's chunks from the last 60 s.

## Plan linter (P2-06b)

```python
from rulecam.planner.lint import lint_plan, require_lint_clean
from rulecam.planner.pipeline import camera_views

views = camera_views(frames)  # camera id -> (width, height) of its latest readable frame
findings = lint_plan(plan, views=views)  # tuple[LintFinding{code, rule_id, message}]
require_lint_clean(plan, views=views)  # raises PlanLintError(findings)
```

- Deterministic, no model calls. Codes: `undefined_concept`, `undefined_zone`,
  `zone_out_of_view` (camera not at the site, or polygon outside its frame), `unreasonable_threshold`
  (distance or buffer outside 0.3-20 m, duration outside 1-3,600 s), `contradictory_rules` (same
  subject and zone, inside vs. not inside or absent; within an `all` or across two rules),
  `privacy_inconsistency` (Anonymous/Strict Mode with a face concept), `private_space_zone`
  (zone names matched with the guardrails' private-space patterns), `missing_action`,
  `missing_severity`.

## Acceptance checks and repair loop (P2-06c)

```python
from rulecam.planner.acceptance import PlannedRule, RepairLoop, SiteFacts

loop = RepairLoop(facts, recompile=recompile, probe=probe, verify_citation=verify)
outcome = loop.run(planned, feasibility)  # rules, feasibility, acceptance, questions
```

- Measured here, per checkable concern: `concern_covered` (a rule compiled, without structural
  lint errors), then per rule `zone_in_view` (polygon wholly inside the camera's latest frame),
  `detect_lb>=0.8` (unprobed concepts are probed first), `calibration_err<=tol` (metric rules,
  `err_m <= 0.10`), `privacy_consistent`, `citation_text_match` (Spark grounding + quote on the
  cited page) and `no_private_spaces`. `ci_green` and `nonvacuous` are left to Rule CI (P2-08).
- Strict AND; the trace keeps the last round's failures. Only items failing `concern_covered`,
  `zone_in_view`, `privacy_consistent` or `no_private_spaces` are re-compiled, with the failures
  as feedback, for at most 2 rounds; a repair that lowers severity or drops an action is
  rejected. Still failing: the rule is removed and becomes a question plus a `caveat` verdict.
  Failing only `detect_lb>=0.8`, `calibration_err<=tol` or `citation_text_match` (camera,
  calibration or source fixes): the rule stays, with a `caveat` verdict saying why.

## Tests

```bash
uv run pytest tests/planner
uv run pytest tests/guardrails/test_planner_declines.py
```
