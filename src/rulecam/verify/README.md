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
  (base64 data URLs). It copies any visible text verbatim into `scene_text` for the firewall.
- Every call publishes `relay.model_used` and appends a line to
  `evidence/glimmer/inference-log.jsonl` (model, quant, hardware, latency, request hash; never
  prompts or images). Calls served from the replay cache are not logged, since no inference ran.
  This is the open-weight prize evidence: commit it after demo runs.

## Tests

```bash
uv run pytest tests/verify tests/clients/open_weights
```
