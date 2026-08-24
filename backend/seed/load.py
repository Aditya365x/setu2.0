"""Load the Ganjam seed. Idempotent: safe to re-run, and `make reset` keeps it.

    python -m seed.load
"""

import asyncio
import json
import math
import random
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import SessionLocal, apply_schema  # noqa: E402
from seed.ganjam import (  # noqa: E402
    BOUNDARY, BLOCKS, PINCODES, RESOURCE_MIX, SHELTER_KINDS,
)

# Fixed seed: the demo must be reproducible. A judge asking "show me again"
# should get the same district, not a different one.
RNG = random.Random(20260822)

DISTRICT = {"name": "Ganjam", "state": "Odisha", "lgd_code": "381"}
TARGET_SHELTERS = 62


def jitter(lat: float, lng: float, km: float) -> tuple[float, float]:
    """Scatter a point within `km` of a block centre, correcting longitude for
    latitude so the spread is circular on the ground, not on the projection."""
    r = RNG.uniform(0, km) / 111.0
    theta = RNG.uniform(0, 2 * math.pi)
    return lat + r * math.sin(theta), lng + r * math.cos(theta) / math.cos(math.radians(lat))


async def seed_district(session) -> int:
    ring = ", ".join(f"{lng} {lat}" for lng, lat in BOUNDARY)
    centroid_lat = sum(b[1] for b in BLOCKS) / len(BLOCKS)
    centroid_lng = sum(b[2] for b in BLOCKS) / len(BLOCKS)

    district_id = (
        await session.execute(
            text(
                f"""
                INSERT INTO districts (id, name, state, lgd_code, boundary, centroid)
                VALUES (:id, :name, :state, :lgd,
                        ST_GeogFromText('SRID=4326;POLYGON(({ring}))'),
                        ST_MakePoint(:clng, :clat)::geography)
                ON CONFLICT (id) DO UPDATE
                    SET boundary = EXCLUDED.boundary, centroid = EXCLUDED.centroid
                RETURNING id
                """
            ),
            {
                "id": settings.district_id, **{"name": DISTRICT["name"]},
                "state": DISTRICT["state"], "lgd": DISTRICT["lgd_code"],
                "clat": centroid_lat, "clng": centroid_lng,
            },
        )
    ).scalar_one()
    return district_id


async def seed_shelters(session, district_id: int) -> int:
    existing = (
        await session.execute(
            text("SELECT COUNT(*) FROM shelters WHERE district_id = :d"), {"d": district_id}
        )
    ).scalar_one()
    if existing:
        return existing

    count = 0
    # Weight shelter count by block size — Berhampur gets more than Surada,
    # which is how the real register looks.
    for name, lat, lng, weight in BLOCKS:
        n = max(1, round(TARGET_SHELTERS * weight / sum(b[3] for b in BLOCKS)))
        for i in range(n):
            kind, base_capacity = SHELTER_KINDS[(count + i) % len(SHELTER_KINDS)]
            slat, slng = jitter(lat, lng, 6.0)
            capacity = int(base_capacity * RNG.uniform(0.7, 1.4))
            await session.execute(
                text(
                    """
                    INSERT INTO shelters (district_id, name, geom, capacity_total,
                                          occupancy, has_medical, has_power, has_water,
                                          status, contact, last_verified_at)
                    VALUES (:d, :name, ST_MakePoint(:lng, :lat)::geography, :cap, 0,
                            :med, :pow, :wat, 'open', :contact, now())
                    """
                ),
                {
                    "d": district_id,
                    "name": f"{kind}, {name}" + (f" {i + 1}" if n > 1 else ""),
                    "lat": slat, "lng": slng, "cap": capacity,
                    "med": kind.startswith(("Multipurpose", "Primary Health", "Govt College")),
                    "pow": RNG.random() < 0.75,
                    "wat": RNG.random() < 0.85,
                    "contact": f"+9194{RNG.randint(10000000, 99999999)}",
                },
            )
            count += 1
    return count


