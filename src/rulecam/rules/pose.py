"""Injectable pose features for `pose` predicates (P4-04 provides the real ones).

The engine does not import `rulecam.pose`; the pipeline passes an adapter to `evaluate(...,
pose=...)`. Without one, every pose state evaluates false and candidates carry
`pose_unavailable=True` in their trace, so a missing pose model is visible in every candidate.
"""

from __future__ import annotations

from typing import Literal, Protocol

from rulecam.contracts import TrackPoint

PoseStateName = Literal["down", "hands_above_shoulder", "ladder_three_point_lost", "bent_back_lift"]


class PoseFeatures(Protocol):
    """Decides a pose state for one track point (adapter over `rulecam.pose.features`)."""

    def __call__(self, state: PoseStateName, point: TrackPoint) -> bool | None:
        """Return whether `point` shows `state`, or None if its keypoints are unreliable."""
        ...
