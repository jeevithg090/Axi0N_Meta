# `rulecam.advisor`: Site Advisor

Business advice you can check (docs/site-advisor.md). Owners: `process.py` P1, `pipeline.py` P2,
`stats/` and `whatif/` P4, `api.py` P3. mypy strict; coverage floor 85%.

## `process.py`: process log and quality gates (P1-12)

Turns tracks into a station process log, OCEL 2.0-shaped, in DuckDB (SA §4, steps 3–4).

```python
from rulecam.advisor.process import (
    assess_quality, build_process_log, stations_from_zones, write_duckdb,
)

stations = stations_from_zones(plan_zones)  # zone ids dock_* and aisle_*
log = build_process_log(tracks, stations, t0=t0, t1=t1, provenance=Provenance.RECORDED)
write_duckdb(log, duckdb.connect("data/advisor.duckdb"))
report = assess_quality(log, tracks, stations, coverage=watchdog)  # CoverageWatchdog (P1-08)
```

**Tables.** Timestamps are UTC `TIMESTAMP`. Every row has a `provenance` column.

| Table | Columns |
|---|---|
| `objects` | `object_id, type` (`truck`, `pallet` or `forklift`) |
| `events` | `event_id, activity, ts, station` |
| `event_object` | `event_id, object_id, qualifier` (`truck`, `handler`, `pallet` or `blocker`) |
| `station_intervals` | `interval_id, kind, station, start_ts, end_ts, seconds, case_id, object_id` |
| `cases` | `case_id, station, truck_object_id, arrived_ts, departed_ts, wait_s, handling_s, post_dwell_s, turnaround_s, complete` |

**Activities:**
- `truck_arrived`, `handling_started`, `handling_ended`, `truck_departed`;
- `pallet_staged`;
- `aisle_blocked`, `aisle_cleared`.

**Interval kinds:**
- `truck_present`, `waiting`, `handling`, `post_dwell`;
- `forklift_active`, `forklift_idle`;
- `aisle_blocked`.

**Dock cases.** Each truck visit splits into three parts:
- **waiting**: truck present and no forklift in the zone, before the last handling;
- **handling**: a forklift is in the zone;
- **post-handling dwell**: after the last forklift leaves.

So `wait_s + handling_s + post_dwell_s = turnaround_s`. A forklift is active above 0.2 m/s of
smoothed ground speed, which needs calibrated `foot_m`. A pallet, or an idle forklift, inside an
aisle zone blocks it.

**Station, not person (SA §5).** People are never OCEL objects:
- unmapped concepts (people included) are dropped before anything is built;
- `ProcessConfig` refuses to map a person concept;
- object ids are fresh, never tracker ids.

`tests/guardrails/test_process_log_people.py` enforces this.

**Quality gates (`QualityReport`).** Each gate is `pass`, `fail` or `n/a`. A failed gate downgrades
or hides a finding.

| Gate | Passes when |
|---|---|
| Watched fraction | the lowest watched fraction over the station cameras is ≥ 0.8 |
| Little's law | occupancy L from raw truck tracks matches λ·W from the cases within 15% |
| Fragment rate | the share of process-object tracks shorter than 2 s is ≤ 10% |
| Incomplete cases | the share of cases cut by the window edges is ≤ 25% |

## Tests

```bash
uv run pytest tests/advisor tests/guardrails/test_process_log_people.py
```
