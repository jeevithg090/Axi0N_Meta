# `rulecam.verify`: verification tiers (P3-03 onwards)

## Purpose

Every candidate from the rule engine is checked by **Muse Glimmer** (Tier 1, local, open weights).
Disputed or low-confidence ones go to **Muse Spark Open** (Tier 2, P3-04), and `policy.py` (P3-05)
decides what happens. Text seen in frames is data, never instructions.

## Running Glimmer locally (llama.cpp)

```bash
# Pick the quant by RAM: 24 GB → k-quant-17G, 32 GB+ → k-quant-dynamic.
# Confirm the exact repo and file names on Hugging Face before the take.
llama-server -hf meta-models/Muse-Glimmer-30B-GGUF --port 8080
# or: llama-server -m /path/to/muse-glimmer-30b-<quant>.gguf --port 8080
curl http://127.0.0.1:8080/v1/models     # make preflight checks this too
```

`.env`: `GLIMMER_BASE_URL=http://127.0.0.1:8080/v1`, `GLIMMER_MODEL`, `GLIMMER_QUANT` and
`GLIMMER_HARDWARE`. The last two are written to the evidence log.

## Public API

```python
from rulecam.clients.open_weights.glimmer import GlimmerClient
from rulecam.verify import GlimmerVerifier

glimmer = GlimmerClient.from_settings(settings, cache=cache, clock=clock, bus=bus)
verdict = GlimmerVerifier(glimmer).verify(candidate, rule)  # VerifierVerdict, tier="glimmer"

# Generic structured calls (back-translation, translation, Site Advisor recompute):
answer = glimmer.structured(model=glimmer.model, messages=msgs, schema=MySchema)
```

- Output is constrained with `response_format: json_schema`. A malformed reply is retried once,
  with a corrective message (so the retry is a new request and a new replay-cache key), then
  the verifier returns `unsure` with score 0.5. It never crashes and never passes silently.
- Glimmer receives the rule text, an IR summary, the predicate trace and up to 6 strip frames
  (base64 data URLs). It copies any text visible in the scene verbatim into `scene_text` for the
  firewall. Rulecam's own caption and legend are in an annotation band below each frame, which
  the prompt says is ours and not scene text; any copy of a string we drew (listed in the strip's
  `annotations.json`) is removed from `scene_text` before the firewall (`verify.annotations`:
  multi-word strings anywhere, one-word labels only as a whole line, nothing else touched).
- Every call publishes `relay.model_used` and appends a line to
  `evidence/glimmer/inference-log.jsonl` (model, quant, hardware, latency, request hash; never
  prompts or images). Calls served from the replay cache are not logged, since no inference ran.
  This is the open-weight prize evidence: commit it after demo runs.

## Tests

```bash
uv run pytest tests/verify tests/clients/open_weights
```

## Tier 0: crop-zoom findings for Glimmer (add-on 3.3)

