"""Site Advisor statistics: pure functions, seeded where random (task P4-08b)."""

from rulecam.advisor.stats.baseline import HourBaseline, hour_of_day_baseline, robust_z
from rulecam.advisor.stats.bootstrap import bootstrap_ci, moving_window_pvalue
from rulecam.advisor.stats.bottleneck import (
    active_period_bottleneck,
    mean_wait,
    merge_spans,
    utilisation,
)
from rulecam.advisor.stats.decomposition import (
    Decomposition,
    Stratum,
    mix_rate_decomposition,
    strata_from_samples,
)
from rulecam.advisor.stats.multiple_testing import BenjaminiHochberg, benjamini_hochberg
from rulecam.advisor.stats.pitfalls import (
    RegressionToMean,
    StratumMeans,
    regression_to_mean,
    simpsons_paradox,
)
from rulecam.advisor.stats.switchback import (
    min_detectable_effect,
    minimum_pairs,
    sign_flip_ci,
    sign_flip_p,
    smallest_possible_p,
)

__all__ = [
    "BenjaminiHochberg",
    "Decomposition",
    "HourBaseline",
    "RegressionToMean",
    "Stratum",
    "StratumMeans",
    "active_period_bottleneck",
    "benjamini_hochberg",
    "bootstrap_ci",
    "hour_of_day_baseline",
    "mean_wait",
    "merge_spans",
    "min_detectable_effect",
    "minimum_pairs",
    "mix_rate_decomposition",
    "moving_window_pvalue",
    "regression_to_mean",
    "robust_z",
    "sign_flip_ci",
    "sign_flip_p",
    "simpsons_paradox",
    "smallest_possible_p",
    "strata_from_samples",
    "utilisation",
]
