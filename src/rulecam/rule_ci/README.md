# `rulecam.rule_ci`

Rule CI part 1 (task P2-08, MP §3.4): boundary traces and mutation tests for the temporal and
metric logic of a rule, run with the real rules engine (`rulecam.rules.evaluate`). Part 2
(P2-09): Muse Image detectability scenes, the lookalike red-team, injection signs and the alert
forecast. Owner: P2.

## Public API

| Name | What it does |
|---|---|
| `generate_rule_traces(rule) -> list[TraceCase]` | Synthetic tracks from the rule's own thresholds and zones, each with its expected outcome. Raises `UnsupportedRuleError` for shapes it has no generator for |
| `generate_mutations(rule) -> list[MutationCase]` | Every applicable mutant: thresholds x0.1 and x10, flip `lt`/`gt`, drop `not`, swap `all`/`any`, remove `persist`, switch `semantics`, widen each zone by 50% |
| `run_rule_ci(rule, *, bus, traces, mutants, justification) -> RuleCIReport` | Runs traces, then every mutant on every trace; publishes `ci.result` (`CIResult{rule_id, report, ci_green, summary}`) |
| `prove_plan_hook(bus, *, justifications, red_team) -> PlanHook` | The planner's step 10: Rule CI for every rule (part 2 too when `red_team` is given), recorded as `ci_green` and `nonvacuous` checks in the acceptance trace (re-running replaces them) |
| `RedTeam(...)(rule) -> RedTeamReport` | Part 2 for one rule on its real camera frame: scenes, lookalikes, injection signs, forecast; a stage that cannot run is listed in `skipped` with why |
| `run_detectability_scenes`, `run_lookalike_red_team`, `run_injection_red_team`, `forecast_alerts` | The four stages on their own |
| `append_regression(rule_id, item)` | Adds a confirmed real false alarm to `regressions/<rule_id>.jsonl`, a permanent lookalike test |
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

## Part 2 (P2-09)

- **Scenes**: Spark writes positive, negative and edge cases naming which rule concepts must be
  present or absent; Muse Image renders each with the real frame as reference; SAM checks each
  concept by its prompt. A failing case is regenerated at most twice (3 renders), with what was
  wrong added to the prompt. Reported as a pass rate; detection is gated by `detect_lb>=0.8`.
- **Lookalikes**: Spark lists lookalikes per concept; each is rendered into the real frame. SAM
  finding the imitated concept, or Tier 1 confirming a violation, means `fooled`, with a fix
  (negative concept if SAM was fooled, else a concept-prompt tweak). The committed regressions
  in `regressions/` are tested on every run.
- **Injection signs**: six signs composited with PIL onto the positive scenes SAM confirmed,
  then Tier 1 and the runtime policy with its scene-text firewall. The alert must be kept;
  `tamper` means the firewall appended a `tamper_attempt` record.
- **Forecast**: per camera, backtest candidates over hours observed, summed over cameras, times
  the owner's shift length, against the owner's budget (`AlertBudget`). Unavailable, not zero,
  when the stored tracks lack the rule's concepts.
- `ci_green` also needs part 2 to pass when it ran: no fooled lookalike, no silenced alert.

## Tests

```bash
uv run pytest tests/rule_ci
```
