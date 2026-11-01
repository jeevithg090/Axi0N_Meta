# Golden-run scenarios

A golden run is a scripted flow recorded once with real model calls and replayed forever with
none: `rulecam golden verify <name>` (CI job `golden-verify`, `make golden-verify`) re-derives
every step offline and fails on any change. One YAML file per flow, plus its verified outputs:

```
scenarios/<name>.yaml            the steps (kind + params)
scenarios/<name>.expected.json   their outputs, written by `rulecam golden record <name>`
scenarios/<name>/                the recorded run (recorded-run scenarios only, below)
```

Step kinds are registered by the packages that own them (`rulecam.replay.golden.register_step`).

## Recorded-run scenarios (`demo.*` steps): the site's REPLAY demo

These freeze one **live** eval run, played through the deployed API with `RULECAM_MODE=record`,
into `scenarios/<name>/` (`rulecam.demo`, layout in `src/rulecam/demo/fixture.py`): the eval run
record, the approved plan, the runtime's policy configuration, the day's evidence chain, the
track window of each candidate, the Tier 1 strips (the runtime's JPEGs, about 35 KB each; never
video) and the recorded Glimmer reply each strip was sent with.

| step kind | re-derives, offline |
|---|---|
| `demo.plan` | the plan's rules and their hashes against the run's, canonical form, acceptance trace, planner steps |
| `demo.rule_ci` | Rule CI on every rule, compared with the acceptance trace |
| `demo.evidence` | chain integrity from the day's first record, Merkle root, each candidate re-derived from its track window |
| `demo.verify` | Tier 1 from the recorded reply (cache key = the same request), then the policy's decision, both against the chain |
| `demo.score` | the eval harness's score of the run against the clip's labels |

`warehouse_driver` is the first: the stock driver-only clip (`eval/clips/warehouse_45848`, a
negative; since the operator warm-up, P1-07, a clean one with no candidate). The site plays it on `?demo=1` (`/demo`, `/planner`, `/plans/<id>`, `/monitor`) and
its chain on `/verify/index.html?chain=…`.

### Adding a run (e.g. the owner's positive clip)

The same pipeline for any eval clip; the UI already shows a slot for `owner_fl_pos_1m`.

```bash
# on the API pod (cd /workspace/rulecam), when nobody is demoing
cp .env /workspace/.env.bak && sed -i 's/^RULECAM_MODE=.*/RULECAM_MODE=record/' .env
bash scripts/ops/restart_check.sh                       # restart into record mode, plan kept
uv run --no-sync rulecam eval run --clip owner_fl_pos_1m --out /workspace/golden/reports
curl -s localhost:8000/plans/<plan_id> > /workspace/golden/plan.json
curl -s localhost:8000/runtime/metrics > /workspace/golden/metrics.json
uv run --no-sync rulecam golden capture owner_fl_pos_1m \
  --run /workspace/golden/reports/runs/<ts>/owner_fl_pos_1m__forklift_2m.run.json \
  --plan /workspace/golden/plan.json --metrics /workspace/golden/metrics.json \
  --scenarios-dir /workspace/golden/scenarios
cp /workspace/.env.bak .env && bash scripts/ops/restart_check.sh   # back to live

# locally, after copying /workspace/golden/scenarios/owner_fl_pos_1m into scenarios/
cp scenarios/warehouse_driver.yaml scenarios/owner_fl_pos_1m.yaml   # then edit name, paths, text
uv run rulecam golden record owner_fl_pos_1m    # writes the expected outputs; calls no model
uv run rulecam golden verify owner_fl_pos_1m
uv run rulecam golden export owner_fl_pos_1m    # ui/src/fixtures/golden + ui/public/golden
```

Then add the bundle to `GOLDEN_RUNS` in `ui/src/fixtures/golden/index.ts` (and remove its
`PENDING_RUNS` entry). `capture` fails rather than guess when a reply was not recorded, a strip
is missing or the track window is gone. Scan the fixture for secrets before committing (it holds
model replies and chain records, never keys).
