# Golden-run scenarios

One YAML file per demo flow (`uc1.yaml`, `uc4.yaml`, …), recorded with `make golden SCENARIO=<name>`
and checked with `uv run rulecam golden verify <name>` (see `src/rulecam/replay/README.md`).

```yaml
name: uc1
description: Inspection-ready kitchen, intake to first verified alert
steps:
  - id: intake                     # unique within the scenario
    kind: planner.intake           # registered with rulecam.replay.golden.register_step
    params: {photo: data/uc1/inspection.jpg, voice: data/uc1/request-es.wav}
```

Media paths point into `data/` (git-ignored). Commit the `.yaml` and the generated
`.expected.json`; the recorded replies themselves live in the replay cache directory.
