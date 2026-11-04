# Sample data for the cockpit UI (not a recorded run)

Everything in this folder is **hand-made sample data**, written by
[`scripts/seed_live_backend.py`](../../scripts/seed_live_backend.py) to develop the cockpit UI
on a laptop. It is **not** a recorded run and **not** a result:

- `plans/plan-8b21c409a7e1*` and `plans/plan-c914e72a03f5*` are two sample `SitePlan`s. Their
  probes, calibration, acceptance checks ("Rule CI green") and the "spark-open" audit finding
  were written by hand. No planner, SAM 3.1 probe, Rule CI run or Spark Open audit produced
  them, and their text says so (`[SAMPLE: hand-written, not a model call]`).
- `roots/2026-11-01.txt` and `roots/2026-11-03.txt` are the Merkle roots of chains the script
  wrote (the golden 1 Nov recording copied into the `demo` site, and a sample chain of seven
  hand-written incidents for 3 Nov). They are not the committed roots of any real site's chain.

These files used to sit in `plans/` and `evidence/roots/`, which the API serves and which hold
real plans and real public roots. They were moved here so a deployment can never show them as
real. The seed script now writes its sample plans to `plans/` only at run time (git-ignored)
and its roots to `data/evidence/sample-roots/`, never to `evidence/roots/`.

Do not copy anything from here into a deployment's `plans/` or `evidence/roots/`, and do not
quote any number from it.
