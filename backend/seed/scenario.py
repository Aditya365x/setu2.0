"""Scripted event streams — the demo backbone.

    python -m seed.scenario cyclone_landfall
    python -m seed.scenario flash_flood --speed 10

"Cyclone Landfall" is the four-minute demo: ~200 reports over 90 seconds,
clustered along the coast and the Rushikulya corridor, with 6-10 duplicate
reports per genuine incident. The duplication is the point — it is what the
DBSCAN stage has to collapse, and what a DEOC currently drowns in.

Reports are posted through the real ingest API, not inserted into the database.
The demo therefore exercises the same path a citizen's phone does.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import random
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from seed.ganjam import BLOCKS  # noqa: E402

API = "http://localhost:8000/api/v1/ingest/report"
RNG = random.Random(20260822)

# Hazard mix by scenario. A cyclone is mostly water and stranding, with a tail
# of structural and medical calls; a flash flood is tighter and faster.
CYCLONE_MIX = [
    ("flood", 0.42), ("stranded", 0.24), ("cyclone_damage", 0.12),
    ("building_collapse", 0.08), ("medical", 0.07), ("power_line", 0.05),
    ("fire", 0.02),
]
FLOOD_MIX = [
    ("flood", 0.55), ("stranded", 0.28), ("medical", 0.09),
    ("building_collapse", 0.05), ("power_line", 0.03),
]

# Where a cyclone actually hurts Ganjam: the coastal strip and the river mouth.
CYCLONE_FOCI = [
    ("Gopalpur-on-Sea", 19.2667, 84.9167, 1.00),
    ("Chatrapur",       19.3500, 84.9833, 0.85),
    ("Ganjam",          19.3833, 85.0500, 0.80),
    ("Rambha",          19.5167, 85.1000, 0.60),
    ("Berhampur",       19.3150, 84.7941, 0.75),
    ("Khallikote",      19.6000, 85.0833, 0.45),
    ("Rangeilunda",     19.2833, 84.8167, 0.50),
]

FLOOD_FOCI = [
    ("Aska",            19.6167, 84.6667, 1.00),
    ("Purushottampur",  19.5167, 84.8833, 0.80),
    ("Kabisuryanagar",  19.6167, 84.8000, 0.70),
    ("Hinjilicut",      19.4833, 84.7500, 0.55),
]

DESCRIPTIONS = {
    "flood": ["water 3ft near primary school", "road submerged, cannot cross",
              "water entering houses", "ward flooded, ground floor gone"],
    "stranded": ["6 people on roof", "family stuck, water rising",
                 "12 stranded near the embankment", "cannot leave, boat needed"],
    "cyclone_damage": ["roof blown off", "trees down across the road",
                       "shed collapsed, no injuries"],
    "building_collapse": ["house fell, 3 inside", "wall collapsed on shed",
                          "old building down near market"],
    "medical": ["2 injured, bleeding", "elderly man unconscious",
                "pregnant woman needs transport"],
    "power_line": ["live wire down on road", "transformer sparking"],
    "fire": ["shop on fire near bus stand"],
}


def pick(mix: list[tuple[str, float]]) -> str:
    r = RNG.random()
    acc = 0.0
    for hazard, weight in mix:
        acc += weight
        if r <= acc:
            return hazard
    return mix[-1][0]


def near(lat: float, lng: float, km: float) -> tuple[float, float]:
    r = RNG.uniform(0, km) / 111.0
    theta = RNG.uniform(0, 2 * math.pi)
    return lat + r * math.sin(theta), lng + r * math.cos(theta) / math.cos(math.radians(lat))


def build_events(scenario: str, total: int) -> list[dict]:
    """Generate genuine incidents first, then duplicates around each — which is
    how a real report stream arrives, and what the clustering stage must undo."""
    foci = CYCLONE_FOCI if scenario == "cyclone_landfall" else FLOOD_FOCI
    mix = CYCLONE_MIX if scenario == "cyclone_landfall" else FLOOD_MIX
    weights = [f[3] for f in foci]

    events: list[dict] = []
    genuine = 0
    while len(events) < total:
        focus = RNG.choices(foci, weights=weights, k=1)[0]
        hazard = pick(mix)
        # The genuine event: one real thing happening at one real place.
        elat, elng = near(focus[1], focus[2], 9.0)
        genuine += 1

        duplicates = RNG.randint(6, 10)
        for _ in range(min(duplicates, total - len(events))):
            # Every witness reports it from where they are standing — within a
            # couple of hundred metres, which is exactly the eps we cluster at.
            rlat, rlng = near(elat, elng, 0.22)
            # A minority of reports come by SMS: pincode-grade accuracy only.
            by_sms = RNG.random() < 0.18
            events.append(
                {
                    "lat": round(rlat, 6),
                    "lng": round(rlng, 6),
                    "hazard_type": hazard,
                    "severity_raw": max(1, min(5, round(RNG.gauss(3.4, 0.9)))),
                    "description": RNG.choice(DESCRIPTIONS.get(hazard, ["situation reported"])),
                    "gps_accuracy_m": 3000 if by_sms else RNG.choice([6, 9, 14, 22, 45]),
                    "people_reported": RNG.choice([0, 0, 2, 3, 4, 6, 8, 12]),
                    "phone": f"+9194{RNG.randint(10000000, 99999999)}",
                    "client_report_uuid": str(uuid.uuid4()),
                }
            )
    RNG.shuffle(events)
    return events[:total], genuine


async def run(scenario: str, total: int, duration: float, api: str) -> None:
    events, genuine = build_events(scenario, total)
    gap = duration / max(1, len(events))

    print(f"scenario={scenario} reports={len(events)} "
          f"genuine_incidents~{genuine} over {duration:.0f}s -> {api}")

    sent = failed = 0
    async with httpx.AsyncClient(timeout=10.0) as client:
        for i, event in enumerate(events, 1):
            try:
                resp = await client.post(api, data=event)
                if resp.status_code == 202:
                    sent += 1
                else:
                    failed += 1
            except Exception:
                failed += 1
            if i % 25 == 0:
                print(f"  {i}/{len(events)} sent ({failed} failed)")
            await asyncio.sleep(gap)

    print(f"done: {sent} accepted, {failed} failed")
    print("Watch the dashboard: the pins should collapse into a much smaller "
          "number of incidents within a couple of seconds.")


def main() -> None:
    parser = argparse.ArgumentParser(description="SETU scenario runner")
    parser.add_argument("scenario", nargs="?", default="cyclone_landfall",
                        choices=["cyclone_landfall", "flash_flood"])
    parser.add_argument("--reports", type=int, default=None)
    parser.add_argument("--duration", type=float, default=None,
                        help="seconds to spread the stream over")
    parser.add_argument("--api", default=API)
    args = parser.parse_args()

    defaults = {"cyclone_landfall": (200, 90.0), "flash_flood": (80, 45.0)}
    total, duration = defaults[args.scenario]
    asyncio.run(
        run(args.scenario, args.reports or total, args.duration or duration, args.api)
    )


if __name__ == "__main__":
    main()
