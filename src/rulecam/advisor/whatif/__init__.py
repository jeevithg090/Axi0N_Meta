"""The what-if ladder: Little's law, Erlang C, Allen-Cunneen (task P4-08b)."""

from rulecam.advisor.whatif.assumptions import check_assumptions, choose_rung
from rulecam.advisor.whatif.model import QueueObservations, Scenario, WhatIf, queue_what_if
from rulecam.advisor.whatif.queueing import (
    UnstableQueueError,
    allen_cunneen_wait,
    erlang_c,
    littles_law_time,
    littles_law_wip,
    mmc_wait,
)

__all__ = [
    "QueueObservations",
    "Scenario",
    "UnstableQueueError",
    "WhatIf",
    "allen_cunneen_wait",
    "check_assumptions",
    "choose_rung",
    "erlang_c",
    "littles_law_time",
    "littles_law_wip",
    "mmc_wait",
    "queue_what_if",
]
