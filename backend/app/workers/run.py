"""Worker process: CAP poller + one optimizer loop per district.

The optimizer is triggered by events but also ticks on a timer, because freed
capacity — a unit going back to idle — must re-trigger optimisation even when no
new report has arrived.

## Why one loop per district and not one loop

Each district is an independent allocation problem: its own units, its own
incidents, its own Collector. A single loop keyed to `settings.district_id`
silently starved every other district — reports would ingest and cluster into
incidents, then sit forever because nothing ever ran the solver for them. With
one district seeded that was invisible. With six it would have been the whole
coastal strip north of Ganjam showing incidents and never a dispatch.

Districts are discovered from the database at startup rather than configured, so
seeding a seventh district and restarting the worker is all it takes. Each loop
has its own Redis queue and its own per-district lock, so a slow solve in
Balasore cannot delay Ganjam.
"""

import asyncio
import logging

from sqlalchemy import text

from ..bus import optimize_queue, redis
from ..config import settings
from ..db import SessionLocal, apply_schema
from ..integrations.cap import poll_cap
from ..services.optimizer import optimization_cycle

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("setu.worker")


async def discover_districts() -> list[tuple[int, str]]:
    """Every seeded district. Falls back to the configured one so a worker
    started against an unseeded database still comes up and still works the
    moment `seed.load` runs."""
    try:
        async with SessionLocal() as session:
            rows = (
                await session.execute(
                    text("SELECT id, name FROM districts ORDER BY id")
                )
            ).all()
        if rows:
            return [(r[0], r[1]) for r in rows]
    except Exception:
        log.exception("could not read districts; falling back to configured id")
    return [(settings.district_id, f"district {settings.district_id}")]


async def optimizer_loop(district_id: int, name: str) -> None:
    """Debounced. A burst of eighty reports collapses into one solver run —
    without this the system spends the whole cyclone re-solving."""
    queue = optimize_queue(district_id)
    log.info("optimizer loop up for %s (district %s)", name, district_id)

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
                "[%s] cycle(%s): %s incidents, %s units, mean %.1f -> %.1f min, %sms",
                name,
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
            log.exception("[%s] optimizer cycle failed; continuing", name)
            await asyncio.sleep(1)


async def cap_loop(districts: list[tuple[int, str]]) -> None:
    """One poll, fanned out to every district.

    CAP is a national feed and the expensive part is fetching it, not matching
    it. `poll_cap` already filters each alert against the district boundary, so
    polling once per district would re-download the same XML six times to throw
    away five sixths of it. Fetch once, offer it to each district, let the
    boundary test decide.
    """
    while True:
        for district_id, name in districts:
            try:
                async with SessionLocal() as session:
                    result = await poll_cap(session, district_id)
                if result.get("ingested"):
                    log.info("[%s] CAP: ingested %s alert(s)", name, result["ingested"])
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("[%s] CAP poll failed; will retry", name)
        await asyncio.sleep(settings.cap_poll_seconds)


async def main() -> None:
    await apply_schema()
    districts = await discover_districts()
    log.info(
        "worker up — %s district(s): %s | routing=%s offline=%s",
        len(districts),
        ", ".join(f"{n}({i})" for i, n in districts),
        settings.routing_provider,
        settings.offline_mode,
    )
    await asyncio.gather(
        *[optimizer_loop(i, n) for i, n in districts],
        cap_loop(districts),
    )


if __name__ == "__main__":
    asyncio.run(main())
