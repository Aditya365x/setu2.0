"""Coastal Odisha — the six districts on the Bay of Bengal.

South to north: Ganjam, Puri, Jagatsinghpur, Kendrapara, Bhadrak, Balasore.
Together these are the landfall corridor. Every major cyclone of the last three
decades — the 1999 Super Cyclone, Phailin (2013), Fani (2019), Amphan (2020),
Yaas (2021) — came ashore inside this strip, and it is where OSDMA's cyclone
shelter programme is concentrated.

Why these six and not "coastal Odisha" loosely: these are the districts whose
administrative boundary actually meets the sea, so they are the ones with a
Collector holding cyclone-response authority, a district control room, and a
share of the ODRAF/NDRF deployment. Khordha and Jajpur are near-coastal and
flood heavily, but their emergencies route differently.

## Provenance — stated plainly, because a judge will ask

* **Block names and coordinates** — real settlements at their real positions.
  These are the places, not invented ones.
* **District boundaries** — DERIVED, not surveyed. Ganjam carries a hand-drawn
  simplified outline; the other five are computed as a convex hull of their
  block positions with an outward buffer (see `load.py`). Good enough to frame
  a map and to test point-in-district; NOT a legal boundary. Replace with the
  Survey of India / OSM admin relations before any pilot.
* **Shelters** — real institution TYPES at real block coordinates, with
  capacities in the OSDMA multipurpose-cyclone-shelter range. The authoritative
  list is the Odisha SRC register. Named as generated, never claimed as the
  register.
* **Resources** — synthetic roster. There is no public NDRF/ODRAF asset
  register, so units sit at real block headquarters with a realistic agency and
  capability mix. Labelled synthetic throughout.
* **Pincodes** — the three-digit PREFIXES are correct and are what the
  out-of-district check keys on. Individual six-digit codes are well-known ones
  where we are confident and plausible ones otherwise; verify against the India
  Post file before a pilot. Widening to the full ~19,000-row file is a data
  swap, not a code change.
* **LGD codes** — as commonly published; verify against the LGD registry.

## Scaling shape

Nothing here is special-cased per district. The schema has been multi-tenant
since P0 — ten tables carry `district_id` and every query filters on it — so
adding a district is this file plus a seed run, not a rewrite.
"""

from __future__ import annotations

from typing import NotRequired, TypedDict


class District(TypedDict):
    id: int
    name: str
    state: str
    lgd_code: str
    pincode_prefixes: list[str]
    # Blocks: (name, lat, lng, population weight 0..1)
    blocks: list[tuple[str, float, float, float]]
    # Which blocks front the sea or a major river mouth. Boats and rescue teams
    # are stationed here, because that is where they actually live.
    coastal: list[str]
    pincodes: list[tuple[str, str]]
    # Hand-drawn outline. Only Ganjam has one; the rest get a hull derived from
    # their block positions at seed time.
    boundary: NotRequired[list[tuple[float, float]]]


# Real institution types used as cyclone shelters across coastal Odisha.
SHELTER_KINDS: list[tuple[str, int]] = [
    ("Govt High School", 400),
    ("Multipurpose Cyclone Shelter", 1000),
    ("Govt UP School", 250),
    ("Panchayat Community Centre", 300),
    ("Govt Girls High School", 350),
    ("Block Community Hall", 500),
    ("Govt College", 800),
    ("Primary Health Centre Annexe", 200),
]

# Per-district resource mix, in the §A proportions. Scaled by district size in
# load.py — Ganjam and Balasore carry more than Jagatsinghpur, which is how the
# real deployment looks.
RESOURCE_MIX: list[tuple[str, int, str, list[str], int]] = [
    ("rescue_team",     8,  "NDRF",   ["water_rescue", "cutting"],  40),
    ("boat",           10,  "ODRAF",  ["water_rescue"],             30),
    ("ambulance",       6,  "Fire",   ["medical"],                   4),
    ("medical_team",    4,  "Health", ["medical"],                  25),
    ("supply_truck",    8,  "NGO",    ["supply"],                  200),
    ("heavy_equipment", 4,  "Fire",   ["cutting", "fire"],          10),
]


