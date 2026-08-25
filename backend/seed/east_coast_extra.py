"""The rest of the east-coast cyclone corridor: Andhra Pradesh and West Bengal.

Odisha's six coastal districts live in `odisha_coastal.py`. This file carries
the districts either side of them, so the covered strip runs continuously from
Nellore in the south to the Sundarbans in the north — roughly 1,100 km of the
Bay of Bengal coast, which is where India's cyclone landfalls concentrate.

Why this strip and not "flood-prone India" generally:

* **Andhra Pradesh coastal** — Srikakulam through Nellore. The Godavari and
  Krishna deltas flood every monsoon, and the Visakhapatnam–Srikakulam stretch
  takes the same Bay depressions that hit south Odisha. Hudhud (2014) came
  ashore at Visakhapatnam; Titli (2018) at Palasa in Srikakulam, 40 km from the
  Ganjam border.
* **West Bengal coastal** — Purba Medinipur and South 24 Parganas. The
  Sundarbans delta is the most exposed inhabited terrain on the coast: Amphan
  (2020) and Yaas (2021) both flooded it, and its islands have single-road
  access that fails first.

Riverine flooding in Assam and Bihar is a genuinely different problem —
Brahmaputra and Kosi inundation lasts weeks rather than hours, and the response
is relief-camp logistics rather than boat dispatch. The allocation engine would
transfer, the hazard model and SLAs would not. Left out deliberately rather than
claimed.

## Provenance — stricter caveats than the Odisha file

* **District names** — Andhra Pradesh reorganised from 13 districts to 26 in
  2022. Names here follow the post-2022 scheme (Konaseema, Bapatla, Kakinada as
  separate districts). Verify against the current AP gazette before a pilot;
  boundaries in particular moved.
* **Block/mandal lists** — REPRESENTATIVE, not exhaustive. Each district has
  many more mandals than are listed; these are the significant settlements and
  the coastal ones, chosen so the population surface and resource placement are
  plausible. A pilot needs the full mandal list.
* **Coordinates** — real towns at their real positions.
* **Pincode prefixes** — correct at the 3-digit level, which is what the
  coverage check keys on. Individual codes are well-known ones; verify against
  the India Post file.
* **Everything else** — shelters, resources, population — generated exactly as
  for Odisha, with the same honesty: synthetic rosters at real locations.
"""

from __future__ import annotations

from .odisha_coastal import District