`verify.tier0.CropZoomCheck` runs before Glimmer for a rule with a `without` predicate (hair net,
gloves, hat). It decodes 3 keyframes of the episode at full camera resolution
(`runtime.strips.EpisodeFrames`), cuts out the subject's head, hands or body (the predicate's
region; wrists from pose when known) and asks SAM 3.1 for each item on the enlarged crops
(`perception.crop_zoom`, the GPU pod's `/segment_crops` with hosted SAM as the fallback). The
counts (crops checked, crops with a hit, keyframes, best confidence) become a few lines in
Glimmer's prompt, `GlimmerVerifier(context=...)`, labelled as a detector's hints to weigh, not a
verdict. Rules without `without` add nothing; any failure adds nothing (logged `tier0_failed`).
`RULECAM_CROP_ZOOM` (on by default) turns it on; it is never built in replay mode, because its
lines would change Glimmer's prompts and so the replay cache's keys.

## Tier 2: Muse Spark Open on Baseten (P3-04)

```python
from rulecam.clients.open_weights.spark_open import SparkOpenClient
from rulecam.verify.tier2 import SparkOpenVerifier

spark = SparkOpenVerifier(
    SparkOpenClient.from_settings(settings, cache=cache, clock=clock, bus=bus)
)
spark.verify(candidate, rule, tier1)  # reasoning verbatim in VerifierVerdict.reasoning
spark.review_dispute(candidate, rule, dispute)  # upheld | rejected (pending on failure)
spark.audit_plan(plan)  # list[AuditFinding] with reasoning_ref
spark.devil_advocate({"metric": ..., "leading": ...})  # list[str], tested by the caller
```

- `.env`: `BASETEN_API_KEY`, `BASETEN_BASE_URL` (default `https://inference.baseten.co/v1`),
  `BASETEN_SPARK_OPEN_MODEL` (confirm the `-1026` ID), and `SPARK_OPEN_IMAGES_SUPPORTED`. Leave
  that one `false` until a day-one call confirms images work; the text-only path then sends
  Glimmer's description of the strip, labelled as Glimmer's. With no Glimmer verdict to pass,
  a text-only `verify` returns `unsure` without calling Spark Open.
- `reasoning_content` is stored in `data/reasoning/<so_id>.txt`. The id is content-addressed
  (`so_` + the first 16 hex of its SHA-256), so replayed runs give identical ids. Each call appends a line with the
  Baseten response id to `evidence/spark_open/inference-log.jsonl`.
- A timeout or error gives `unsure` (verify, with the elapsed latency) or `pending` (dispute),
  never a silent pass. Audit and
  devil's-advocate failures raise, so callers can show them as "not audited".
- Live check: `make live-test` runs `tests/verify/test_tier2_live.py`.

## Policy and scene-text firewall (P3-05)

`VerificationPolicy` is the runtime `Policy`:

- **HIGH severity:** `act_first` is true, so the runtime runs the actions (stop, power cut) before any
  model is asked. A veto, `unsure` or disagreement sends the alert to `human_queue`; a HIGH alert is
  never dropped.
- **LOW and MEDIUM:** verify first. Tier 1 confirming at the threshold alerts. Otherwise Tier 2 is
  asked (`spark-open-on-disagree`), and Tier 2 confirming alerts. The alert is suppressed only when
  every tier that ran rejects it with confidence; anything else goes to `human_queue`.
- **Threshold:** the rule's calibrated one (`thresholds` from rule health), else
  `verify.min_confidence`.
- **Disputes:** always go to Tier 2 (`review_dispute`).
- **Errors:** if Tier 2 or the firewall fails (model error, unreadable strip, missing extra), the
  alert goes to `human_queue` with `alert=True`; it is never dropped.

`SceneTextFirewall` scans each tier's `scene_text` with **Llama Prompt Guard 2** (gated on Hugging
Face: set `HF_TOKEN` and install `--extra ml`). Text is flagged when its malicious score is **at or
above** the threshold (default 0.5; a tie counts as an attack). On a flag it:

1. blurs the `sign` and `text` regions found by SAM (`BoxStripBlurrer`);
2. re-runs the tiers on the blurred strip;
3. sends the alert to `human_queue` if any tier now rejects it;
4. appends a `tamper_attempt` record to the chain.

A property test shows that a flagged sign never suppresses an alert (E4 under D3).

## When Tier 1 fails: never silent (P3-05, `guard.py`)

The runtime's Tier 1 is `GuardedVerifier(GlimmerVerifier(client, raise_errors=True))`
(`runtime.factory.build_tier1`). A verification that gives no verdict (pod stopped or
unreachable, timeout, 5xx, bad reply, unreadable strip) is retried once if transient, then
recorded as an `unsure` verdict whose reason starts `verifier_error: ` (no contract change). The
policy's `decide_unverified` sends it to the human queue ("Needs review: verifier unavailable —
<reason>") for every severity; Tier 2 may confirm it first when the rule allows it. A circuit
breaker opens after 3 transient failures in a row, so later candidates fail fast (no timeout)
until a trial after the cooldown, or a healthy `/health` probe, gets through. Its state is in
`/runtime/metrics` (`verifier`, `verifier_down`) and the UI header. Details: docs/deploy.md,
"When Tier 1 fails".

## Rule health: measured precision and SPRT auto-pause (P4-09, `health.py`)

A rule that fires wrongly costs more than a rule that does not fire, because people stop
trusting the system and then stop reading any of it. So each rule's precision is tracked from
the labels humans gave, and a rule whose precision has dropped is paused automatically, with a
reason, instead of running until somebody notices.

```python
from rulecam.verify.health import RuleHealthMonitor, read_labels

monitor = RuleHealthMonitor(chain)  # publishes `rule_change` records
labels = read_labels(read_chain(chain.path_for(today)))
severities = {rule.id: rule.severity for rule in plan.rules}  # missing = treated as HIGH
monitor.assess_all(labels, severities=severities, alerts={"forklift_2m": 120})
loop.set_plan(plan.with_paused(monitor.paused_rules()))  # paused rules stop being evaluated
monitor.review_rules()  # HIGH rules whose test failed: still running, waiting for a person
monitor.status("forklift_2m"), monitor.reason("forklift_2m")  # running/paused/needs_review
monitor.resume("forklift_2m", role="owner", note="zone redrawn")  # a person's decision
```