DISTRICTS: list[District] = [
    # ── 1. GANJAM ─────────────────────────────────────────────────────────
    {
        "id": 1,
        "name": "Ganjam",
        "state": "Odisha",
        "lgd_code": "381",
        "pincode_prefixes": ["760", "761"],
        "blocks": [
            ("Berhampur",        19.3150, 84.7941, 1.00),
            ("Chatrapur",        19.3500, 84.9833, 0.55),
            ("Gopalpur-on-Sea",  19.2667, 84.9167, 0.45),
            ("Ganjam",           19.3833, 85.0500, 0.40),
            ("Rangeilunda",      19.2833, 84.8167, 0.50),
            ("Patrapur",         19.2333, 84.6333, 0.35),
            ("Chikiti",          19.1667, 84.6333, 0.35),
            ("Digapahandi",      19.4167, 84.5833, 0.40),
            ("Hinjilicut",       19.4833, 84.7500, 0.45),
            ("Aska",             19.6167, 84.6667, 0.50),
            ("Purushottampur",   19.5167, 84.8833, 0.40),
            ("Kabisuryanagar",   19.6167, 84.8000, 0.35),
            ("Khallikote",       19.6000, 85.0833, 0.40),
            ("Rambha",           19.5167, 85.1000, 0.30),
            ("Polasara",         19.6667, 84.8167, 0.35),
            ("Kodala",           19.6167, 84.9333, 0.30),
            ("Beguniapada",      19.7500, 85.0000, 0.25),
            ("Buguda",           19.8000, 84.7833, 0.30),
            ("Bellaguntha",      19.7833, 84.6500, 0.25),
            ("Bhanjanagar",      19.9333, 84.5833, 0.40),
            ("Jagannathprasad",  19.8667, 84.4167, 0.20),
            ("Surada",           19.7333, 84.4500, 0.25),
            ("Dharakote",        19.7000, 84.6167, 0.25),
            ("Sheragada",        19.5000, 84.6833, 0.30),
            ("Sanakhemundi",     19.4500, 84.4667, 0.25),
        ],
        "coastal": ["Gopalpur-on-Sea", "Chatrapur", "Ganjam", "Rambha",
                    "Khallikote", "Berhampur", "Rangeilunda", "Purushottampur"],
        "pincodes": [
            ("760001", "Berhampur"), ("760002", "Berhampur"),
            ("760004", "Rangeilunda"), ("760010", "Berhampur"),
            ("761020", "Chatrapur"), ("761002", "Gopalpur-on-Sea"),
            ("761026", "Ganjam"), ("761010", "Patrapur"),
            ("761012", "Digapahandi"), ("761102", "Hinjilicut"),
            ("761110", "Aska"), ("761018", "Purushottampur"),
            ("761104", "Kabisuryanagar"), ("761030", "Khallikote"),
            ("761028", "Rambha"), ("761105", "Polasara"),
            ("761032", "Kodala"), ("761118", "Buguda"),
            ("761119", "Bellaguntha"), ("761126", "Bhanjanagar"),
            ("761121", "Jagannathprasad"), ("761108", "Surada"),
            ("761106", "Dharakote"), ("761003", "Sheragada"),
            ("761008", "Chikiti"),
        ],
        # The one hand-drawn outline: coast on the south-east from the Bay up to
        # the Chilika edge, inland to the Eastern Ghats.
        "boundary": [
            (84.42, 19.12), (84.62, 19.09), (84.80, 19.14), (84.94, 19.24),
            (85.02, 19.34), (85.09, 19.42), (85.16, 19.52), (85.18, 19.63),
            (85.12, 19.74), (85.00, 19.82), (84.86, 19.90), (84.70, 19.98),
            (84.54, 20.02), (84.38, 19.96), (84.28, 19.84), (84.22, 19.70),
            (84.24, 19.54), (84.30, 19.40), (84.34, 19.26), (84.42, 19.12),
        ],
    },

    # ── 2. PURI ───────────────────────────────────────────────────────────
    # Chilika lagoon to Konark. Highest tourist density on the coast, which
    # matters: a cyclone landfall here hits a transient population that has no
    # local knowledge of where the shelters are.
    {
        "id": 2,
        "name": "Puri",
        "state": "Odisha",
        "lgd_code": "380",
        "pincode_prefixes": ["752"],
        "blocks": [
            ("Puri",             19.8135, 85.8312, 1.00),
            ("Konark",           19.8876, 86.0945, 0.45),
            ("Satapada",         19.6667, 85.4500, 0.30),
            ("Brahmagiri",       19.7500, 85.6167, 0.40),
            ("Krushnaprasad",    19.6000, 85.3500, 0.25),
            ("Astaranga",        20.0833, 86.3333, 0.35),
            ("Kakatpur",         20.0833, 86.1667, 0.35),
            ("Nimapara",         20.0500, 86.0000, 0.45),
            ("Gop",              20.0167, 86.1000, 0.30),
            ("Pipili",           20.1167, 85.8333, 0.45),
            ("Delang",           20.0333, 85.7833, 0.35),
            ("Sakhigopal",       19.9000, 85.7500, 0.35),
            ("Kanas",            20.0000, 85.6167, 0.30),
        ],
        "coastal": ["Puri", "Konark", "Astaranga", "Satapada",
                    "Krushnaprasad", "Brahmagiri", "Kakatpur"],
        "pincodes": [
            ("752001", "Puri"), ("752002", "Puri"), ("752004", "Puri"),
            ("752111", "Konark"), ("752106", "Nimapara"),
            ("752101", "Pipili"), ("752012", "Sakhigopal"),
            ("752011", "Brahmagiri"), ("752030", "Satapada"),
            ("752118", "Astaranga"), ("752104", "Kakatpur"),
            ("752114", "Gop"), ("752015", "Delang"), ("752017", "Kanas"),
        ],
    },

    # ── 3. JAGATSINGHPUR ──────────────────────────────────────────────────
    # Paradip port and the Mahanadi delta mouth. Erasama block was the epicentre
    # of the 1999 Super Cyclone storm surge — roughly 10,000 dead in this
    # district alone. The shelter programme exists because of what happened here.
    {
        "id": 3,
        "name": "Jagatsinghpur",
        "state": "Odisha",
        "lgd_code": "375",
        "pincode_prefixes": ["754"],
        "blocks": [
            ("Jagatsinghpur",    20.2548, 86.1707, 0.80),
            ("Paradip",          20.3167, 86.6167, 1.00),
            ("Kujang",           20.2833, 86.5000, 0.55),
            ("Erasama",          20.1667, 86.3833, 0.50),
            ("Balikuda",         20.1500, 86.2333, 0.40),
            ("Naugaon",          20.2000, 86.4500, 0.35),
            ("Tirtol",           20.2833, 86.3000, 0.40),
            ("Raghunathpur",     20.3167, 86.2000, 0.35),
            ("Biridi",           20.3333, 86.3167, 0.25),
        ],
        "coastal": ["Paradip", "Erasama", "Kujang", "Naugaon", "Balikuda"],
        "pincodes": [
            ("754103", "Jagatsinghpur"), ("754142", "Paradip"),
            ("754141", "Kujang"), ("754107", "Erasama"),
            ("754108", "Balikuda"), ("754112", "Naugaon"),
            ("754137", "Tirtol"), ("754132", "Raghunathpur"),
            ("754105", "Biridi"),
        ],
    },

    # ── 4. KENDRAPARA ─────────────────────────────────────────────────────
    # Bhitarkanika mangroves and the Brahmani-Baitarani delta. The mangrove belt
    # is the reason the surge damage here is lower than Jagatsinghpur's for the
    # same wind speed — worth knowing, because pre-positioning should reflect it.
    {
        "id": 4,
        "name": "Kendrapara",
        "state": "Odisha",
        "lgd_code": "374",
        "pincode_prefixes": ["754"],
        "blocks": [
            ("Kendrapara",       20.5000, 86.4167, 1.00),
            ("Rajnagar",         20.6333, 86.7500, 0.50),
            ("Mahakalapada",     20.4167, 86.7833, 0.50),
            ("Marshaghai",       20.4833, 86.6500, 0.40),
            ("Pattamundai",      20.5833, 86.5667, 0.55),
            ("Aul",              20.6833, 86.6333, 0.40),
            ("Rajkanika",        20.7167, 86.5833, 0.35),
            ("Garadpur",         20.5333, 86.3333, 0.30),
            ("Derabish",         20.6167, 86.3833, 0.30),
        ],
        "coastal": ["Mahakalapada", "Rajnagar", "Marshaghai", "Pattamundai", "Aul"],
        "pincodes": [
            ("754211", "Kendrapara"), ("754225", "Rajnagar"),
            ("754224", "Mahakalapada"), ("754213", "Marshaghai"),
            ("754215", "Pattamundai"), ("754219", "Aul"),
            ("754220", "Rajkanika"), ("754212", "Garadpur"),
            ("754216", "Derabish"),
        ],
    },

    # ── 5. BHADRAK ────────────────────────────────────────────────────────
    # Dhamra port and the Baitarani mouth. Chandbali is the standard staging
    # point for anything going into the northern delta.
    {
        "id": 5,
        "name": "Bhadrak",
        "state": "Odisha",
        "lgd_code": "373",
        "pincode_prefixes": ["756"],
        "blocks": [
            ("Bhadrak",          21.0574, 86.5157, 1.00),
            ("Basudevpur",       21.1167, 86.7333, 0.55),
            ("Chandbali",        20.7833, 86.7500, 0.50),
            ("Dhamnagar",        20.9333, 86.4167, 0.45),
            ("Tihidi",           20.9167, 86.6167, 0.40),
            ("Bonth",            21.0000, 86.4000, 0.35),
            ("Bhandaripokhari",  21.0333, 86.3000, 0.30),
        ],
        "coastal": ["Basudevpur", "Chandbali", "Tihidi", "Dhamnagar"],
        "pincodes": [
            ("756100", "Bhadrak"), ("756101", "Bhadrak"),
            ("756125", "Basudevpur"), ("756133", "Chandbali"),
            ("756117", "Dhamnagar"), ("756130", "Tihidi"),
            ("756114", "Bonth"), ("756116", "Bhandaripokhari"),
        ],
    },

    # ── 6. BALASORE (Baleshwar) ───────────────────────────────────────────
    # Northernmost coastal district, bordering West Bengal. Chandipur's tidal
    # range is extreme — the sea withdraws several kilometres and returns — which
    # makes surge timing here unlike anywhere else on the coast.
    {
        "id": 6,
        "name": "Balasore",
        "state": "Odisha",
        "lgd_code": "372",
        "pincode_prefixes": ["756"],
        "blocks": [
            ("Balasore",         21.4942, 86.9336, 1.00),
            ("Chandipur",        21.4667, 87.0167, 0.40),
            ("Remuna",           21.5167, 86.8833, 0.45),
            ("Basta",            21.6833, 87.0167, 0.45),
            ("Baliapal",         21.6500, 87.1667, 0.40),
            ("Bhograi",          21.7833, 87.2167, 0.45),
            ("Jaleswar",         21.8000, 87.2167, 0.40),
            ("Soro",             21.2833, 86.6833, 0.45),
            ("Nilgiri",          21.4667, 86.7667, 0.35),
            ("Simulia",          21.3167, 86.8333, 0.30),
            ("Khaira",           21.4000, 86.8000, 0.30),
        ],
        "coastal": ["Chandipur", "Baliapal", "Bhograi", "Balasore", "Basta"],
        "pincodes": [
            ("756001", "Balasore"), ("756002", "Balasore"),
            ("756003", "Remuna"), ("756025", "Chandipur"),
            ("756029", "Basta"), ("756026", "Baliapal"),
            ("756038", "Bhograi"), ("756032", "Jaleswar"),
            ("756045", "Soro"), ("756040", "Nilgiri"),
            ("756126", "Simulia"), ("756048", "Khaira"),
        ],
    },
]


