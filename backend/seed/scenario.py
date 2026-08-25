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

from seed.odisha_coastal import (  # noqa: E402
    DISTRICTS,
    DISTRICT_BY_ID,
    District,
)

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

def foci_for(district: District, scenario: str) -> list[tuple[str, float, float, float]]:
    """Where this disaster actually lands, derived from the district itself.

    These used to be seven hardcoded Ganjam place names, which meant the
    scenario engine could only ever populate Ganjam. Once the whole east-coast
    corridor was seeded that became the reason fifteen districts sat empty: the
    simulator was structurally incapable of reporting into them, so switching
    the dashboard's district picker showed a blank board and made the coverage
    claim look hollow.

    Now the geography comes from the district record:

    * **cyclone_landfall** hits the coast. Foci are the district's `coastal`
      blocks — the ones fronting the sea or a river mouth — because that is
      where surge and wind damage concentrate.
    * **flash_flood** hits inland. Foci are the NON-coastal blocks, which is
      where river and drainage flooding actually happens; a flash flood
      centred on a beach is not a scenario, it is a bug.

    Weights carry over from the block's population weight, so a landfall near
    the district headquarters generates more reports than one near a village —
    which is what report volume actually looks like.
    """
    blocks = district["blocks"]
    coastal_names = set(district.get("coastal") or [])

    # EVERY block is a possible focus. An earlier version selected only the
    # coastal ones for a cyclone and only the inland ones for a flood, which
    # left half of each district permanently incident-free — a Ganjam landfall
    # never touched Bhanjanagar, and the operator's map had a hard edge down
    # the middle that no real storm produces.
    #
    # A cyclone is a district-wide event. Surge and the worst wind damage
    # concentrate on the coast, but trees come down, power lines fail and roofs
    # go inland too. So the geography is expressed as a WEIGHTING rather than a
    # filter: every block can generate reports, and the hazard decides where
    # they cluster.
    # A storm has a LANDFALL POINT, and damage decays away from it.
    #
    # Weighting every block roughly equally — which is what "spread it across
    # the whole district" naively produces — is both unphysical and quietly
    # destroys the demo: incidents end up ~29 km apart with five idle boats
    # within reach of each, so there is no contention, and Hungarian and greedy
    # return the same plan. The optimizer's advantage is a scarcity effect; if
    # nothing is scarce there is nothing to show.
    #
    # So: pick where it comes ashore, then decay from there. Every block still
    # generates reports — nothing is zero, the whole district is covered — but
    # the footprint is concentrated the way a real cyclone is, which is also
    # what puts several incidents in competition for the same nearby boat.
    candidates = [b for b in blocks if b[0] in coastal_names] or list(blocks)
    if scenario == "flash_flood":
        # River flooding starts inland and runs down; the "landfall" is the
        # worst-hit inland block.
        candidates = [b for b in blocks if b[0] not in coastal_names] or list(blocks)

    landfall = RNG.choices(candidates, weights=[c[3] for c in candidates], k=1)[0]
    # ~28 km e-folding distance. A severe cyclonic storm's damaging-wind radius
    # is tens of kilometres, so this puts most reports within a block or two of
    # landfall and a thinning tail across the rest of the district.
    decay_km = 28.0

    out: list[tuple[str, float, float, float]] = []
    for name, lat, lng, weight in blocks:
        d_km = math.hypot((lat - landfall[1]) * 111,
                          (lng - landfall[2]) * 111 * math.cos(math.radians(lat)))
        intensity = math.exp(-((d_km / decay_km) ** 2))
        # Floor of 0.04 so no block is ever silent: a district-wide event should
        # produce a trickle everywhere, not a hard edge at the storm's rim.
        out.append((name, lat, lng, round(weight * max(intensity, 0.04), 4)))

    print(f"  landfall: {landfall[0]}")
    return out

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


def build_events(scenario: str, total: int, district: District) -> list[dict]:
    """Generate genuine incidents first, then duplicates around each — which is
    how a real report stream arrives, and what the clustering stage must undo."""
    foci = foci_for(district, scenario)
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


async def run(
    scenario: str, total: int, duration: float, api: str, district: District
) -> None:
    events, genuine = build_events(scenario, total, district)
    gap = duration / max(1, len(events))

    print(f"scenario={scenario} district={district['name']} ({district['state']}) "
          f"reports={len(events)} genuine_incidents~{genuine} "
          f"over {duration:.0f}s -> {api}")

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
    # Demo control. The queue should start SMALL — a board already showing 26
    # incidents gives an audience nothing to watch, whereas five on screen and
    # the rest arriving live is the whole point of a live operating picture.
    # Each genuine event carries 6-10 duplicate reports, so this just works
    # backwards from the incident count you want on screen.
    parser.add_argument("--incidents", type=int, default=None,
                        help="approximate number of distinct incidents to produce")
    parser.add_argument("--duration", type=float, default=None,
                        help="seconds to spread the stream over")
    parser.add_argument("--api", default=API)
    parser.add_argument("--district", type=int, default=1,
                        help="district id to simulate (see --list-districts)")
    parser.add_argument("--all", action="store_true",
                        help="run the scenario in EVERY seeded district")
    parser.add_argument("--list-districts", action="store_true")
    args = parser.parse_args()

    if args.list_districts:
        for d in DISTRICTS:
            coastal = len(d.get("coastal") or [])
            print(f"{d['id']:>3}  {d['name']:18s} {d['state']:16s} "
                  f"{len(d['blocks']):2d} blocks, {coastal} coastal")
        raise SystemExit(0)

    defaults = {"cyclone_landfall": (200, 90.0), "flash_flood": (80, 45.0)}
    total, duration = defaults[args.scenario]

    if args.incidents:
        # 8 is the midpoint of the 6-10 duplicates each genuine event emits.
        total = args.incidents * 8
        duration = args.duration if args.duration is not None else max(10.0, total * 0.45)

    total = args.reports or total
    duration = args.duration or duration

    if args.all:
        # A corridor-wide event. Each district gets its own landfall, so the
        # dashboard's district picker shows a live board wherever it is pointed
        # rather than one populated district and fifteen empty ones.
        targets = DISTRICTS
        per_district = max(8, total // len(targets))
        print(f"corridor run: {len(targets)} districts x ~{per_district} reports")
        for d in targets:
            asyncio.run(run(args.scenario, per_district, duration, args.api, d))
        return

    if args.district not in DISTRICT_BY_ID:
        raise SystemExit(f"unknown district {args.district}; try --list-districts")

    asyncio.run(
        run(args.scenario, total, duration, args.api, DISTRICT_BY_ID[args.district])
    )


if __name__ == "__main__":
    main()
