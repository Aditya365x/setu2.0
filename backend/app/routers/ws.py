"""§7.3 — the WebSocket hub.

Redis pub/sub fans out to every DEOC screen in under a second. The client side
of this contract matters as much as the server: exponential-backoff reconnect
plus a full resync on reconnect, because the demo laptop will sleep at some
point and a silently stale map is worse than no map.
"""

import asyncio
import contextlib
import json

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from ..bus import redis, updates_channel
from ..config import settings

router = APIRouter(prefix="/api/v1", tags=["ws"])

HEARTBEAT_SECONDS = 20


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    district_id: int = Query(default=settings.district_id),
    role: str = Query(default="operator"),
) -> None:
    await websocket.accept()
    pubsub = redis.pubsub()
    await pubsub.subscribe(updates_channel(district_id))

    async def pump() -> None:
        """Forward every district event to this client."""
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            await websocket.send_text(message["data"])

    async def heartbeat() -> None:
        """Keeps intermediaries from reaping an idle socket, and gives the
        client a liveness signal it can time out on."""
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            await websocket.send_text(json.dumps({"type": "heartbeat"}))

    pump_task = asyncio.create_task(pump())
    beat_task = asyncio.create_task(heartbeat())
    try:
        while True:
            # Client -> server messages are subscription hints and heartbeats;
            # reading also gives us prompt disconnect detection.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        for task in (pump_task, beat_task):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(updates_channel(district_id))
            await pubsub.aclose()