### What counts as evidence

| Record | Meaning |
|---|---|
| `label` with `label: false_alarm` | False positive (the "False alarm" button, `act/api.py`) |
| `label` with `label: confirmed` | True positive — **nothing writes this yet** (see the gap below) |
| `dispute`, outcome `upheld` | The reviewer agreed with the worker: false positive (model-decided) |
| `dispute`, outcome `rejected` | The reviewer looked and kept the alert: true positive (model-decided) |
| `label` with `source: worker_dispute` | The label an upheld dispute writes: false positive (model-decided) |
| `label` or `dispute` with no `rule_id` | Says nothing about any rule: logged and skipped |
| `dispute`, outcome `pending` | Nobody has reviewed it, so it says nothing either way |
| `label` with `drill: true` | A rehearsal, not a real alert: skipped |

**An alert nobody labelled is not evidence and is not counted.** Counting unlabelled alerts as
correct is the obvious way to make precision look good, so `RulePrecision` reports `unlabelled`
and `label_share` next to the estimate: a precision of 1.0 from two labels and four hundred
unlabelled alerts should reassure nobody. One alert judged twice (a false-alarm label and then
an upheld dispute) is one trial, and the latest judgement wins.

Disputes are reviewed by the Tier 2 model (Spark Open, `worker/disputes.py`), not by a person.
Those judgements still count, but each label carries `decided_by: model`, `RulePrecision`
reports `model_decided`, and a pause reason says how many of its judgements the model made.

The interval is Wilson 95% over the labelled alerts. It accounts for how few labels there are
and for nothing else: not for who labelled them, not for labellers seeing each other's
decisions, not for a label that is itself wrong.

### The test

A Wald SPRT on precision, H0 `p = p0` (healthy, default 0.80) against H1 `p = p1` (unhealthy,
default 0.50), with `alpha` 0.05 and `beta` 0.10. The statistic is the log-likelihood ratio in
favour of H0, so it moves down on each false positive, and the boundaries are
`log((1 - alpha) / beta)` above (healthy, stop) and `log(alpha / (1 - beta))` below (**pause**).
At the defaults, four wrong alerts in a row cross the lower boundary. Between the boundaries the
test keeps going and nothing happens: a rule with three labels is left alone.

One run of the test stops at the label that crossed — that is what keeps its error rates true.
So that an early "healthy" does not hide what comes after it, the test **restarts** from the next
label each time it calls a rule healthy (`sequential_sprt`): five confirmed alerts and then a
hundred false alarms pause the rule at the ninth judgement. Each run keeps Wald's error rates;
over many runs the chance of some false pause grows, which is the price of still looking.

A LOW or MEDIUM rule is paused once, publishing one `rule_change` record (`change: paused`).
**A HIGH rule is never paused**: it acts first, and pausing it on four "False alarm" taps would
silently switch a safety rule off on every camera. Its failing test publishes one `rule_change`
with `change: needs_review` and a `rule_needs_review` warning, the rule keeps running, and a
person decides. `RuleHealth.status` and `monitor.status()` / `reason()` show which.

Resuming (or clearing a review flag) is always a person's call, because whatever made the rule
wrong is still there until someone changes it. The `resumed` record carries `resumed_at`, and
the test starts again from labels recorded after it, so the evidence behind the pause cannot
pause the rule a second time; new false alarms after the resume can. Precision and its interval
are still measured over every label.

### What this does not tell you

- The SPRT assumes each labelled alert is an independent draw at a fixed precision. Neither
  holds exactly: alerts cluster (one confusing scene makes several), and precision changes when
  the site changes. **Treat a pause as "look at this rule now", not as a measurement.**
- The error rates are about the test, not about the labels. Systematically wrong labels give a
  confidently wrong answer.
- Not firing is invisible here. Precision says nothing about recall, so a rule can be paused for
  being wrong and a rule that never fires at all looks perfectly healthy.

### Known gaps

- **Nothing writes `label: confirmed` yet.** Today only the "False alarm" button writes a label,
  so the only evidence is negative and a busy, correct rule accumulates no true positives. Until
  the acknowledgement path records a confirmation, precision is measured on disputes plus false
  alarms alone, which biases it down. The vocabulary is in place for whoever adds it.
- Nothing calls `assess_all` on a schedule yet. The monitor is built and the runtime honours
  `ActivePlan.paused`, but wiring the periodic read of the chain into the runtime or the API is
  not done.
