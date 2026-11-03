"""Crowd watch: SAM 3.1 density (`density.py`), checked on video by Muse Spark (`watch.py`)."""

from rulecam.crowd.api import crowd_router
from rulecam.crowd.density import (
    CrowdLevels,
    DensitySample,
    Episode,
    episodes,
    peak_local_density,
    sample,
)
from rulecam.crowd.watch import (
    CheckedEpisode,
    CrowdCheck,
    CrowdError,
    CrowdReport,
    CrowdSign,
    CrowdWatch,
    decide,
)

__all__ = [
    "CheckedEpisode",
    "CrowdCheck",
    "CrowdError",
    "CrowdLevels",
    "CrowdReport",
    "CrowdSign",
    "CrowdWatch",
    "DensitySample",
    "Episode",
    "crowd_router",
    "decide",
    "episodes",
    "peak_local_density",
    "sample",
]
