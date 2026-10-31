"""`/ws`: mirrors every bus topic to the UI as JSON frames `{"topic": ..., "payload": ...}`."""

from __future__ import annotations

import contextlib

from fastapi import WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from rulecam.bus import ALL_TOPICS, Bus, Message
from rulecam.log import get_logger

_log = get_logger(__name__)


def frame(message: Message) -> dict[str, object]:
    """Encode one bus message as a JSON-ready WebSocket frame.

    Args:
        message: A bus message; pydantic payloads are dumped in JSON mode.

    Returns:
        `{"topic": <topic>, "payload": <JSON>}`.
    """
    payload = message.payload
    data = (
        payload.model_dump(mode="json")
        if isinstance(payload, BaseModel)
        else jsonable_encoder(payload)
    )
    return {"topic": str(message.topic), "payload": data}


async def mirror_bus(websocket: WebSocket, bus: Bus) -> None:
    """Accept the socket and forward every bus message until the client goes away.

    Args:
        websocket: The client connection.
        bus: The bus to mirror.
    """
    await websocket.accept()
    async with contextlib.aclosing(bus.subscribe(ALL_TOPICS)) as messages:
        try:
            async for message in messages:
                await websocket.send_json(frame(message))
        except WebSocketDisconnect:
            _log.info("ws_client_left")
