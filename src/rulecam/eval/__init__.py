"""The measurement layer (WS0, PLAN-9.5 §3): eval set, runner, metrics and the §2 scorecard.

- `schema`: the eval-set format (`eval/clips/<name>/clip.yaml`);
- `fetch`: clips pinned by URL + sha256, cached outside git;
- `runner` + `client`: play clips into a running API as paired phones, record what happened;
- `record` + `observe`: the saved run (replayable offline) and the alerts in it;
- `metrics`, `tracking`, `score`: the math (temporal IoU matching, rates, latency, cost, HOTA);
- `scorecard`: the PLAN §2 table, written to `reports/eval/<date>.md`.

See `eval/README.md` for labelling and recording instructions.
"""
