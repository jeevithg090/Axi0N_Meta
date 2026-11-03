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

### Score gates (`rulecam golden gate`, CI)

`golden verify` fails when a run's outputs change; `golden gate` fails when they are wrong. It
re-scores each scenario's recorded run with the `eval score` code and checks the `gate` in its
`demo.score` params (`src/rulecam/demo/gate.py`: `min_alerts`, `max_alerts`,
`max_false_alerts_on_negative`, `min_tp`, `max_fp`, `tier1_includes`, `outcome`). Every scenario
is gated on no false alert on a negative clip even without a `gate`. CI runs `make golden-gate`
right after `make golden-verify`.

### Recording T02 and T04 (the verified-alert and rejected-candidate goldens)

`warehouse_driver` has no candidate, so its REPLAY shows no verdict. Two runs of the 1 Nov
campaign do; both were recorded this way on 2 Nov and are now goldens (their scenario files
waited in `scenarios/pending/`, outside the `scenarios/*.yaml` glob, until then):

| Scenario | Campaign test | Clip and rule | What it shows | Gate |
|---|---|---|---|---|
| `worker_passes_4m` | T02 | `pexels_32243654`, `forklift_4m` | 2.9 m measured, Glimmer "violation" 0.99, verified alert | `min_alerts: 1`, `tier1_includes: violation` |
| `driver_only_4m` | T04 | `pexels_32243653`, `forklift_4m` | the engine flags the seated driver, Glimmer rejects it | `max_alerts: 0`, `tier1_includes: no_violation` |

The Spanish warning of the campaign was T03, the same clip with rule `forklift_4m_es`
(`Mantener a las personas a al menos 4 metros…`); record it the same way as
`worker_passes_4m_es` if the demo should show it.

On the API pod (`cd /workspace/rulecam`), when nobody is demoing, with both GPUs warm, once per
scenario (`CLIP`/`RULE`/`NAME` = `pexels_32243654`/`forklift_4m`/`worker_passes_4m`, then
`pexels_32243653`/`forklift_4m`/`driver_only_4m`):

```bash
cp .env /workspace/.env.bak && sed -i 's/^RULECAM_MODE=.*/RULECAM_MODE=record/' .env
bash scripts/ops/restart_check.sh                     # record mode, plan kept
uv run --no-sync rulecam eval run --clip $CLIP --rule $RULE --include-unlabelled \
  --out /workspace/golden/reports
curl -s localhost:8000/plans/<plan_id from the run record> > /workspace/golden/$NAME.plan.json
curl -s localhost:8000/runtime/metrics > /workspace/golden/$NAME.metrics.json
uv run --no-sync rulecam golden capture $NAME \
  --run /workspace/golden/reports/runs/<ts>/${CLIP}__${RULE}.run.json \
  --plan /workspace/golden/$NAME.plan.json --metrics /workspace/golden/$NAME.metrics.json \
  --scenarios-dir /workspace/golden/scenarios
cp /workspace/.env.bak .env && bash scripts/ops/restart_check.sh   # back to live
```

Then locally, after copying `/workspace/golden/scenarios/$NAME` into `scenarios/`:

```bash
git mv scenarios/pending/$NAME.yaml scenarios/
uv run rulecam golden record $NAME     # expected outputs; calls no model
uv run rulecam golden verify $NAME
uv run rulecam golden gate $NAME       # must pass: a T02 run without the alert is not a golden
uv run rulecam golden export $NAME     # ui/src/fixtures/golden/$NAME.json + ui/public/golden
```

and move its entry from `PENDING_RUNS` to `GOLDEN_RUNS` in `ui/src/fixtures/golden/index.ts`
(the import lines are there, commented). Glimmer is not deterministic: if a record run does not
show what the campaign showed (no candidate, or another verdict), record again rather than
loosening the gate.
