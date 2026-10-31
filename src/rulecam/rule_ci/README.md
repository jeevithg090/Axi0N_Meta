# `rulecam.rule_ci`

Rule CI part 1 (task P2-08, MP §3.4): boundary traces and mutation tests for the temporal and
metric logic of a rule, run with the real rules engine (`rulecam.rules.evaluate`). Owner: P2.
Part 2 (P2-09: Muse Image detectability, lookalikes, injection signs, alert forecast) builds on it.

## Public API

| Name | What it does |
|---|---|
| `generate_rule_traces(rule) -> list[TraceCase]` | Synthetic tracks from the rule's own thresholds and zones, each with its expected outcome. Raises `UnsupportedRuleError` for shapes it has no generator for |
| `generate_mutations(rule) -> list[MutationCase]` | Every applicable mutant: thresholds x0.1 and x10, flip `lt`/`gt`, drop `not`, swap `all`/`any`, remove `persist`, switch `semantics`, widen each zone by 50% |
| `run_rule_ci(rule, *, bus, traces, mutants, justification) -> RuleCIReport` | Runs traces, then every mutant on every trace; publishes `ci.result` (`CIResult{rule_id, report, ci_green, summary}`) |
| `prove_plan_hook(bus, *, justifications) -> PlanHook` | The planner's step 10: Rule CI for every rule, recorded as `ci_green` and `nonvacuous` checks in the acceptance trace (re-running replaces them) |
| `RuleCIReport` | `{rule_id, traces_passed, traces_total, mutants_killed, mutants_total, failures, nonvacuous, justification}`; `ci_green`, `mutation_score`, `summary()` ("boundary 13/13, mutations 5/5") |

## Traces

Supported shapes: `sequence(inside -> dwell)` (past and future), `[persist] inside`, and
`[persist] distance_m` alone or with `moving` under `all`. For the handwash rule (20 s, gap 3 s,
window 300 s) the traces are: 19.5/20.5 s washes; two 12 s washes 2 s and 4 s apart;
292.4/307.5 s waits; a wash interrupted halfway; an ID swap mid-wash; entering before washing;
and just-inside/just-outside both zones (5 cm off an edge). Expected outcomes come from how a
trace was built, never from the engine. Rules are per track, so an ID swap loses history: the
expected outcome is what per-track semantics give, including the 0.5 s a track's last point is
held. Other shapes (e.g. `persist(pose)`) get an empty report that fails `ci_green` and says why.

`nonvacuous`: every threshold and zone-edge pair has a positive member the rule fires on and a
negative member it does not. `ci_green`: all traces pass and at least 90% of mutants are killed,
or a justification is recorded.

## Tests

```bash
uv run pytest tests/rule_ci
```
