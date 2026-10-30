"""The `relay.model_used` payload every model client publishes (task book §4.8, §5.1).

The UI's relay HUD shows which model handled which pipeline segment and how long it took.
"""

from __future__ import annotations

from pydantic import Field

from rulecam.bus import Bus, Topic
from rulecam.contracts import Frozen


class ModelUsed(Frozen):
    """One model call, as shown on the relay HUD.

    Attributes:
        model: The model ID that served the call, e.g. `sam-3.1`.
        segment: The pipeline segment that made the call, e.g. `perception`.
        latency_ms: Wall-clock time of the call including retries, in milliseconds.
        camera_id: The camera the call was made for, if any.
        attempts: How many HTTP attempts the call needed (1 when no retry happened).
    """

    model: str
    segment: str
    latency_ms: int = Field(ge=0)
    camera_id: str | None = None
    attempts: int = Field(default=1, ge=1)


def publish_model_used(bus: Bus | None, event: ModelUsed) -> None:
    """Publish `event` on `relay.model_used` when a bus is configured.

    Args:
        bus: The event bus, or `None` when the caller runs without one (CLI tools, tests).
        event: The payload to publish.
    """
    if bus is not None:
        bus.publish(Topic.RELAY_MODEL_USED, event)
