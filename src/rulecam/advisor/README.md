# `rulecam.advisor`: Site Advisor

Business advice you can check (docs/site-advisor.md). Owners: `process.py` P1, `pipeline.py` P2,
`stats/` and `whatif/` P4, `api.py` P3. mypy strict; coverage floor 85%.

## `process.py`: process log and quality gates (P1-12)

Turns tracks into a station process log, OCEL 2.0-shaped, in DuckDB (SA §4, steps 3–4).

```python
from rulecam.advisor.process import (
    assess_quality,
    build_process_log,
    stations_from_zones,
    write_duckdb,
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

## `pipeline.py`: the reasoning pipeline (P2-12)

```python
from rulecam.advisor.pipeline import SiteAdvisorPipeline

advisor = SiteAdvisorPipeline(
    spark=spark,  # Muse Spark 1.3: Ask and Recommend
    devil=spark_open_verifier,  # SparkOpenVerifier.devil_advocate (stores reasoning)
    stats=p4_stats,  # AdvisorStats: catalogue, detect, gap shares, what-ifs (P4-08)
    bus=bus,
    clock=clock,
    recorded_windows=windows,  # what was actually recorded, per station
    active_rules=rules,
    mitigations={"ped-forklift-2m": "fenced walkway"},  # owner-entered, keyed by rule id
)
outcome = advisor.run("Why was dock 3 slow this afternoon?")
```

`run` returns a `Recommendation` (also published on `advice.new`), a `DeclinedQuestionResult`,
a `WindowGuardResult` or a `NoAdviceResult`. Model and devil's-advocate failures raise; nothing
falls back to made-up advice.

- **Numbers come only from P4.** Saw claims are code templates whose `{slots}` name metric
  result ids; the gain and the 20th-percentile weekly value come from P4's what-if. Spark's
  schemas carry no numbers. `numbers_reproduced` stays 0 until Glimmer recomputes (P3-10).
- **Explain.** Each dock hypothesis is a metric test: `supported` when the whole interval clears
  its threshold, `ruled_out` when none of it does, `partly` when it straddles, `untestable`
  when the metric is missing or under k. Shares of the gap are P4's mix/rate shares.
- **Station, not person** (`claims.py`, SA §5). Per-person questions are declined before any
  model call; results grouped by a track id raise; results under 5 tracks or 3 shifts are not
  shown; claims framed around people are rejected.
- **Safety veto** (`safety.py`). Fails closed and never trusts model output: see its docstring.
- **Claim linter** (`claims.py`). No digits outside slots; causal verbs become "coincided with"
  below grade A (a switchback verdict); SA §13 overclaims are rejected.

## P4-08b: metrics, statistics, what-ifs and the measurement plan

`DockAdvisorStats` (`dock_stats.py`) implements the `AdvisorStats` protocol above on top of the
DuckDB process log, so `SiteAdvisorPipeline(stats=DockAdvisorStats(...))` works as is. Everything
here is code: no model writes a number.

```python
from rulecam.advisor.dock_stats import DockAdvisorStats