DISTRICTS_EXTRA: list[District] = [
    # ══ ANDHRA PRADESH ═══════════════════════════════════════════════════
    # ── 7. SRIKAKULAM ─────────────────────────────────────────────────────
    # Directly south of Ganjam. Titli made landfall at Palasa in 2018.
    {
        "id": 7,
        "name": "Srikakulam",
        "state": "Andhra Pradesh",
        "lgd_code": "561",
        "pincode_prefixes": ["532"],
        "blocks": [
            ("Srikakulam",       18.2949, 83.8938, 1.00),
            ("Palasa",           18.7742, 84.4103, 0.50),
            ("Ichchapuram",      19.1167, 84.6833, 0.40),
            ("Sompeta",          18.9333, 84.6000, 0.35),
            ("Tekkali",          18.6083, 84.2361, 0.40),
            ("Kalingapatnam",    18.3333, 84.1333, 0.30),
            ("Amadalavalasa",    18.4167, 83.9000, 0.35),
            ("Narasannapeta",    18.4167, 84.0500, 0.35),
        ],
        "coastal": ["Kalingapatnam", "Ichchapuram", "Sompeta", "Palasa", "Srikakulam"],
        "pincodes": [
            ("532001", "Srikakulam"), ("532221", "Palasa"),
            ("532312", "Ichchapuram"), ("532284", "Sompeta"),
            ("532201", "Tekkali"), ("532263", "Kalingapatnam"),
            ("532185", "Amadalavalasa"), ("532421", "Narasannapeta"),
        ],
    },

    # ── 8. VIZIANAGARAM ───────────────────────────────────────────────────
    {
        "id": 8,
        "name": "Vizianagaram",
        "state": "Andhra Pradesh",
        "lgd_code": "560",
        "pincode_prefixes": ["535"],
        "blocks": [
            ("Vizianagaram",     18.1067, 83.3956, 1.00),
            ("Bobbili",          18.5667, 83.3667, 0.45),
            ("Parvathipuram",    18.7833, 83.4250, 0.45),
            ("Salur",            18.5167, 83.2000, 0.35),
            ("Cheepurupalli",    18.3167, 83.5667, 0.35),
            ("Bhogapuram",       18.0167, 83.5167, 0.35),
            ("Nellimarla",       18.1667, 83.4333, 0.30),
        ],
        "coastal": ["Bhogapuram", "Vizianagaram", "Nellimarla"],
        "pincodes": [
            ("535001", "Vizianagaram"), ("535558", "Bobbili"),
            ("535501", "Parvathipuram"), ("535591", "Salur"),
            ("535128", "Cheepurupalli"), ("535216", "Bhogapuram"),
            ("535217", "Nellimarla"),
        ],
    },

    # ── 9. VISAKHAPATNAM ──────────────────────────────────────────────────
    # Hudhud (2014) landfall. Largest urban population on this coast, and the
    # only district here with high-rise exposure as well as surge exposure.
    {
        "id": 9,
        "name": "Visakhapatnam",
        "state": "Andhra Pradesh",
        "lgd_code": "559",
        "pincode_prefixes": ["530", "531"],
        "blocks": [
            ("Visakhapatnam",    17.6868, 83.2185, 1.00),
            ("Gajuwaka",         17.6833, 83.2000, 0.70),
            ("Bheemunipatnam",   17.8917, 83.4500, 0.45),
            ("Anakapalle",       17.6911, 83.0044, 0.55),
            ("Pendurthi",        17.8167, 83.1000, 0.45),
            ("Yelamanchili",     17.5500, 82.8500, 0.35),
            ("Narsipatnam",      17.6667, 82.6167, 0.35),
            ("Paderu",           18.0833, 82.6667, 0.25),
        ],
        "coastal": ["Visakhapatnam", "Bheemunipatnam", "Gajuwaka", "Anakapalle"],
        "pincodes": [
            ("530001", "Visakhapatnam"), ("530002", "Visakhapatnam"),
            ("530016", "Visakhapatnam"), ("530026", "Gajuwaka"),
            ("531162", "Bheemunipatnam"), ("531001", "Anakapalle"),
            ("531173", "Pendurthi"), ("531055", "Yelamanchili"),
            ("531116", "Narsipatnam"), ("531024", "Paderu"),
        ],
    },

    # ── 10. KAKINADA ──────────────────────────────────────────────────────
    # Northern Godavari delta. Deep-water port; heavy fishing fleet exposure.
    {
        "id": 10,
        "name": "Kakinada",
        "state": "Andhra Pradesh",
        "lgd_code": "545",
        "pincode_prefixes": ["533"],
        "blocks": [
            ("Kakinada",         16.9891, 82.2475, 1.00),
            ("Uppada",           17.0833, 82.3333, 0.35),
            ("Pithapuram",       17.1167, 82.2500, 0.40),
            ("Samalkota",        17.0500, 82.1667, 0.40),
            ("Peddapuram",       17.0833, 82.1333, 0.40),
            ("Tuni",             17.3500, 82.5500, 0.40),
            ("Rajanagaram",      17.0333, 81.9000, 0.30),
        ],
        "coastal": ["Kakinada", "Uppada", "Tuni", "Pithapuram"],
        "pincodes": [
            ("533001", "Kakinada"), ("533005", "Kakinada"),
            ("533447", "Uppada"), ("533450", "Pithapuram"),
            ("533440", "Samalkota"), ("533437", "Peddapuram"),
            ("533401", "Tuni"), ("533294", "Rajanagaram"),
        ],
    },

    # ── 11. KONASEEMA ─────────────────────────────────────────────────────
    # The Godavari delta islands. Lowest-lying inhabited land on this coast;
    # inundation here is measured in days, not hours.
    {
        "id": 11,
        "name": "Konaseema",
        "state": "Andhra Pradesh",
        "lgd_code": "546",
        "pincode_prefixes": ["533"],
        "blocks": [
            ("Amalapuram",       16.5786, 82.0064, 1.00),
            ("Razole",           16.4667, 81.8333, 0.45),
            ("Mummidivaram",     16.6500, 82.1167, 0.40),
            ("Kothapeta",        16.7333, 81.9167, 0.40),
            ("Ramachandrapuram", 16.8333, 82.0333, 0.40),
            ("Antarvedi",        16.3167, 81.7333, 0.25),
            ("Malikipuram",      16.4000, 81.7833, 0.30),
        ],
        "coastal": ["Antarvedi", "Razole", "Malikipuram", "Amalapuram", "Mummidivaram"],
        "pincodes": [
            ("533201", "Amalapuram"), ("533242", "Razole"),
            ("533216", "Mummidivaram"), ("533223", "Kothapeta"),
            ("533255", "Ramachandrapuram"), ("533252", "Antarvedi"),
            ("533253", "Malikipuram"),
        ],
    },

    # ── 12. KRISHNA ───────────────────────────────────────────────────────
    # Krishna delta and the Machilipatnam coast.
    {
        "id": 12,
        "name": "Krishna",
        "state": "Andhra Pradesh",
        "lgd_code": "550",
        "pincode_prefixes": ["521"],
        "blocks": [
            ("Machilipatnam",    16.1875, 81.1389, 1.00),
            ("Gudivada",         16.4333, 80.9833, 0.55),
            ("Pedana",           16.2667, 81.1333, 0.35),
            ("Avanigadda",       16.0167, 80.9167, 0.35),
            ("Bantumilli",       16.2833, 81.2500, 0.30),
            ("Kaikaluru",        16.5500, 81.2167, 0.35),
            ("Nuzvid",           16.7833, 80.8500, 0.35),
        ],
        "coastal": ["Machilipatnam", "Avanigadda", "Bantumilli", "Pedana"],
        "pincodes": [
            ("521001", "Machilipatnam"), ("521301", "Gudivada"),
            ("521366", "Pedana"), ("521121", "Avanigadda"),
            ("521324", "Bantumilli"), ("521333", "Kaikaluru"),
            ("521201", "Nuzvid"),
        ],
    },

    # ── 13. BAPATLA ───────────────────────────────────────────────────────
    # Nizampatnam bay. Long shallow shelf, so surge runs far inland here.
    {
        "id": 13,
        "name": "Bapatla",
        "state": "Andhra Pradesh",
        "lgd_code": "549",
        "pincode_prefixes": ["522", "523"],
        "blocks": [
            ("Bapatla",          15.9044, 80.4675, 1.00),
            ("Chirala",          15.8236, 80.3522, 0.60),
            ("Repalle",          16.0167, 80.8333, 0.45),
            ("Nizampatnam",      15.9000, 80.6667, 0.30),
            ("Parchur",          15.9833, 80.1833, 0.30),
            ("Vetapalem",        15.7833, 80.3167, 0.30),
        ],
        "coastal": ["Bapatla", "Nizampatnam", "Chirala", "Repalle", "Vetapalem"],
        "pincodes": [
            ("522101", "Bapatla"), ("523155", "Chirala"),
            ("522265", "Repalle"), ("522314", "Nizampatnam"),
            ("523169", "Parchur"), ("523187", "Vetapalem"),
        ],
    },

    # ── 14. NELLORE ───────────────────────────────────────────────────────
    # Southern end of the corridor. Pulicat lagoon and the Krishnapatnam port.
    {
        "id": 14,
        "name": "Nellore",
        "state": "Andhra Pradesh",
        "lgd_code": "555",
        "pincode_prefixes": ["524"],
        "blocks": [
            ("Nellore",          14.4426, 79.9865, 1.00),
            ("Kavali",           14.9139, 79.9931, 0.50),
            ("Gudur",            14.1500, 79.8500, 0.45),
            ("Sullurpeta",       13.7000, 80.0167, 0.35),
            ("Naidupeta",        13.9000, 79.8833, 0.35),
            ("Krishnapatnam",    14.2500, 80.1167, 0.30),
            ("Vakadu",           14.0833, 80.0833, 0.25),
        ],
        "coastal": ["Krishnapatnam", "Vakadu", "Gudur", "Sullurpeta", "Kavali"],
        "pincodes": [
            ("524001", "Nellore"), ("524201", "Kavali"),
            ("524101", "Gudur"), ("524121", "Sullurpeta"),
            ("524126", "Naidupeta"), ("524344", "Krishnapatnam"),
            ("524415", "Vakadu"),
        ],
    },

    # ══ WEST BENGAL ══════════════════════════════════════════════════════
    # ── 15. PURBA MEDINIPUR ───────────────────────────────────────────────
    # Digha to Haldia. Directly north of Balasore, so the corridor stays
    # continuous across the state border.
    {
        "id": 15,
        "name": "Purba Medinipur",
        "state": "West Bengal",
        "lgd_code": "334",
        "pincode_prefixes": ["721"],
        "blocks": [
            ("Tamluk",           22.3000, 87.9167, 1.00),
            ("Haldia",           22.0667, 88.0667, 0.70),
            ("Contai",           21.7800, 87.7500, 0.55),
            ("Digha",            21.6270, 87.5090, 0.35),
            ("Ramnagar",         21.6500, 87.5500, 0.30),
            ("Egra",             21.9000, 87.5333, 0.40),
            ("Nandigram",        22.0167, 87.9833, 0.35),
        ],
        "coastal": ["Digha", "Ramnagar", "Contai", "Haldia", "Nandigram"],
        "pincodes": [
            ("721636", "Tamluk"), ("721607", "Haldia"),
            ("721401", "Contai"), ("721428", "Digha"),
            ("721446", "Ramnagar"), ("721429", "Egra"),
            ("721631", "Nandigram"),
        ],
    },

    # ── 16. SOUTH 24 PARGANAS ─────────────────────────────────────────────
    # The Sundarbans. Most exposed inhabited terrain on the coast: island
    # settlements with single-road or boat-only access, embankments that fail
    # under surge, and a population that cannot self-evacuate by road.
    {
        "id": 16,
        "name": "South 24 Parganas",
        "state": "West Bengal",
        "lgd_code": "343",
        "pincode_prefixes": ["743"],
        "blocks": [
            ("Baruipur",         22.3667, 88.4333, 1.00),
            ("Diamond Harbour",  22.1900, 88.1900, 0.65),
            ("Canning",          22.3167, 88.6667, 0.55),
            ("Kakdwip",          21.8833, 88.1833, 0.45),
            ("Namkhana",         21.7667, 88.2333, 0.35),
            ("Sagar",            21.6500, 88.0833, 0.35),
            ("Gosaba",           22.1667, 88.8000, 0.35),
            ("Basanti",          22.1833, 88.6667, 0.35),
        ],
        "coastal": ["Sagar", "Namkhana", "Kakdwip", "Gosaba", "Basanti",
                    "Diamond Harbour"],
        "pincodes": [
            ("743302", "Baruipur"), ("743331", "Diamond Harbour"),
            ("743329", "Canning"), ("743347", "Kakdwip"),
            ("743357", "Namkhana"), ("743373", "Sagar"),
            ("743370", "Gosaba"), ("743312", "Basanti"),
        ],
    },
]
