"""§9.2 — inbound SMS grammar.

    FORMAT: <HAZARD> <SEVERITY 1-5> <PINCODE> [free text landmark]
    FLOOD 4 761008 water 3ft near primary school
    BAADH 4 761008 paani 3 foot school ke paas

The parser is deliberately tolerant. Every fallback below exists because the
alternative is dropping a message from someone who may be in the water:

* severity missing            -> default 3
* pincode missing             -> district centroid
* hazard keyword unknown      -> 'other', flagged for operator review
* message entirely unparseable-> still ingested, raw text preserved

A pincode resolves to a centroid with roughly 2-5 km accuracy, so we store
gps_accuracy_m = 3000. That automatically lowers the report's trust score
(§6.3) and widens its clustering radius — the system treats SMS as genuinely
lower-precision rather than pretending it is GPS-grade, and the operator sees
an uncertainty circle instead of a false-precision pin.
"""

import re
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

PINCODE_ACCURACY_M = 3000
DEFAULT_SEVERITY = 3

# Multilingual lookup. Hindi and Odia script variants live here too, so a
# message typed in Devanagari resolves the same way as a transliterated one.
HAZARD_KEYWORDS: dict[str, str] = {
    # flood
    "FLOOD": "flood", "BAADH": "flood", "BANYA": "flood", "BADH": "flood",
    "बाढ़": "flood", "ବନ୍ୟା": "flood",
    # landslide
    "LANDSLIDE": "landslide", "BHUSKHALAN": "landslide", "भूस्खलन": "landslide",
    # medical
    "MEDICAL": "medical", "MED": "medical", "DOCTOR": "medical", "ILAAJ": "medical",
    "चिकित्सा": "medical", "ଡାକ୍ତର": "medical",
    # building collapse
    "COLLAPSE": "building_collapse", "BUILDING": "building_collapse",
    "MAKAAN": "building_collapse", "मकान": "building_collapse",
    # stranded
    "STUCK": "stranded", "STRANDED": "stranded", "FANSE": "stranded",
    "फंसे": "stranded", "ଆଟକ": "stranded",
    # others
    "FIRE": "fire", "AAG": "fire", "आग": "fire",
    "CYCLONE": "cyclone_damage", "TOOFAN": "cyclone_damage",
    "POWER": "power_line", "BIJLI": "power_line",
}

PINCODE_RE = re.compile(r"\b(\d{6})\b")


@dataclass
class ParsedSms:
    hazard: str
    severity: int
    lat: float
    lng: float
    accuracy_m: int
    landmark: str
    pincode: str | None
    needs_review: bool
    out_of_district: bool = False

    def as_dict(self) -> dict:
        return {
            "hazard": self.hazard, "severity": self.severity, "pincode": self.pincode,
            "landmark": self.landmark, "accuracy_m": self.accuracy_m,
            "needs_review": self.needs_review,
            "out_of_district": self.out_of_district,
        }


DISTRICT_CENTROID_SQL = text(
    "SELECT ST_Y(centroid::geometry) AS lat, ST_X(centroid::geometry) AS lng "
    "FROM districts WHERE id = :did"
)

# Pincode centroids, looked up across EVERY seeded district.
#
# This used to be scoped to the gateway's own district, and that quietly broke
# the whole SMS fallback the moment the corridor was seeded: an SMS reading
# "FLOOD 4 522101" from Bapatla found no match inside Ganjam, fell back to the
# Ganjam centroid, and was filed to the Ganjam DEOC — 500 km from the sender.
# The message was accepted, the reference code was issued, and the person was
# invisible to the control room that could actually reach them.
#
# One inbound shortcode serves the whole deployment, so the pincode itself has
# to decide which district owns the report. `ingest_report` then files it by
# position, and the right DEOC sees it.
PINCODE_SQL = text(
    """
    SELECT p.district_id, p.name,
           ST_Y(p.geom::geometry) AS lat, ST_X(p.geom::geometry) AS lng
    FROM pincodes p
    WHERE p.pincode = :pin
    ORDER BY (p.district_id = :did) DESC, p.district_id
    LIMIT 1
    """
)