stats = DockAdvisorStats(
    duckdb_connection,  # tables from write_duckdb()
    shifts=sessions,  # the recording's shifts / sessions / services
    coverage=watchdog_fraction,  # (station, t0, t1) -> watched fraction
    forklifts=2,
)
result = stats.metric("wait_for_forklift", "dock_3", t0, t1)  # MetricResult
```

| Module | What it does |
|---|---|
| `metrics.py` | The eight vetted dock metrics (`turnaround`, `wait_for_forklift`, `handling`, `post_dwell`, `forklift_active_share`, `concurrent_forklifts`, `aisle_blocked_min`, `near_miss_per_forklift_hour`). Each has a plain-language definition (for the Glimmer recompute), a unit and a query. Returns a `MetricResult` with value, interval, `n`, `n_tracks`, `n_shifts`, coverage, provenance, the SQL and an id that is a hash of that SQL |
| `stats/` | Seeded bootstrap interval; moving-window test; Benjamini-Hochberg; mix/rate decomposition; Simpson's-paradox and regression-to-the-mean checks; utilisation, waiting-time and Roser active-period bottleneck lenses; hour-of-day baseline; exact sign-flip test with inverted interval, minimum detectable effect and the smallest possible p (`2 / 2**k`) |
| `whatif/` | Little's law, Erlang C, Allen-Cunneen, the assumption checks that pick the rung, and `queue_what_if` (gain interval by bootstrapping gaps and service times) |
| `measurement.py` | `build_measurement_plan` (switchback, hashed before the change), `plan_hash`, `read_out` (verdict from the per-pair differences) |
| `dock_stats.py` | `DockAdvisorStats`: `catalogue`, `metric`, `detect_bottlenecks`, `gap_share`, `what_if`, and `result(id)` for the recompute |

**The SQL shown is the SQL that ran.** Station ids (checked against `[A-Za-z0-9_-]+`), integers and
timestamps are written into the query text as literals, and that exact text is executed and kept
on the result. Time-based metrics average over time bins (`bin_minutes`, default 5 set-minutes);
case metrics average over complete visits that arrived in the window.

**Minimum k.** A result carries `n_tracks` (distinct trucks or forklifts) and `n_shifts` (shifts
containing at least one case or bin) and `meets_minimum_k` (5 tracks, 3 shifts). The pipeline
decides what to show. Tracks are never a grouping key.

### How the bottleneck is detected, and what that can and cannot tell you

For each station, the mean wait in the window is compared with the mean of every contiguous run of
the same length in that station's other cases (before and after the window). Waits at a queue
cluster in time, so shuffling cases between window and baseline is anti-conservative; this
comparison respects the clustering. The runs overlap, so the p-value is only nominal and the
Benjamini-Hochberg control on top of it (5% over the stations) is approximate.

Even so, the test alone is too eager. The figures below come from
`scripts/simulate_bottleneck_detection.py` (300 seeded runs of FIFO M/M/1 queues, a 150-visit
baseline at utilisation 0.2, a 30-visit window that is either unchanged or at utilisation about
0.6, one station at the nominal 5% level):

| Effect floor | False flags (window unchanged) | Power (window slower) |
|---|---|---|
| none | 12.7% | 77.7% |
| 30 s | 4.7% | 69.0% |
| 60 s | 0.0% | 44.3% |
| 120 s | 0.0% | 17.3% |

A short baseline of correlated waits is a poor estimate of "normal", so a station is also required
to be at least `min_effect_min` (default **1 set-minute**) above its baseline. That keeps a null
session quiet, at a real cost in power for modest slowdowns. The default is a guess from this
simulation, not from recorded sessions, and should be retuned on them. Flagged stations are
ordered by their Roser active-period share (the real-time, earliest-start variant, not the
retrospective longest-total-period one), then by their robust z against the hour-of-day baseline.

### The pre-registered detectable effect

`min_detectable_effect` solves for the effect using the exact sign-flip test's own power. With 6
pairs the test rejects only when all six differences share a sign, so reaching 80% power takes
**1.79 sigma**, not the 1.14 sigma a normal approximation gives (which would be 44% power).
For more pairs the power is simulated with a fixed seed (up to 12 pairs).

### Known limits

- **Forklift metrics measure handling time, not all time at the dock.** `process.py`
  `_add_activity` writes forklift activity with `station = NULL`, because the active/idle split
  comes from one forklift's ground speed over the whole site. `_forklift_spans` therefore
  attributes a station-less span to a dock for the part of it that falls inside that dock's
  `handling` intervals, and only for a forklift the dock's own visit events link with the
  `handler` qualifier. So `forklift_active_share`, `concurrent_forklifts` and
  `near_miss_per_forklift_hour` are read over the dock's handling windows: a forklift waiting in
  the dock zone with no truck there is not counted, because the log records no dock presence
  outside a visit. A span that already carries a station is used as it stands, so these metrics
  need no change if the builder starts attributing activity itself.
- `aisle_blocked_min` is the mean over the aisles serving the dock, so it stays within 60
  min/h. Aisles are found from the blocked rows, so pass `aisles_for` to include an aisle that was
  never blocked. Blockers are read from the event-object links.
- `gap_share` is the share of the **turnaround** gap (window against the station's other cases)
  that wait, handling or post-handling dwell accounts for. They sum to 1. Other metrics have no
  honest share and return `None`.
- `wait_s` includes any gap where the dock was left without a forklift, such as forklift travel,
  and `servers` counts forklifts at every station, not only the dock being asked about.
- `what_if` models the forklifts as the servers of a G/G/c queue. It only models a change whose
  `params` name `servers_added` (an integer; `add_capacity` defaults to 1) and/or
  `arrival_scv_scale` (a factor on arrival variability). Other changes return `None`. The
  recommend prompt must therefore put those params on changes it wants modelled. When the
  Erlang C checks pass, a smoothing change is modelled by Allen-Cunneen with the scaled arrival
  variation (the baseline's variation is taken as 1).
- A what-if returns `None` when the station is unstable, no rung's assumptions hold, or the model
  does not reproduce the observed wait (`baseline_fit`, within 50% or half a minute). The gain is
  minutes of waiting freed per truck, and `p20_weekly_value` is minutes per week at
  `operating_hours_per_week`. Neither is money.
- The assumption thresholds (dispersion index 0.5 to 1.5, service variation 0.6 to 1.4, utilisation
  under 0.95) are heuristics to be tuned on recorded sessions.
- `near_miss_per_forklift_hour` also needs a `near_miss_events(ts, station, provenance)` table.
- A plan needs at least 6 pairs (`2 / 2**5 = 0.0625` is still above 0.05) and at most 12. The plan
  does not yet record the on/off order per pair or the "start and end with an empty queue" rule:
  the `MeasurementPlan` contract has no field for them, and changing it needs a contract-change PR.
  `read_out` stays `not_enough_data` until every pair is done.
- SA §5's "merge cells with fewer than 3 tracks upward" is not implemented; only the
  `meets_minimum_k` flag is.

## Tests

```bash
uv run pytest tests/advisor tests/guardrails/test_process_log_people.py \
  tests/guardrails/test_advisor_per_person.py