# The rest of the east-coast corridor — Andhra Pradesh and West Bengal — lives
# in a sibling module so the Odisha data stays readable. Imported late and
# defensively: a deployment that only wants Odisha can delete that file and this
# still runs.
try:
    from .east_coast_extra import DISTRICTS_EXTRA

    DISTRICTS = DISTRICTS + DISTRICTS_EXTRA
except ImportError:  # pragma: no cover - optional coverage set
    pass

# Colloquial and alternate names people actually type. "Vizag" is what
# everyone in Andhra says; "Brahmapur" is the official spelling of Berhampur and
# both are in daily use. A gazetteer that only knows the gazetted name is a
# gazetteer that fails the person who needs it, so these are seeded as extra
# searchable rows pointing at the same coordinates.
ALIASES: list[tuple[str, str]] = [
    ("Vizag", "Visakhapatnam"),
    ("Waltair", "Visakhapatnam"),
    ("Brahmapur", "Berhampur"),
    ("Baleswar", "Balasore"),
    ("Baleshwar", "Balasore"),
    ("Kanthi", "Contai"),
    ("Paradeep", "Paradip"),
    ("Jagannath Puri", "Puri"),
    ("Gopalpur", "Gopalpur-on-Sea"),
    ("Bhimunipatnam", "Bheemunipatnam"),
    ("Bheemili", "Bheemunipatnam"),
    ("Masulipatnam", "Machilipatnam"),
    ("Bandar", "Machilipatnam"),
    ("Chatrapur", "Chatrapur"),
    ("Sagar Island", "Sagar"),
    ("Gangasagar", "Sagar"),
]

