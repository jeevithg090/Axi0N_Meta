"""Enforce per-package coverage floors (task book rule R11).

Usage: `uv run python scripts/coverage_floors.py coverage.json`

Reads a coverage.py JSON report, aggregates line coverage per package under `src/rulecam/`, and
fails if any package is below its floor. Packages with no measured statements are skipped.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

STRICT_FLOOR = 85.0
DEFAULT_FLOOR = 70.0
STRICT_PACKAGES = frozenset(
    {"contracts", "rules", "evidence", "advisor", "verify", "planner/lint.py"}
)


@dataclass(frozen=True)
class PackageCoverage:
    """Aggregated coverage of one package."""

    name: str
    statements: int
    covered: int

    @property
    def percent(self) -> float:
        """Line coverage in percent (100 when there is nothing to cover)."""
        return 100.0 if self.statements == 0 else 100.0 * self.covered / self.statements


def package_of(path: str) -> str | None:
    """Map a source path to its package key, e.g. `src/rulecam/rules/engine.py` -> `rules`."""
    parts = Path(path).as_posix().split("/")
    if "rulecam" not in parts:
        return None
    rel = parts[parts.index("rulecam") + 1 :]
    if not rel:
        return None
    if rel[0] == "planner" and rel[-1] == "lint.py":
        return "planner/lint.py"
    return rel[0] if len(rel) > 1 else "_root"


def aggregate(report: dict[str, object]) -> dict[str, PackageCoverage]:
    """Aggregate a coverage.py JSON report into per-package totals."""
    files = report.get("files", {})
    if not isinstance(files, dict):
        msg = "coverage report has no 'files' mapping"
        raise TypeError(msg)
    totals: dict[str, tuple[int, int]] = {}
    for path, data in files.items():
        key = package_of(path)
        if key is None:
            continue
        summary = data["summary"]
        statements, covered = totals.get(key, (0, 0))
        totals[key] = (
            statements + int(summary["num_statements"]),
            covered + int(summary["covered_lines"]),
        )
    return {k: PackageCoverage(k, s, c) for k, (s, c) in totals.items()}


def failures(packages: dict[str, PackageCoverage]) -> list[str]:
    """Return a message for every package below its floor."""
    out = []
    for name, cov in sorted(packages.items()):
        floor = STRICT_FLOOR if name in STRICT_PACKAGES else DEFAULT_FLOOR
        if cov.percent < floor:
            out.append(f"{name}: {cov.percent:.1f}% < floor {floor:.0f}%")
    return out


def main(argv: list[str]) -> int:
    """Check the report given on the command line; return the process exit code."""
    if len(argv) != 2:  # noqa: PLR2004 - program name plus one argument
        print("usage: coverage_floors.py coverage.json")
        return 2
    report = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    packages = aggregate(report)
    for name, cov in sorted(packages.items()):
        print(f"{name:<20} {cov.percent:6.1f}%  ({cov.covered}/{cov.statements})")
    problems = failures(packages)
    for problem in problems:
        print(f"FAIL {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
