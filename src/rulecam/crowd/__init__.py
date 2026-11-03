"""Crowd early warning at gates, stations and venues: measure, assess, check, warn, prove.

- `crowd.signals`: counts on tiles, density per square metre, motion and crowd pressure.
- `crowd.scan`: one reading of a camera (`CrowdScanner`), with a density heatmap.
- `crowd.memory`: readings every few seconds per camera (`CrowdMemory`), loaded by Ask.
- `crowd.warden`: Muse Spark assesses the trend against the operator's instruction, Muse Glimmer
  checks the frames, then staff are alerted, announcements spoken and the alert chained.
- `crowd.density` and `crowd.watch`: after the fact, peak local density from the runtime's SAM
  3.1 person tracks, each crowded spell watched on video by Muse Spark (`POST /crowd/scan`,
  `crowd.api`, mounted with the site tools).

Served at `/crowd/*` (`api.crowd`). Submodules are imported directly (this package imports
nothing, so `ask` and `crowd` can use each other's types).
"""
