"""Seed coastal Odisha — all six Bay of Bengal districts.

    python -m seed.load                 # every coastal district
    python -m seed.load --district 1    # just Ganjam
    python -m seed.load --list          # what is available

Idempotent: safe to re-run, and `make reset` keeps everything it writes.

Nothing in here is district-specific. The six districts are data in
`odisha_coastal.py`; this file is the machinery that turns any of them into
rows. Adding a seventh means editing that list, not this one.
"""

import argparse
import asyncio
import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.db import SessionLocal, apply_schema  # noqa: E402
from seed.odisha_coastal import (  # noqa: E402
    DISTRICTS,
    DISTRICT_BY_ID,
    RESOURCE_MIX,
    SHELTER_KINDS,
    ALIASES,
    District,
)

# Fixed seed: the demo must be reproducible. A judge asking "show me again"
# should get the same district, not a different one.
RNG = random.Random(20260822)

# Shelters per district at weight 1.0, scaled by the district's block count.
# Ganjam (25 blocks) lands near the OSDMA figure; smaller districts get fewer,
# which is how the real register looks.
SHELTERS_PER_BLOCK = 2.5


def jitter(lat: float, lng: float, km: float) -> tuple[float, float]:
    """Scatter a point within `km` of a block centre, correcting longitude for
    latitude so the spread is circular on the ground, not on the projection."""
    r = RNG.uniform(0, km) / 111.0
    theta = RNG.uniform(0, 2 * math.pi)
    return lat + r * math.sin(theta), lng + r * math.cos(theta) / math.cos(math.radians(lat))


async def seed_district(session, d: District) -> int:
    """Insert the district and its boundary.

    A hand-drawn outline is used when one exists. Otherwise the boundary is
    DERIVED: the convex hull of the district's block positions, buffered
    outward. That is honest about what it is — enough to frame a map and to
    answer point-in-district, and emphatically not a surveyed boundary. Replace
    with the Survey of India / OSM admin relation before a pilot.
    """
    blocks = d["blocks"]
    clat = sum(b[1] for b in blocks) / len(blocks)
    clng = sum(b[2] for b in blocks) / len(blocks)

    if d.get("boundary"):
        ring = ", ".join(f"{lng} {lat}" for lng, lat in d["boundary"])
        boundary_sql = f"ST_GeogFromText('SRID=4326;POLYGON(({ring}))')"
        params: dict = {}
    else:
        pts = ", ".join(f"ST_MakePoint({lng}, {lat})" for _, lat, lng, _ in blocks)
        # 0.09 degrees ~= 10 km, roughly the distance from a block headquarters
        # to the edge of its block.
        boundary_sql = (
            f"ST_Buffer(ST_ConvexHull(ST_Collect(ARRAY[{pts}]))::geography, 9000)::geography"
        )
        params = {}

    return (
        await session.execute(
            text(
                f"""
                INSERT INTO districts (id, name, state, lgd_code, boundary, centroid)
                VALUES (:id, :name, :state, :lgd,
                        {boundary_sql},
                        ST_MakePoint(:clng, :clat)::geography)
                ON CONFLICT (id) DO UPDATE
                    SET name = EXCLUDED.name,
                        boundary = EXCLUDED.boundary,
                        centroid = EXCLUDED.centroid
                RETURNING id
                """
            ),
            {
                "id": d["id"], "name": d["name"], "state": d["state"],
                "lgd": d["lgd_code"], "clat": clat, "clng": clng, **params,
            },
        )
    ).scalar_one()


async def seed_shelters(session, d: District) -> int:
    did = d["id"]
    existing = (
        await session.execute(
            text("SELECT COUNT(*) FROM shelters WHERE district_id = :d"), {"d": did}
        )
    ).scalar_one()
    if existing:
        return existing

    blocks = d["blocks"]
    target = round(SHELTERS_PER_BLOCK * len(blocks))
    total_weight = sum(b[3] for b in blocks)
    rows, count = [], 0

    for name, lat, lng, weight in blocks:
        n = max(1, round(target * weight / total_weight))
        for i in range(n):
            kind, base_capacity = SHELTER_KINDS[(count + i) % len(SHELTER_KINDS)]
            slat, slng = jitter(lat, lng, 6.0)
            rows.append(
                {
                    "d": did,
                    "name": f"{kind}, {name}" + (f" {i + 1}" if n > 1 else ""),
                    "lat": slat, "lng": slng,
                    "cap": int(base_capacity * RNG.uniform(0.7, 1.4)),
                    "med": kind.startswith(("Multipurpose", "Primary Health", "Govt College")),
                    "pow": RNG.random() < 0.75,
                    "wat": RNG.random() < 0.85,
                    "contact": f"+9194{RNG.randint(10000000, 99999999)}",
                }
            )
            count += 1

    await session.execute(
        text(
            """
            INSERT INTO shelters (district_id, name, geom, capacity_total, occupancy,
                                  has_medical, has_power, has_water, status,
                                  contact, last_verified_at)
            VALUES (:d, :name, ST_MakePoint(:lng, :lat)::geography, :cap, 0,
                    :med, :pow, :wat, 'open', :contact, now())
            """
        ),
        rows,
    )
    return len(rows)


