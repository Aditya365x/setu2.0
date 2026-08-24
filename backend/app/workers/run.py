"""Worker process: CAP poller + optimizer loop.

Two coroutines in one container. The optimizer is triggered by events but also
ticks on a timer, because freed capacity — a unit going back to idle — must
re-trigger optimisation even when no new report has arrived.
"""

import asyncio
import logging

from ..bus import optimize_queue, redis
from ..config import settings
from ..db import SessionLocal, apply_schema
from ..integrations.cap import poll_cap
from ..services.optimizer import optimization_cycle

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("setu.worker")


async def optimizer_loop() -> None:
    """Debounced. A burst of eighty reports collapses into one solver run —
    without this the system spends the whole cyclone re-solving."""
    district_id = settings.district_id
    queue = optimize_queue(district_id)

    while True:
        try:
            # Block until something happens, but wake anyway on the tick so a
            # freed unit and an ageing incident both get picked up.
            trigger = await redis.blpop(queue, timeout=settings.optimize_tick_seconds)
            reason = trigger[1] if trigger else "tick"

            if trigger:
                # Collection window: drain everything that piled up behind the
                # first trigger before solving once.
                await asyncio.sleep(settings.optimize_debounce_seconds)
                drained = 0
                while await redis.lpop(queue):
                    drained += 1
                if drained:
                    reason = f"{reason}+{drained}"

            async with SessionLocal() as session:
                result = await optimization_cycle(session, district_id)

            if result.get("skipped"):
                continue
            log.info(
                "cycle(%s): %s incidents, %s units, mean %.1f -> %.1f min, %sms",
                reason,
                result.get("incidents"),
                result.get("resources"),
                result.get("greedy", {}).get("mean_response_min", 0),
                result.get("optimized", {}).get("mean_response_min", 0),
                result.get("cycle_ms"),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("optimizer cycle failed; continuing")
            await asyncio.sleep(1)


async def cap_loop() -> None:
    district_id = settings.district_id
    while True:
        try:
            async with SessionLocal() as session:
                result = await poll_cap(session, district_id)
            if result.get("ingested"):
                log.info("CAP: ingested %s alert(s)", result["ingested"])
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("CAP poll failed; will retry")
        await asyncio.sleep(settings.cap_poll_seconds)


async def main() -> None:
    await apply_schema()
    log.info(
        "worker up — district=%s routing=%s offline=%s",
        settings.district_id, settings.routing_provider, settings.offline_mode,
    )
    await asyncio.gather(optimizer_loop(), cap_loop())


if __name__ == "__main__":
    asyncio.run(main())
