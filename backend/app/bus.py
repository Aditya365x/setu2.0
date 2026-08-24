"""Redis: optimizer job queue, WebSocket pub/sub fan-out, CAP ETag cache.

One module so nothing else needs to know the key layout.
"""

import json
from typing import Any

import redis.asyncio as aioredis

from .config import settings

redis = aioredis.from_url(settings.redis_url, decode_responses=True)


def updates_channel(district_id: int) -> str:
    return f"district:{district_id}:updates"


def optimize_queue(district_id: int) -> str:
    return f"optimize:{district_id}"


async def publish(district_id: int, event_type: str, payload: Any) -> None:
    """Emit one §7.3 envelope to every connected DEOC screen."""
    await redis.publish(
        updates_channel(district_id),
        json.dumps({"type": event_type, "payload": payload}, default=str),
    )


async def trigger_optimize(district_id: int, reason: str = "report") -> None:
    """Nudge the optimizer. The worker debounces, so bursts collapse to one run."""
    await redis.lpush(optimize_queue(district_id), reason)
