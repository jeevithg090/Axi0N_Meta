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