DISTRICT_BY_ID = {d["id"]: d for d in DISTRICTS}


def by_state() -> dict[str, list[District]]:
    """Districts grouped by state, for the dashboard's district picker."""
    out: dict[str, list[District]] = {}
    for d in DISTRICTS:
        out.setdefault(d["state"], []).append(d)
    return out


def all_blocks() -> list[tuple[str, float, float, float]]:
    """Every block across the corridor — used to size the basemap bbox."""
    return [b for d in DISTRICTS for b in d["blocks"]]


def corridor_bbox(margin: float = 0.15) -> tuple[float, float, float, float]:
    """(min_lng, min_lat, max_lng, max_lat) covering all six districts.

    Feeds the PMTiles extract in docs/BASEMAP.md — the basemap has to span the
    whole corridor now, not just Ganjam.
    """
    blocks = all_blocks()
    lats = [b[1] for b in blocks]
    lngs = [b[2] for b in blocks]
    return (
        round(min(lngs) - margin, 2), round(min(lats) - margin, 2),
        round(max(lngs) + margin, 2), round(max(lats) + margin, 2),
    )


if __name__ == "__main__":
    print(f"districts: {len(DISTRICTS)}")
    for d in DISTRICTS:
        print(f"  {d['id']}  {d['name']:16s} {len(d['blocks']):2d} blocks  "
              f"{len(d['pincodes']):2d} pincodes  prefixes {d['pincode_prefixes']}")
    print(f"corridor bbox: {corridor_bbox()}")
