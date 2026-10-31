"""Routes for the alerts PWA, acknowledgements and the interlock (task P3-06).

Mounted into the app through `AppServices.routers` (P3-08):

- `GET /alerts?role=…`: the phone's alert list;
- `POST /alerts/{alert_id}/respond {button, role}`: *On my way* (ack), *False alarm* (ack plus a
  `label` record for rule health), *Escalate* (next role now);
- `POST /ack {event_id, role}`;
- `POST /interlock/{device}/resume {role}`: power back on, **only with a person's role**, logged.

Roles are short identifiers (`dock_lead`, never a name). When the site's roles are given, any other
role is refused with 403. A plug that does not respond on resume returns 502.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from rulecam.act.actions import Interlock
from rulecam.act.errors import DeviceError, ResumeRefusedError
from rulecam.act.escalation import EscalationManager
from rulecam.act.notify import Alert, LanNotifier
from rulecam.contracts import Ack, ActionRecord
from rulecam.evidence.chain import EventChain

Button = Literal["On my way", "False alarm", "Escalate"]

ROLE_PATTERN = r"^[A-Za-z][A-Za-z0-9_-]*$"
MAX_ROLE = 64
MAX_ID = 128

Role = Annotated[str, Field(min_length=1, max_length=MAX_ROLE, pattern=ROLE_PATTERN)]


class RespondRequest(BaseModel):
    """A tap on an alert button."""

    button: Button
    role: Role


class AckRequest(BaseModel):
    """An acknowledgement from any client."""

    event_id: str = Field(min_length=1, max_length=MAX_ID, pattern=r"^[A-Za-z0-9_.:-]+$")
    role: Role


class ResumeRequest(BaseModel):
    """A person resuming the interlock (an empty role is refused with 403)."""

    role: str = Field(default="", max_length=MAX_ROLE, pattern=r"^([A-Za-z][A-Za-z0-9_-]*)?$")


class RespondResult(BaseModel):
    """What a button tap did."""

    alert_id: str
    action: ButtonAction


ButtonAction = Literal["acknowledged", "false_alarm", "escalated", "nothing_open"]


def handle_button(
    body: RespondRequest,
    event_id: str,
    *,
    notifier: LanNotifier,
    escalation: EscalationManager,
    chain: EventChain,
    rule_id: str | None = None,
) -> ButtonAction:
    """Apply one alert-button tap. Every ack is chained, with or without an open escalation.

    Args:
        body: The button and the tapping role.
        event_id: The alert's event.
        notifier: Alert store (statuses).
        escalation: Escalation chains.
        chain: Evidence chain for the false-alarm label.
        rule_id: The alert's rule, carried on the false-alarm label for rule health.

    Returns:
        What happened.
    """
    if body.button == "Escalate":
        return "escalated" if escalation.escalate_now(event_id) else "nothing_open"
    if escalation.ack(event_id, body.role) is None:
        escalation.record_ack(event_id, body.role)
    if body.button == "False alarm":
        chain.append(
            "label",
            {"event_id": event_id, "rule_id": rule_id, "label": "false_alarm", "role": body.role},
        )
        notifier.set_status(event_id, "false_alarm")
        return "false_alarm"
    notifier.set_status(event_id, "acknowledged")
    return "acknowledged"


def resume_interlock(interlock: Interlock | None, device: str, role: str) -> ActionRecord:
    """Resume `device` for `role`, mapping failures to HTTP errors (404, 403, 502).

    Args:
        interlock: The site's interlock, if any.
        device: Device name from the path.
        role: The approving role (empty is refused).

    Returns:
        The chained `interlock_resume` record.
    """
    if interlock is None or device not in interlock.devices:
        raise HTTPException(status_code=404, detail="unknown interlock device")
    try:
        return interlock.resume(device, role)
    except ResumeRefusedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DeviceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def alerts_router(
    *,
    notifier: LanNotifier,
    escalation: EscalationManager,
    chain: EventChain,
    interlock: Interlock | None = None,
    roles: Collection[str] | None = None,
) -> APIRouter:
    """Build the router.

    Args:
        notifier: The LAN alert store.
        escalation: Escalation chains (acks cancel them).
        chain: Evidence chain for false-alarm labels.
        interlock: The interlock, if this site has one.
        roles: The site's roles (from the plan); when given, other roles are refused.

    Returns:
        The router.
    """
    router = APIRouter(tags=["alerts"])
    known = frozenset(roles) if roles is not None else None

    def check_role(role: str) -> None:
        if known is not None and role not in known:
            raise HTTPException(status_code=403, detail="unknown role")

    @router.get("/alerts")
    def list_alerts(
        role: Annotated[str | None, Query(max_length=MAX_ROLE, pattern=ROLE_PATTERN)] = None,
    ) -> list[Alert]:
        return notifier.list(role)

    @router.post("/alerts/{alert_id}/respond")
    def respond(alert_id: str, body: RespondRequest) -> RespondResult:
        check_role(body.role)
        alert = notifier.get(alert_id)
        if alert is None or alert.event_id is None:
            raise HTTPException(status_code=404, detail="unknown alert")
        action = handle_button(
            body,
            alert.event_id,
            notifier=notifier,
            escalation=escalation,
            chain=chain,
            rule_id=alert.rule_id,
        )
        return RespondResult(alert_id=alert_id, action=action)

    @router.post("/ack")
    def ack(body: AckRequest) -> Ack:
        check_role(body.role)
        result = escalation.ack(body.event_id, body.role)
        if result is None:
            raise HTTPException(status_code=404, detail="no open escalation for this event")
        notifier.set_status(body.event_id, "acknowledged")
        return result

    @router.post("/interlock/{device}/resume")
    def resume(device: str, body: ResumeRequest) -> ActionRecord:
        if body.role:
            check_role(body.role)
        return resume_interlock(interlock, device, body.role)

    return router
