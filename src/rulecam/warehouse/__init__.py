"""Warehouse forklift safety: the use case's connected story on top of the core pipeline.

The core already plans, proves, runs, verifies, acts and chains a forklift rule. This package
reads what the core recorded and adds what a safety manager asks for next:

- `near_misses`: every time a person came inside the owner's own distance of a moving vehicle,
  with the measured separation, closest approach, time to collision and vehicle speed from the
  candidate's trace, the verifier's verdict and what was done; plus a heatmap of where it
  happened on the camera's floor.
- `story`: one plan's connected steps (request, plan with what it cannot check, Rule CI,
  approval, a live violation, the verdict, the warning and alert, evidence, toolbox talk,
  dispute, regression), each with its own status and the records behind it.
- `talks`: Muse Spark 1.3 writes the worker-language spoken warning and the two-minute toolbox
  talk for one incident from its chained records and its evidence frames (Muse Image draws the
  safe behaviour), and the shift safety report from the day's chain.

Importing the package imports nothing else: the API wiring lives in `warehouse.api`.
"""