async def seed_resources(session, district_id: int) -> int:
    existing = (
        await session.execute(
            text("SELECT COUNT(*) FROM resources WHERE district_id = :d"), {"d": district_id}
        )
    ).scalar_one()
    if existing:
        return existing

    # Coastal and riverine blocks get the water assets; that is where the boats
    # actually live.
    coastal = ["Gopalpur-on-Sea", "Chatrapur", "Ganjam", "Rambha", "Khallikote",
               "Berhampur", "Rangeilunda", "Purushottampur"]
    count = 0
    for rtype, n, agency, caps, capacity in RESOURCE_MIX:
        pool = coastal if rtype in ("boat", "rescue_team") else [b[0] for b in BLOCKS]
        for i in range(n):
            block = pool[i % len(pool)]
            blat, blng = next((b[1], b[2]) for b in BLOCKS if b[0] == block)
            rlat, rlng = jitter(blat, blng, 2.0)
            label = rtype.replace("_", " ").title()
            await session.execute(
                text(
                    """
                    INSERT INTO resources (district_id, name, type, agency, capabilities,
                                           home_geom, current_geom, capacity, status, contact)
                    VALUES (:d, :name, CAST(:type AS resource_type), :agency, :caps,
                            ST_MakePoint(:lng, :lat)::geography,
                            ST_MakePoint(:lng, :lat)::geography,
                            :cap, 'idle', :contact)
                    """
                ),
                {
                    "d": district_id,
                    "name": f"{agency} {label} {i + 1} ({block})",
                    "type": rtype, "agency": agency, "caps": caps,
                    "lat": rlat, "lng": rlng, "cap": capacity,
                    "contact": f"+9194{RNG.randint(10000000, 99999999)}",
                },
            )
            count += 1
    return count


async def seed_population(session, district_id: int) -> int:
    """A modelled 1 km surface. Density decays with distance from the nearest
    settlement, which reproduces the real pattern closely enough for the equity
    term to behave correctly."""
    existing = (
        await session.execute(
            text("SELECT COUNT(*) FROM population_cells WHERE district_id = :d"),
            {"d": district_id},
        )
    ).scalar_one()
    if existing:
        return existing

    lats = [lat for _, lat, _, _ in BLOCKS]
    lngs = [lng for _, _, lng, _ in BLOCKS]
    step = 0.01  # ~1.1 km
    count = 0
    rows = []
    lat = min(lats) - 0.05
    while lat <= max(lats) + 0.05:
        lng = min(lngs) - 0.05
        while lng <= max(lngs) + 0.05:
            density = 0.0
            fragility = 0.0
            for _, blat, blng, weight in BLOCKS:
                d_km = math.hypot((lat - blat) * 111, (lng - blng) * 105)
                density += 9000 * weight * math.exp(-((d_km / 7.0) ** 2))
            # Low-lying coastal strip: single-access roads, first to be cut.
            if lng > 84.85:
                fragility = min(1.0, (lng - 84.85) * 3)
            rows.append((lat, lng, round(density, 2), round(fragility, 3)))
            lng += step
        lat += step

    for rlat, rlng, density, fragility in rows:
        await session.execute(
            text(
                """
                INSERT INTO population_cells (district_id, geom, density,
                                              historical_incident_density, road_fragility)
                VALUES (:d, ST_MakePoint(:lng, :lat)::geography, :den, :hist, :frag)
                """
            ),
            {
                "d": district_id, "lat": rlat, "lng": rlng, "den": density,
                # No public per-incident history exists for Ganjam, so this is
                # a proxy derived from exposure, not a real dataset.
                "hist": round(min(1.0, density / 9000) * 0.8, 3),
                "frag": fragility,
            },
        )
        count += 1
    return count


async def seed_pincodes(session, district_id: int) -> int:
    count = 0
    for pincode, block in PINCODES:
        entry = next((b for b in BLOCKS if b[0] == block), None)
        if not entry:
            continue
        await session.execute(
            text(
                """
                INSERT INTO pincodes (pincode, district_id, name, geom)
                VALUES (:p, :d, :n, ST_MakePoint(:lng, :lat)::geography)
                ON CONFLICT (pincode, district_id) DO NOTHING
                """
            ),
            {"p": pincode, "d": district_id, "n": block, "lat": entry[1], "lng": entry[2]},
        )
        count += 1
    return count


async def main() -> None:
    await apply_schema()
    async with SessionLocal() as session:
        district_id = await seed_district(session)
        shelters = await seed_shelters(session, district_id)
        resources = await seed_resources(session, district_id)
        pincodes = await seed_pincodes(session, district_id)
        cells = await seed_population(session, district_id)
        await session.commit()

    print(
        json.dumps(
            {
                "district": DISTRICT["name"],
                "district_id": district_id,
                "shelters": shelters,
                "resources": resources,
                "pincodes": pincodes,
                "population_cells": cells,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