```

## `recompute.py`, `ledger.py`, `seal.py`, `api.py`: checked cards (P3-10)

- **Independent recompute.** `Recomputer(glimmer, db_path).check_card(rec, numbers)`. For each number
  a card's slots reference, Glimmer gets **only** the metric's plain-language definition, the station,
  the window and the process-log schema, and writes its own SQL. That SQL runs on a **read-only**
  DuckDB connection with `enable_external_access = false`, after DuckDB's own parser confirms it is a
  single `SELECT` over `objects / events / event_object / station_intervals / cases`. A result
  outside 1% tolerance is **hidden** and **flags the card**; `with_recompute` copies
  `numbers_total` / `numbers_reproduced` onto the card.
- **Pre-registration.** `AdviceLedger(chain).accept(rec, plan)` hashes P4's `MeasurementPlan`
  (canonical JSON, without its own hash field), appends a `measurement_plan` record, **then** returns
  the card as `measuring`. `record_block` / `record_verdict` come after it.
  `preregistered_first(records, plan_id)` checks both the order and the hash.
- **Sealed causes.**
  - `rulecam advisor seal "<cause>"` appends only `sha256(cause + salt)`. The cause and salt stay in
    `data/seals/<id>.json` (mode 600), never in the chain.
  - `rulecam advisor reveal <id>` appends the cause and salt.
  - `rulecam advisor seal-status <day>` and `GET /advice/seals` recompute the hash, so a wrong
    cause or salt reads `matches: false`.
- **API.**
  - `GET /advice`, `GET /advice/{id}`: cards with their checks, `hidden` and `flagged`.
  - `POST /advice/{id}/accept`: owner-only, needs `AppServices.plan_factory` from P4 (503 without
    it), and replies with the pre-registration seq.
  - `GET /advice/seals`.
- **UI** `/advice`: the six-part card, clickable numbers (Glimmer's SQL, result and ✓/✗), source
  badges, "considered and not recommended", *Accept* → "pre-registered at event N", the verdict dot
  plot with CI, and the sealed-cause badge.

## `brief.py`: the 60-second morning brief (P3-S3)

`BriefService(rates=RatesStore(data/owner_rates.json), tts=MmsTts, notifier=…, writer=glimmer,
out_dir=data/briefs)`. The brief follows SA §7:

- **Order:** what was watched, yesterday's load, three numbers against the same weekday, the biggest
  leak, the safety/flow overlap, checks in progress, one decision, and what it couldn't see.
- **Length:** at most **150 words**. Lines are dropped from the end, but the watched line and the
  decision always stay.
- **Nothing to report:** it says "Nothing worth your time today."
- **Unit-level sites** (`unit_level`) get 7-day figures only.
- **Money:** the leak appears in dollars **only with the owner's own rate** for that leak kind;
  otherwise it is "hours a week of capacity".
- **Language:** Glimmer translates into the owner's language, and a translation that changes or
  drops a number is refused.
- **Voice note:** MMS-TTS speaks it, and a note over **60 s is refused**. It goes to the `owner`
  role through the notifier (LAN app, or WhatsApp with P3-S1).
- **Routes** (owner-only, mount with `brief_router(service, rates)`): `GET`/`PUT /brief/rates`,
  `POST /brief/preview`, `POST /brief/send`.
