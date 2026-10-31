"""Public payloads of the worker rules page and the tap dispute (task P2-11).

Everything here is served to anyone holding the poster's QR link, so it carries no identity: no
event ids, track ids, strip or clip paths, exact timestamps, names, IP addresses or user agents.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from rulecam.contracts.core import Frozen, PrivacyMode

NeverItem = Literal["faces", "names", "speed_scores", "emotion", "break_rooms"]
NEVER_LIST: Final[tuple[NeverItem, ...]] = (
    "faces",
    "names",
    "speed_scores",
    "emotion",
    "break_rooms",
)

DisputeReason = Literal[
    "lookalike_object",
    "glare_or_shadow",
    "wrong_place_or_distance",
    "rule_was_followed",
    "other",
]
DisputeOutcome = Literal["pending", "upheld", "rejected"]

EVENT_CODE_PATTERN: Final = r"^[0-9A-F]{8}$"
MAX_REASON_TEXT: Final = 500


class WorkerRule(Frozen):
    """One rule watched by the camera, in the worker's language.

    Attributes:
        rule_id: The rule.
        plain_language: Glimmer's back-translation (P2-07); `None` when the rule was not
            back-translated into this language.
    """

    rule_id: str
    plain_language: str | None


class EventCard(Frozen):
    """An anonymous event: a short code, the rule and a 15-minute time bucket.

    Attributes:
        code: Short code to dispute it with (not the event id).
        rule_id: The rule that raised it.
        bucket_start: Start of the 15-minute bucket the event started in (UTC).
        dispute: The latest dispute outcome, if it was disputed.
    """

    code: str
    rule_id: str
    bucket_start: datetime
    dispute: DisputeOutcome | None = None


class RuleCounts(Frozen):
    """Public counts for one rule on one camera within the retention window."""

    alerts: int = 0
    disputes: int = 0
    upheld: int = 0


class WorkerPage(Frozen):
    """Body of `GET /worker/{plan_id}/{camera}`."""

    plan_id: str
    site_name: str
    camera_id: str
    lang: str
    languages: tuple[str, ...]
    privacy_mode: PrivacyMode
    retention_days: int
    never_list: tuple[NeverItem, ...] = NEVER_LIST
    rules: tuple[WorkerRule, ...]
    events: tuple[EventCard, ...]
    counts: dict[str, RuleCounts]


class DisputeRequest(BaseModel):
    """Body of `POST /worker/dispute`. It has no identity fields, and unknown fields are refused."""

    model_config = ConfigDict(extra="forbid")

    event_code: str = Field(pattern=EVENT_CODE_PATTERN)
    reason_choice: DisputeReason
    reason_text: str = Field(default="", max_length=MAX_REASON_TEXT)


class DisputeResult(Frozen):
    """Body of the dispute response: the re-review outcome and the rule's updated counts.

    Spark Open's reasoning stays in the evidence chain for the owner; it can describe the strip,
    so it is not returned to the public page.
    """

    code: str
    rule_id: str
    outcome: DisputeOutcome
    counts: RuleCounts