async def seed_resources(session, d: District) -> int:
    did = d["id"]
    existing = (
        await session.execute(
            text("SELECT COUNT(*) FROM resources WHERE district_id = :d"), {"d": did}
        )
    ).scalar_one()
    if existing:
        return existing

    blocks = d["blocks"]
    coastal = d["coastal"] or [b[0] for b in blocks]
    # Scale the roster with the district. Ganjam's 25 blocks keep the full §A
    # mix; a 7-block district gets proportionally fewer units, never zero of a
    # type — a district with no ambulance is not a smaller district, it is a
    # broken one.
    scale = max(0.45, len(blocks) / 25.0)

    rows = []
    for rtype, n, agency, caps, capacity in RESOURCE_MIX:
        count_for_type = max(1, round(n * scale))
        pool = coastal if rtype in ("boat", "rescue_team") else [b[0] for b in blocks]
        for i in range(count_for_type):
            block = pool[i % len(pool)]
            blat, blng = next((b[1], b[2]) for b in blocks if b[0] == block)
            rlat, rlng = jitter(blat, blng, 2.0)
            # Relief stock, only on supply-carrying units. A 5,000 L tanker
            # and ~1,500 kg of dry ration is a realistic district relief truck;
            # at Sphere standards (15 L/person/day) that is one day of water for
            # about 330 people.
            is_supply = "supply" in caps
            rows.append(
                {
                    "d": did,
                    "name": f"{agency} {rtype.replace('_', ' ').title()} {i + 1} ({block})",
                    "type": rtype, "agency": agency, "caps": caps,
                    "lat": rlat, "lng": rlng, "cap": capacity,
                    "water": RNG.randrange(3000, 6001, 500) if is_supply else 0,
                    "food": RNG.randrange(800, 2001, 100) if is_supply else 0,
                    "contact": f"+9194{RNG.randint(10000000, 99999999)}",
                }
            )

    await session.execute(
        text(
            """
            INSERT INTO resources (district_id, name, type, agency, capabilities,
                                   home_geom, current_geom, capacity, status, contact,
                                   stock_water_l, stock_food_kg)
            VALUES (:d, :name, CAST(:type AS resource_type), :agency, :caps,
                    ST_MakePoint(:lng, :lat)::geography,
                    ST_MakePoint(:lng, :lat)::geography,
                    :cap, 'idle', :contact, :water, :food)
            """
        ),
        rows,
    )
    return len(rows)


async def seed_population(session, d: District) -> int:
    """A modelled 1 km surface. Density decays with distance from the nearest
    settlement, which reproduces the real pattern closely enough for the equity
    term in §6.2 to behave correctly."""
    did = d["id"]
    existing = (
        await session.execute(
            text("SELECT COUNT(*) FROM population_cells WHERE district_id = :d"),
            {"d": did},
        )
    ).scalar_one()
    if existing:
        return existing

    blocks = d["blocks"]
    lats = [b[1] for b in blocks]
    lngs = [b[2] for b in blocks]
    # The coastal edge of this district: everything east of the 80th percentile
    # of block longitude is treated as the low-lying strip with single-access
    # roads. Derived per district rather than a global constant, because the
    # coastline runs diagonally across the corridor.
    coast_lng = sorted(lngs)[int(0.8 * (len(lngs) - 1))]

    step = 0.01  # ~1.1 km
    rows = []
    lat = min(lats) - 0.05
    while lat <= max(lats) + 0.05:
        lng = min(lngs) - 0.05
        while lng <= max(lngs) + 0.05:
            density = sum(
                9000 * w * math.exp(-((math.hypot((lat - bl) * 111, (lng - bn) * 105) / 7.0) ** 2))
                for _, bl, bn, w in blocks
            )
            fragility = min(1.0, max(0.0, (lng - coast_lng) * 3))
            rows.append(
                {
                    "d": did, "lat": lat, "lng": lng,
                    "den": round(density, 2),
                    # No public per-incident history exists, so this is a proxy
                    # derived from exposure, not a real dataset.
                    "hist": round(min(1.0, density / 9000) * 0.8, 3),
                    "frag": round(fragility, 3),
                }
            )
            lng += step
        lat += step

    # executemany, not a loop of round trips: six districts is tens of thousands
    # of cells and one-at-a-time inserts turn a 20-second seed into minutes.
    CHUNK = 5000
    for i in range(0, len(rows), CHUNK):
        await session.execute(
            text(
                """
                INSERT INTO population_cells (district_id, geom, density,
                                              historical_incident_density, road_fragility)
                VALUES (:d, ST_MakePoint(:lng, :lat)::geography, :den, :hist, :frag)
                """
            ),
            rows[i:i + CHUNK],
        )
    return len(rows)


