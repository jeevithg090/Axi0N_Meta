"""The verification policy's output (task book P3-05), shared by the runtime loop and actions."""

from __future__ import annotations

from typing import Literal

from rulecam.contracts import Frozen


class Decision(Frozen):
    """What happens to a candidate after verification.

    Attributes:
        alert: Whether the alert goes out.
        route: `auto` acts on the rule's actions; `human_queue` waits for a person.
        act_first: Whether actions already ran before verification (HIGH severity).
        reason: Why, in one line (shown on the verifier panel).
    """

    alert: bool
    route: Literal["auto", "human_queue"]
    act_first: bool
    reason: str
