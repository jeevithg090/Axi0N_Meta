from __future__ import annotations

import pytest

from rulecam.advisor.whatif import check_assumptions, choose_rung
from rulecam.advisor.whatif.assumptions import Verdict, dispersion_index, squared_cv


def test_dispersion_index() -> None:
    assert dispersion_index([2, 2, 2, 2]) == 0.0
    assert dispersion_index([1, 3, 1, 3]) == pytest.approx(4 / 3 / 2)
    assert dispersion_index([1, 2]) is None  # too few bins
    assert dispersion_index([0, 0, 0, 0]) is None


def test_squared_cv() -> None:
    assert squared_cv([1.0, 1.0, 1.0]) == 0.0
    assert squared_cv([1.0, 3.0]) == pytest.approx(2.0 / 4.0)
    assert squared_cv([5.0]) is None
    assert squared_cv([0.0, 0.0]) is None


def test_checks_pass_fail_and_not_applicable() -> None:
    poisson_like = [1, 3, 2, 4, 0, 2, 3, 1]
    exponential_like = [0.1, 0.5, 1.0, 2.5, 0.3, 1.8, 0.2, 0.9, 3.0, 0.4]
    checks = check_assumptions(poisson_like, exponential_like, 0.6)
    assert checks == {"poisson_arrivals": "pass", "exponential_service": "pass", "stable": "pass"}
    regular = check_assumptions([3, 3, 3, 3], [1.0, 1.0, 1.01, 0.99, 1.0], 0.99)
    assert regular == {"poisson_arrivals": "fail", "exponential_service": "fail", "stable": "fail"}
    unknown = check_assumptions([1], [1.0], None)
    assert set(unknown.values()) == {"n/a"}


def test_rung_choice() -> None:
    base: dict[str, Verdict] = {
        "poisson_arrivals": "pass",
        "exponential_service": "pass",
        "stable": "pass",
    }
    assert choose_rung(base) == "erlang_c"
    assert choose_rung({**base, "exponential_service": "fail"}) == "allen_cunneen"
    assert choose_rung({**base, "poisson_arrivals": "fail"}) == "allen_cunneen"
    assert choose_rung({**base, "stable": "fail"}) is None
    assert choose_rung({**base, "stable": "n/a"}) is None
    assert choose_rung({**base, "poisson_arrivals": "n/a"}) is None
