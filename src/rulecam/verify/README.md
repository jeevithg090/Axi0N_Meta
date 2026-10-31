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
  then the verifier returns `unsure` with score 0.5. It never crashes and never passes silently.
- Glimmer receives the rule text, an IR summary, the predicate trace and up to 6 strip frames
  (base64 data URLs). It copies any visible text verbatim into `scene_text` for the firewall.
- Every call publishes `relay.model_used` and appends a line to
  `evidence/glimmer/inference-log.jsonl` (model, quant, hardware, latency, request hash; never
  prompts or images). This is the open-weight prize evidence: commit it after demo runs.

## Tests

```bash
uv run pytest tests/verify tests/clients/open_weights
```

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
  Glimmer's description of the strip, labelled as Glimmer's.
- `reasoning_content` is stored in `data/reasoning/<so_id>.txt`. Each call appends a line with the
  Baseten response id to `evidence/spark_open/inference-log.jsonl`.
- A timeout or error gives `unsure` (verify) or `pending` (dispute), never a silent pass. Audit and
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

`SceneTextFirewall` scans each tier's `scene_text` with **Llama Prompt Guard 2** (gated on Hugging
Face: set `HF_TOKEN` and install `--extra ml`). On a flag it:

1. blurs the `sign` and `text` regions found by SAM (`BoxStripBlurrer`);
2. re-runs the tiers on the blurred strip;
3. sends the alert to `human_queue` if any tier now rejects it;
4. appends a `tamper_attempt` record to the chain.

A property test shows that a flagged sign never suppresses an alert (E4 under D3).
