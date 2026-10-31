"""Routes for the alerts PWA, acknowledgements and the interlock (task P3-06).

Mounted into the app through `AppServices.routers` (P3-08):

- `GET /alerts?role=…`: the phone's alert list;
- `POST /alerts/{alert_id}/respond {button, role}`: *On my way* (ack), *False alarm* (ack plus a
  `label` record for rule health), *Escalate* (next role now);
- `POST /ack {event_id, role}`;
- `POST /interlock/{device}/resume {role}`: power back on, **only with a person's role**, logged.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from rulecam.act.actions import Interlock
from rulecam.act.errors import ResumeRefusedError
from rulecam.act.escalation import EscalationManager
from rulecam.act.notify import Alert, LanNotifier
from rulecam.contracts import Ack, ActionRecord
from rulecam.evidence.chain import EventChain

Button = Literal["On my way", "False alarm", "Escalate"]


class RespondRequest(BaseModel):
    """A tap on an alert button."""

    button: Button
    role: str = Field(min_length=1)


class AckRequest(BaseModel):
    """An acknowledgement from any client."""

    event_id: str
    role: str = Field(min_length=1)


class ResumeRequest(BaseModel):
    """A person resuming the interlock (an empty role is rejected)."""

    role: str = ""


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
) -> ButtonAction:
    """Apply one alert-button tap.

    Args:
        body: The button and the tapping role.
        event_id: The alert's event.
        notifier: Alert store (statuses).
        escalation: Escalation chains.
        chain: Evidence chain for the false-alarm label.

    Returns:
        What happened.
    """
    if body.button == "Escalate":
        return "escalated" if escalation.escalate_now(event_id) else "nothing_open"
    escalation.ack(event_id, body.role)
    if body.button == "False alarm":
        chain.append("label", {"event_id": event_id, "label": "false_alarm", "role": body.role})
        notifier.set_status(event_id, "false_alarm")
        return "false_alarm"
    notifier.set_status(event_id, "acknowledged")
    return "acknowledged"


def alerts_router(
    *,
    notifier: LanNotifier,
    escalation: EscalationManager,
    chain: EventChain,
    interlock: Interlock | None = None,
) -> APIRouter:
    """Build the router.

    Args:
        notifier: The LAN alert store.
        escalation: Escalation chains (acks cancel them).
        chain: Evidence chain for false-alarm labels.
        interlock: The interlock, if this site has one.

    Returns:
        The router.
    """
    router = APIRouter(tags=["alerts"])

    @router.get("/alerts")
    def list_alerts(role: str | None = None) -> list[Alert]:
        return notifier.list(role)

    @router.post("/alerts/{alert_id}/respond")
    def respond(alert_id: str, body: RespondRequest) -> RespondResult:
        alert = notifier.get(alert_id)
        if alert is None or alert.event_id is None:
            raise HTTPException(status_code=404, detail="unknown alert")
        action = handle_button(
            body, alert.event_id, notifier=notifier, escalation=escalation, chain=chain
        )
        return RespondResult(alert_id=alert_id, action=action)

    @router.post("/ack")
    def ack(body: AckRequest) -> Ack:
        result = escalation.ack(body.event_id, body.role)
        if result is None:
            raise HTTPException(status_code=404, detail="no open escalation for this event")
        notifier.set_status(body.event_id, "acknowledged")
        return result

    @router.post("/interlock/{device}/resume")
    def resume(device: str, body: ResumeRequest) -> ActionRecord:
        if interlock is None or device not in interlock.devices:
            raise HTTPException(status_code=404, detail="unknown interlock device")
        try:
            return interlock.resume(device, body.role)
        except ResumeRefusedError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    return router
