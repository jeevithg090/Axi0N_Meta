"""Gate the eval score of every golden run in CI (add-on 1.2).

`rulecam golden verify` fails when a golden run's outputs *change*. This fails when they are
*wrong*: each scenario's `demo.score` step may carry a `gate` in its params, checked against a
fresh `rulecam eval score` of the recorded run (`demo.steps.score_fixture`):

```yaml
- {id: score, kind: demo.score,
   params: {fixture: scenarios/worker_passes_4m, eval_set: eval/clips,
            gate: {min_alerts: 1, tier1_includes: violation}}}
```

| Key | Fails when |
|---|---|
| `max_alerts` / `min_alerts` | the run has more / fewer verified alerts |
| `max_false_alerts_on_negative` | a negative clip got more false alerts |
| `min_tp` / `max_fp` | fewer true positives / more false positives against the labels |
| `tier1_includes` | no Tier 1 verdict of the run is this one (e.g. `violation`) |
| `outcome` | the run's outcome differs (e.g. `activated`) |

A scenario without a gate is still gated on the one thing every run must satisfy: no false
alert on a negative clip.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from rulecam.demo.steps import score_fixture
from rulecam.replay.golden import load_scenario

DEFAULT_GATE: Mapping[str, object] = {"max_false_alerts_on_negative": 0}
_MAX = {
    "max_alerts": "alerts",
    "max_false_alerts_on_negative": "false_alerts_on_negative",
    "max_fp": "fp",
}
_MIN = {"min_alerts": "alerts", "min_tp": "tp"}
TIER1, OUTCOME = "tier1_includes", "outcome"


@dataclass(frozen=True)
class GateResult:
    """One scenario's gate: the score it was checked on and what failed."""

    scenario: str
    score: Mapping[str, object]
    failures: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """Whether every gate passed."""
        return not self.failures


def _int(value: object) -> int:
    return int(value) if isinstance(value, int | float | str) else 0


def check_gate(score: Mapping[str, object], gate: Mapping[str, object]) -> list[str]:
    """Return a message for every gate the score breaks (unknown keys are errors too)."""
    failures = []
    for check, limit in gate.items():
        if check in _MAX:
            if _int(score.get(_MAX[check])) > _int(limit):
                failures.append(f"{_MAX[check]} {score.get(_MAX[check])} > {limit}")
        elif check in _MIN:
            if _int(score.get(_MIN[check])) < _int(limit):
                failures.append(f"{_MIN[check]} {score.get(_MIN[check])} < {limit}")
        elif check == TIER1:
            verdicts = score.get("tier1")
            if not isinstance(verdicts, list) or limit not in verdicts:
                failures.append(f"no Tier 1 verdict {limit!r} (got {verdicts})")
        elif check == OUTCOME:
            if score.get("outcome") != limit:
                failures.append(f"outcome {score.get('outcome')!r} != {limit!r}")
        else:
            failures.append(f"unknown gate key {check!r}")
    return failures


def gate_scenario(path: Path) -> GateResult:
    """Score one scenario's recorded run and check its gate (or the default one)."""
    scenario = load_scenario(path)
    steps = [s for s in scenario.steps if s.kind == "demo.score"]
    if not steps:
        return GateResult(scenario.name, {}, ())
    params = steps[0].params
    score = score_fixture(params)
    gate = params.get("gate")
    gates = {**DEFAULT_GATE, **(gate if isinstance(gate, Mapping) else {})}
    return GateResult(scenario.name, score, tuple(check_gate(score, gates)))


def gate_all(scenarios_dir: Path, names: list[str] | None = None) -> list[GateResult]:
    """Gate every `scenarios_dir/*.yaml` (or only `names`)."""
    paths = sorted(scenarios_dir.glob("*.yaml"))
    if names:
        paths = [p for p in paths if p.stem in names]
    return [gate_scenario(p) for p in paths]