async def seed_pincodes(session, d: District) -> int:
    rows = []
    for pincode, block in d["pincodes"]:
        entry = next((b for b in d["blocks"] if b[0] == block), None)
        if not entry:
            continue
        rows.append(
            {"p": pincode, "d": d["id"], "n": block, "lat": entry[1], "lng": entry[2]}
        )
    if not rows:
        return 0
    await session.execute(
        text(
            """
            INSERT INTO pincodes (pincode, district_id, name, geom)
            VALUES (:p, :d, :n, ST_MakePoint(:lng, :lat)::geography)
            ON CONFLICT (pincode, district_id) DO NOTHING
            """
        ),
        rows,
    )
    return len(rows)


async def seed_places(session, d: District) -> int:
    """The searchable gazetteer: every block headquarters and every shelter.

    Two accuracies, honestly distinguished. A block name locates you to the
    block (~5 km); a named school locates you to that building (~150 m). The
    number travels onto the report so trust scoring and the map circle both
    reflect what was actually known.
    """
    did = d["id"]
    existing = (
        await session.execute(
            text("SELECT COUNT(*) FROM places WHERE district_id = :d"), {"d": did}
        )
    ).scalar_one()
    if existing:
        return existing

    rows = [
        {"d": did, "name": name, "kind": "town", "lat": lat, "lng": lng, "acc": 5000}
        for name, lat, lng, _ in d["blocks"]
    ]
    await session.execute(
        text(
            """
            INSERT INTO places (district_id, name, kind, geom, accuracy_m)
            VALUES (:d, :name, :kind, ST_MakePoint(:lng, :lat)::geography, :acc)
            ON CONFLICT (district_id, name, kind) DO NOTHING
            """
        ),
        rows,
    )

    # Shelters are already rows with names and points; mirror them in rather
    # than duplicating the coordinates by hand.
    await session.execute(
        text(
            """
            INSERT INTO places (district_id, name, kind, geom, accuracy_m)
            SELECT district_id, name, 'shelter', geom, 150
            FROM shelters WHERE district_id = :d
            ON CONFLICT (district_id, name, kind) DO NOTHING
            """
        ),
        {"d": did},
    )
    # Colloquial names, pointed at the same coordinates as the gazetted one.
    alias_rows = []
    block_names = {b[0]: (b[1], b[2]) for b in d["blocks"]}
    for alias, canonical in ALIASES:
        if canonical in block_names:
            lat, lng = block_names[canonical]
            alias_rows.append(
                {"d": did, "name": alias, "kind": "town",
                 "lat": lat, "lng": lng, "acc": 5000}
            )
    if alias_rows:
        await session.execute(
            text(
                """
                INSERT INTO places (district_id, name, kind, geom, accuracy_m)
                VALUES (:d, :name, :kind, ST_MakePoint(:lng, :lat)::geography, :acc)
                ON CONFLICT (district_id, name, kind) DO NOTHING
                """
            ),
            alias_rows,
        )

    return (
        await session.execute(
            text("SELECT COUNT(*) FROM places WHERE district_id = :d"), {"d": did}
        )
    ).scalar_one()


async def seed_one(session, d: District) -> dict:
    await seed_district(session, d)
    return {
        "district_id": d["id"],
        "district": d["name"],
        "shelters": await seed_shelters(session, d),
        "resources": await seed_resources(session, d),
        "pincodes": await seed_pincodes(session, d),
        "population_cells": await seed_population(session, d),
        "places": await seed_places(session, d),
    }


async def main(only: int | None = None) -> None:
    await apply_schema()
    targets = [DISTRICT_BY_ID[only]] if only else DISTRICTS

    results = []
    async with SessionLocal() as session:
        for d in targets:
            results.append(await seed_one(session, d))
            await session.commit()
            print(f"  seeded {d['name']}", flush=True)

    totals = {
        k: sum(r[k] for r in results)
        for k in ("shelters", "resources", "pincodes", "population_cells", "places")
    }
    print(json.dumps({"districts": results, "totals": totals}, indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Seed coastal Odisha")
    ap.add_argument("--district", type=int, default=None,
                    help="seed only this district id")
    ap.add_argument("--list", action="store_true", help="list districts and exit")
    args = ap.parse_args()

    if args.list:
        for d in DISTRICTS:
            print(f"{d['id']}  {d['name']:16s} {len(d['blocks']):2d} blocks  "
                  f"prefixes {','.join(d['pincode_prefixes'])}")
        raise SystemExit(0)

    if args.district and args.district not in DISTRICT_BY_ID:
        raise SystemExit(f"unknown district {args.district}; try --list")

    asyncio.run(main(args.district))
