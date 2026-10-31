"""Golden runs: record a scripted demo flow once, then prove a replay gives the same outputs.

A scenario is `scenarios/<name>.yaml`:

```yaml
name: uc1
description: Inspection-ready kitchen, intake to first verified alert
steps:
  - id: intake
    kind: planner.intake          # a registered step kind
    params: {photo: data/uc1/inspection.jpg, voice: data/uc1/request-es.wav}
```

`record` runs every step with the cache in `record` mode (real model calls, stored) and writes the
canonical output of each step to `scenarios/<name>.expected.json`. `verify` runs the same steps in
`replay` mode (no network) and reports every step whose output differs. Step kinds are registered
by the packages that own them with `register_step`, so this module knows nothing about planners or
verifiers. While a scenario runs, the runner's cache is also the process-wide cache, so steps that
call `cached_call` are switched to the same mode (and `verify` can never reach the network).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path

import yaml
from pydantic import Field, ValidationError

from rulecam.contracts import Frozen
from rulecam.log import get_logger
from rulecam.replay.cache import Mode, ReplayCache, atomic_write_text, canonical_json, using
from rulecam.replay.errors import ReplayMiss, ScenarioError

_log = get_logger(__name__)

StepFn = Callable[[Mapping[str, object], ReplayCache], object]

STEP_REGISTRY: dict[str, StepFn] = {}


def register_step(kind: str) -> Callable[[StepFn], StepFn]:
    """Register a step kind for golden runs (a decorator).

    The step receives its `params` and the replay cache, must route every model call through the
    cache, and must return a JSON-like value that is deterministic for the same recorded replies.

    Args:
        kind: The name used in scenario files, e.g. `planner.intake`.

    Returns:
        A decorator that registers the function and returns it unchanged.
    """

    def decorator(fn: StepFn) -> StepFn:
        STEP_REGISTRY[kind] = fn
        return fn

    return decorator


class ScenarioStep(Frozen):
    """One step of a scenario."""

    id: str
    kind: str
    params: dict[str, object] = Field(default_factory=dict)


class Scenario(Frozen):
    """A scripted demo flow."""

    name: str
    description: str = ""
    steps: tuple[ScenarioStep, ...] = Field(min_length=1)


class StepDiff(Frozen):
    """A step whose replayed output differs from the recorded one."""

    step_id: str
    expected: object
    actual: object
    error: str | None = None


class GoldenReport(Frozen):
    """The result of `verify`."""

    scenario: str
    steps: int
    diffs: tuple[StepDiff, ...]

    @property
    def ok(self) -> bool:
        """Whether every step reproduced its recorded output."""
        return not self.diffs


def load_scenario(path: Path) -> Scenario:
    """Parse a scenario YAML file.

    Args:
        path: The scenario file.

    Returns:
        The scenario.

    Raises:
        ScenarioError: If the file is missing or invalid, or step ids repeat.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        scenario = Scenario.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        msg = f"invalid scenario {path}: {exc}"
        raise ScenarioError(msg) from exc
    ids = [step.id for step in scenario.steps]
    if len(set(ids)) != len(ids):
        msg = f"scenario {path} repeats a step id"
        raise ScenarioError(msg)
    return scenario


def _normalise(value: object) -> object:
    """Round-trip through canonical JSON so outputs compare as plain JSON values."""
    return json.loads(canonical_json(value))


class GoldenRunner:
    """Records and verifies scenarios against a replay cache."""

    def __init__(
        self,
        cache: ReplayCache,
        scenarios_dir: Path,
        registry: Mapping[str, StepFn] | None = None,
    ) -> None:
        """Create the runner.

        Args:
            cache: The replay cache; its mode is switched for the run and restored after.
            scenarios_dir: Where `<name>.yaml` and `<name>.expected.json` live.
            registry: Step kinds; defaults to `STEP_REGISTRY`.
        """
        self._cache = cache
        self._dir = scenarios_dir
        self._registry = STEP_REGISTRY if registry is None else registry

    def scenario_path(self, name: str) -> Path:
        """Return the YAML path of scenario `name`."""
        return self._dir / f"{name}.yaml"

    def expected_path(self, name: str) -> Path:
        """Return the expected-output path of scenario `name`."""
        return self._dir / f"{name}.expected.json"

    def _step_fn(self, step: ScenarioStep) -> StepFn:
        try:
            return self._registry[step.kind]
        except KeyError as exc:
            msg = f"unknown step kind {step.kind!r} in step {step.id!r}"
            raise ScenarioError(msg) from exc

    def _run(self, scenario: Scenario, mode: Mode) -> tuple[dict[str, object], dict[str, str]]:
        """Run every step; return outputs, and (in replay) the steps that hit `ReplayMiss`."""
        fns = [self._step_fn(step) for step in scenario.steps]  # fail before any model call
        outputs: dict[str, object] = {}
        misses: dict[str, str] = {}
        previous = self._cache.mode
        self._cache.set_mode(mode)
        try:
            with using(self._cache):
                for step, fn in zip(scenario.steps, fns, strict=True):
                    try:
                        outputs[step.id] = _normalise(fn(step.params, self._cache))
                    except ReplayMiss as exc:
                        if mode != "replay":
                            raise
                        misses[step.id] = str(exc)
        finally:
            self._cache.set_mode(previous)
        return outputs, misses

    def record(self, name: str) -> Path:
        """Run scenario `name` in record mode and write its expected outputs.

        Args:
            name: The scenario name.

        Returns:
            The path of the expected-output file.

        Raises:
            ScenarioError: If the scenario is invalid or names an unknown step kind.
        """
        scenario = load_scenario(self.scenario_path(name))
        outputs, _ = self._run(scenario, "record")
        path = self.expected_path(name)
        atomic_write_text(
            path,
            json.dumps({"scenario": scenario.name, "steps": outputs}, indent=2, sort_keys=True)
            + "\n",
        )
        _log.info("golden_recorded", scenario=name, steps=len(outputs))
        return path

    def _load_expected(self, name: str) -> dict[str, object]:
        path = self.expected_path(name)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            msg = f"no usable expected outputs at {path}; run `rulecam golden record {name}`"
            raise ScenarioError(msg) from exc
        steps = data.get("steps") if isinstance(data, dict) else None
        if not isinstance(steps, dict):
            msg = f"{path} has no `steps` object"
            raise ScenarioError(msg)
        return steps

    def verify(self, name: str) -> GoldenReport:
        """Replay scenario `name` and compare each step with its recorded output.

        A step that hits `ReplayMiss` is reported as a diff (with its step id and the error), and
        the remaining steps still run.

        Args:
            name: The scenario name.

        Returns:
            The report; `ok` is true only when every step matched.

        Raises:
            ScenarioError: If the scenario or its expected outputs are missing or invalid.
        """
        scenario = load_scenario(self.scenario_path(name))
        expected = self._load_expected(name)
        actual, misses = self._run(scenario, "replay")
        diffs = tuple(
            StepDiff(
                step_id=step.id,
                expected=expected.get(step.id),
                actual=actual.get(step.id),
                error=misses.get(step.id),
            )
            for step in scenario.steps
            if step.id in misses or expected.get(step.id) != actual[step.id]
        )
        _log.info("golden_verified", scenario=name, diffs=len(diffs))
        return GoldenReport(scenario=name, steps=len(scenario.steps), diffs=diffs)
