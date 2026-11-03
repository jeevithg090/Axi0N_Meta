# `rulecam.chain`: Chain Mode, one rule pack for many stores

**Problem.** A franchise with 20 kitchens cannot visit each one, and every store sets up its
rules from zero. Head office has no way to know which stores actually run the rules, which
cameras are pointing at a wall, or which kitchen needs help, without watching people.

**What it does.** Head office writes a **rule pack** once. Rulecam proves it, fits it to each
store's own cameras, runs Rule CI again at every store, and shows head office one scorecard per
store and rule, never per worker, with a fix-first list and one evidence root for the whole
chain.

Try it (no models, no network, about 10 s):

```bash
uv run rulecam chain demo chains/kitchen-basics
```

## The loop

| Step | Module | What happens |
|---|---|---|
| 1. Write | `models.RulePack` | A versioned pack of rule templates. Zones are **slots** in words ("the hand sink nearest the line"); thresholds stores may tune are **knobs** with an HQ range and a safer direction |
| 2. Prove | `pack.prove_pack` | Lint (guardrails, slots, knobs), then Rule CI at the default **and at both ends of every knob's range**. A pack is releasable only when every allowed setting is green |
| 3. Fit | `fit.fit_store` | Each store maps slots to its own zones and cameras (or they bind themselves), sets knobs inside HQ's range, may raise severity, never lower it. Lint and Rule CI run again on the store's real zones and calibrations |
| 4. Score | `scorecard.chain_scorecard` | Compliance per store and rule with BLIND time kept out, a Wilson 95% interval, a small-crew floor and a fix-first list |
| 5. Commit | `rollup.chain_root` | One daily HQ Merkle root over every store's root, with an inclusion proof per store that checks on its own |

## What a store gets back

Every rule at every store gets one status and one line in plain words:

| Status | Meaning | Example |
|---|---|---|
| `ready` | Runs; Rule CI passed on this store's zones | "Runs on kitchen-cam; Rule CI boundary 13/13, mutations 5/5 on this store." |
| `needs_setup` | A zone, camera, calibration or alert channel is missing | "no alert channel for role 'kitchen_lead': the alert would go nowhere" |
| `blocked` | A guardrail stops it | "zone 'staff_break_room' names a private space and can never be watched" |
| `failed` | Lint or Rule CI is red on this store's zones | |

Refused overrides are warnings, and the pack's value runs instead:
"wash_seconds=10s refused: HQ allows 20-40s; the pack's 20s runs".

## Honest numbers

- **BLIND is not compliant.** Outcomes during BLIND time count on neither side
  (`perception.coverage.tally_compliance`). Time with no video at all is BLIND (`no_video`).
- **Small crews are not singled out.** Below `MIN_OUTCOMES` (5) watched outcomes, no rate is
  shown for that store and rule.
- **"Below the chain" needs evidence.** A store is flagged only when the top of its Wilson 95%
  interval is below the chain's pooled rate.
- **Provenance is never mixed.** The demo's numbers come from `simulate.py` and are printed
  SIMULATED; one scorecard holds one provenance.

## Guardrails (rule R13)

- Results are per store and per rule. No model has a track, person or worker field, and no
  Chain Mode source touches `track_id` (`tests/guardrails/test_chain_store_level.py`).
- A pack concern the planner would decline (`planner.guardrails.deterministic_check`) stops the
  pack. A slot, or a store zone, named after a private space is refused, prototype mode or not.
- A store can never drop below the pack's privacy floor.

## Public API

`load_pack`, `parse_pack`, `lint_pack`, `prove_pack`; `load_store`, `load_stores`, `fit_store`,
`fit_chain`; `chain_scorecard`; `chain_root`, `inclusion_proof`, `verify_inclusion`,
`load_roots`; `simulate`, `load_simulation`; `build_report`, `render_report`, `render_proof`,
`render_fits`. Models are in `models.py`; they are Chain Mode's own, not shared contracts.

## CLI

```bash
uv run rulecam chain prove chains/kitchen-basics/pack.yaml          # exit 1 if not releasable
uv run rulecam chain fit chains/kitchen-basics/pack.yaml chains/kitchen-basics/stores --out fits.json
uv run rulecam chain demo chains/kitchen-basics --out report.json
uv run rulecam chain rollup 2026-11-02 store-01/roots.txt store-07/roots.txt --proofs proofs/
uv run rulecam chain verify-proof proofs/2026-11-02-store-01.proof.json
```

## Tests

```bash
uv run pytest tests/chain tests/guardrails/test_chain_store_level.py
```

## Next (not built yet)

1. **Rollout waves.** Give a new pack version to 2 pilot stores, backtest it on their stored
   tracks, then promote to every store; roll back by pinning the old version.
2. **Shared false-alarm memory.** A false alarm Glimmer rejects or a worker dispute upholds at
   one store becomes a permanent Rule CI case in the pack, so every store gets the fix.
3. **HQ page and API.** `/chain` routes and a UI page that show this report live.
4. **Store plan approval.** Turn a store's fit into a `SitePlan` the store owner approves
   through the normal `/plans` flow.
5. **New store in 10 minutes.** Pair-a-Phone plus auto-binding: the feasibility gauge says which
   slots still need a camera.
