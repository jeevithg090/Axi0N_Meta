from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "coverage_floors.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("coverage_floors", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations via sys.modules
    spec.loader.exec_module(module)
    return module


def report(files: dict[str, tuple[int, int]]) -> dict[str, object]:
    return {
        "files": {
            path: {"summary": {"num_statements": s, "covered_lines": c}}
            for path, (s, c) in files.items()
        }
    }


def test_package_keys() -> None:
    cf = load_script()
    assert cf.package_of("src/rulecam/rules/engine.py") == "rules"
    assert cf.package_of("src/rulecam/planner/lint.py") == "planner/lint.py"
    assert cf.package_of("src/rulecam/planner/pipeline.py") == "planner"
    assert cf.package_of("src/rulecam/bus.py") == "_root"
    assert cf.package_of("tests/test_bus.py") is None


def test_strict_package_below_85_fails_and_default_70_passes() -> None:
    cf = load_script()
    packages = cf.aggregate(
        report(
            {
                "src/rulecam/rules/engine.py": (100, 80),
                "src/rulecam/capture/chunker.py": (100, 75),
            }
        )
    )
    problems = cf.failures(packages)
    assert len(problems) == 1
    assert problems[0].startswith("rules")


def test_main_exit_codes(tmp_path: Path) -> None:
    cf = load_script()
    good = tmp_path / "good.json"
    good.write_text(json.dumps(report({"src/rulecam/contracts/core.py": (10, 10)})))
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(report({"src/rulecam/contracts/core.py": (10, 5)})))

    assert cf.main(["x", str(good)]) == 0
    assert cf.main(["x", str(bad)]) == 1
    assert cf.main(["x"]) == 2


def test_empty_package_counts_as_covered() -> None:
    cf = load_script()
    assert cf.PackageCoverage("x", 0, 0).percent == 100.0
