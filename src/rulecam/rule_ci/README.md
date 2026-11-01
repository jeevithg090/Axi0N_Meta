# `rulecam.rule_ci`

Rule CI part 1 (task P2-08, MP §3.4): boundary traces and mutation tests for the temporal and
metric logic of a rule, run with the real rules engine (`rulecam.rules.evaluate`). Part 2
(P2-09): Muse Image detectability scenes, the lookalike red-team, injection signs and the alert
forecast. Owner: P2.

## Public API

| Name | What it does |
|---|---|
| `generate_rule_traces(rule, *, calibrations) -> list[TraceCase]` | Synthetic tracks from the rule's own thresholds and zones, each with its expected outcome. Raises `UnsupportedRuleError` for shapes it has no generator for, or a buffer zone without a calibration |
| `generate_mutations(rule) -> list[MutationCase]` | Every applicable mutant: thresholds x0.1 and x10, flip `lt`/`gt`, drop `not`, swap `all`/`any`, remove `persist`, switch `semantics`, widen each zone by 50%, and `drop_operator_exclusion` for person-vehicle rules (`MutationCase.operator_exclusion=False`) |
| `run_rule_ci(rule, *, bus, traces, mutants, justification, red_team, calibrations) -> RuleCIReport` | Runs traces, then every mutant on every trace; publishes `ci.result` (`CIResult{rule_id, report, ci_green, summary}`) |
| `prove_plan_hook(bus, *, justifications, red_team, calibrations) -> PlanHook` | The planner's step 10: Rule CI for every rule (part 2 too when `red_team` is given; buffer traces through the run's calibrations, `api.app.run_calibrations`), recorded as `ci_green` and `nonvacuous` checks in the acceptance trace (re-running replaces them) |
| `RedTeam(...)(rule) -> RedTeamReport` | Part 2 for one rule on its real camera frame: scenes, lookalikes, injection signs, forecast; a stage that cannot run is listed in `skipped` with why |
| `run_detectability_scenes`, `run_lookalike_red_team`, `run_injection_red_team`, `forecast_alerts` | The four stages on their own |
| `append_regression(rule_id, item)` | Adds a confirmed real false alarm to `regressions/<rule_id>.jsonl`, a permanent lookalike test |
| `HardNegatives(directory).from_dispute(...)` / `.from_correction(...)` | WS1f: an upheld dispute or an owner correction becomes one `append_regression` line (fixed wording only, never the worker's text; once per event; failures logged, never raised). Wired by the API into `DisputeService` and `CorrectionService` |
| `RuleCIReport` | `{rule_id, traces_passed, traces_total, mutants_killed, mutants_total, failures, nonvacuous, justification}`; `ci_green`, `mutation_score`, `summary()` ("boundary 13/13, mutations 5/5") |

## Traces

Supported shapes (`generate.py` picks the generator): `sequence(inside -> dwell)` (past and
future), `[persist] inside` on a polygon zone, `[persist] inside` on a buffer zone alone or with
`moving` (of the subject or of the buffer's concept) under `all`, and `[persist] distance_m` alone
or with `moving` under `all`. For the handwash rule (20 s, gap 3 s,
window 300 s) the traces are: 19.5/20.5 s washes; two 12 s washes 2 s and 4 s apart;
292.4/307.5 s waits; a wash interrupted halfway; an ID swap mid-wash; entering before washing;
and just-inside/just-outside both zones (5 cm off an edge). Expected outcomes come from how a
trace was built, never from the engine. Rules are per track, so an ID swap loses history: the
expected outcome is what per-track semantics give, including the 0.5 s a track's last point is
held. Other shapes (e.g. `persist(pose)`) get an empty report that fails `ci_green` and says why.

**Buffer zones** (`buffer_traces.py`). "Keep people at least 2 metres away from a moving
forklift" compiles either to `all(distance_m(person, forklift) < 2, moving(forklift))` or to
`all(inside(person, forklift_buffer), moving(forklift))` with `forklift_buffer` =
`{kind: buffer, buffer_of: forklift, buffer_m: 2}`. The forklift drives along the ground; the
person keeps a set distance from the projected bottom edge of its box (its footprint). Traces:
1.95/2.05 m from the footprint (`zone_inside`/`zone_outside`), forklift at 0.21/0.19 m/s and
standing still, then the persist pair, an interruption and an ID swap (with `persist`) or an
interruption and an ID swap that still fire (without). Mutants: thresholds x0.1/x10 (speed,
persist), `swap_all_any`, `remove_persist`, and `widen_zone` (`buffer_m` x1.5, killed by the
2.05 m trace). Metres need a ground plane: the traces go through the zone camera's real homography
when the run has one (`calibrations`), else the synthetic plane when the rule names
`ground-plane@<camera>`; with neither, the report fails with "buffer zone needs calibration".
After layout, shapely re-measures each sample's distance and the moving object's speed against
the design, so a homography that bends the layout is refused rather than mis-expected.

*Decision (updated, WS1e): the compiler canonicalises.* Live, the forms behaved differently, and
one compile wrote the inverted `distance_m(gt=2)`, which Rule CI then proved against its own
(wrong) semantics. `planner.canonical.canonicalize_rule` now rewrites every person-vehicle
separation rule after compile into `[persist] all(distance_m(person, vehicle, lt=d),
moving(vehicle))`; buffer zones remain for fixed things and polygons the owner drew. Rule CI
still proves both forms, so a buffer rule written by hand keeps working.

**Kinematic and operator traces** (`kinematic_traces.py`, WS1c/WS1d). For `distance_m(lt=d)` and
buffer rules: an approach pair (drives straight at the other and turns `d -/+ eps` away) and a
pass-by pair (passes `d -/+ eps` to the side), both without `persist`; and a co-moving trace
3.2 d away (6.4 m for 2 m, the live far candidate: `closest_m` 6.4, `ttc_s` none), which must
never fire and kills `flip_lt_gt`. When the subject is person-like and the other vehicle-like:
a person riding the vehicle inside its box `min(d/2, 0.5)` m from its footprint (must not fire:
operator exclusion, `rules.operators`), a pedestrian walking beside it at the same distance and
one standing in front of it while it passes (both must fire); and the live P1-07 close-up, a
vehicle just above `moving`'s threshold whose box fills the 720 px frame: a person riding it
(must not fire) and a pedestrian it drives up to and over (must fire). These rules also get the
`drop_operator_exclusion` mutant (the same rule run with exclusion off), killed by the riding
traces. Kinematic traces use the synthetic plane even when the buffer traces use a real
homography: they test the rule's kinematics and the image-box operator logic, not the camera.

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