# The district's own pincode prefixes, derived from the seeded rows rather than
# hardcoded. See the geocode endpoint for why: a pincode from another state is
# not a low-precision answer, it is a different district's problem.
# Every prefix the deployment covers, not just one district's. A shortcode is
# national; the corridor is what decides whether we can help.
DISTRICT_PREFIX_SQL = text("SELECT DISTINCT left(pincode, 3) FROM pincodes")


async def parse_sms(session: AsyncSession, message: str, district_id: int) -> ParsedSms:
    tokens = message.strip().split()
    upper = [t.upper() for t in tokens]

    hazard = "other"
    needs_review = True
    hazard_idx = -1
    for i, tok in enumerate(upper):
        if tok in HAZARD_KEYWORDS:
            hazard = HAZARD_KEYWORDS[tok]
            needs_review = False
            hazard_idx = i
            break

    # Severity: the first standalone 1-5 after the hazard keyword. Remember
    # which token it was so only that one is stripped from the landmark — a
    # blanket digit filter would eat the "3" out of "water 3 foot deep", which
    # is the most useful thing in the message.
    severity = DEFAULT_SEVERITY
    severity_idx = -1
    for offset, tok in enumerate(tokens[hazard_idx + 1:], start=hazard_idx + 1):
        if tok.isdigit() and 1 <= int(tok) <= 5:
            severity = int(tok)
            severity_idx = offset
            break

    pin_match = PINCODE_RE.search(message)
    pincode = pin_match.group(1) if pin_match else None

    lat = lng = None
    out_of_district = False
    if pincode:
        try:
            row = (
                await session.execute(PINCODE_SQL, {"pin": pincode, "did": district_id})
            ).mappings().one_or_none()
            if row:
                lat, lng = float(row["lat"]), float(row["lng"])
                # Found, but in a different district than the gateway's default.
                # Not an error — one shortcode covers the whole corridor — so it
                # is recorded rather than flagged for review.
                out_of_district = int(row["district_id"]) != district_id
            else:
                # Unseeded. Is it plausibly ours at all, or another district's?
                # Unlike the PWA, an SMS is never rejected — someone may be in
                # the water — so it is ingested at the district centroid and
                # flagged for the operator queue instead.
                prefixes = {
                    r[0]
                    for r in (await session.execute(DISTRICT_PREFIX_SQL)).all()
                }
                if prefixes and pincode[:3] not in prefixes:
                    # Not a pincode this deployment covers at all. Still
                    # ingested — never drop a message from someone who may be
                    # in the water — but an operator has to look at it.
                    out_of_district = True
                    needs_review = True
        except Exception:
            # No pincode table yet, or an unseeded pincode. Fall through to the
            # district centroid — never reject the message.
            lat = lng = None

    if lat is None:
        row = (
            await session.execute(DISTRICT_CENTROID_SQL, {"did": district_id})
        ).mappings().one()
        lat, lng = float(row["lat"]), float(row["lng"])

    # Whatever is left after the recognised structure is the landmark. Keep it:
    # "near the primary school" is frequently the most useful thing in the
    # message to a team that has never been to this ward.
    consumed = {i for i in (hazard_idx, severity_idx) if i >= 0}
    landmark_tokens = [
        tok for i, tok in enumerate(tokens)
        if i not in consumed and tok != pincode
    ]
    landmark = " ".join(landmark_tokens).strip()

    return ParsedSms(
        hazard=hazard, severity=severity, lat=lat, lng=lng,
        accuracy_m=PINCODE_ACCURACY_M, landmark=landmark or message.strip(),
        pincode=pincode, needs_review=needs_review,
        out_of_district=out_of_district,
    )
