"""§35 — IVR webhook endpoints.

Each step returns TwiML and names the next step as its `action`, so the tree is
driven entirely by the telephony provider walking our own URLs. State lives in
the query string (see integrations/ivr.py for why), which means these handlers
are stateless and a restart mid-call loses nothing but the current prompt.

The last step calls the same `ingest_report()` every other channel uses, so an
IVR call becomes an ordinary report the moment it lands. The optimizer never
learns it came from a phone tree.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session
from ..integrations.ivr import (
    finish_twiml,
    hazard_twiml,
    people_twiml,
    severity_twiml,
    welcome_twiml,
)
from .ingest import ingest_report

router = APIRouter(prefix="/api/v1/ivr", tags=["ivr"])

XML = "application/xml"


def _xml(body: str) -> Response:
    return Response(content=body, media_type=XML)


async def _digits(request: Request) -> str:
    """Providers post `Digits` as form data; the mock simulator sends JSON.
    Accept either rather than coupling the tree to one vendor."""
    try:
        form = await request.form()
        if "Digits" in form:
            return str(form["Digits"]).strip()
    except Exception:
        pass
    try:
        body = await request.json()
        return str(body.get("Digits", "")).strip()
    except Exception:
        return ""


@router.api_route("/start", methods=["GET", "POST"])
async def ivr_start(lang: str = "en") -> Response:
    """Inbound call lands here. Point the number's voice webhook at this."""
    return _xml(welcome_twiml(lang=lang))


@router.api_route("/hazard", methods=["GET", "POST"])
async def ivr_hazard(request: Request, lang: str = "en") -> Response:
    return _xml(hazard_twiml(await _digits(request), lang=lang))


@router.api_route("/people", methods=["GET", "POST"])
async def ivr_people(request: Request, hazard: str = "other", lang: str = "en") -> Response:
    return _xml(people_twiml(hazard, await _digits(request), lang=lang))


@router.api_route("/severity", methods=["GET", "POST"])
async def ivr_severity(
    request: Request, hazard: str = "other", people: str = "", lang: str = "en"
) -> Response:
    return _xml(severity_twiml(hazard, people, await _digits(request), lang=lang))


@router.api_route("/finish", methods=["GET", "POST"])
async def ivr_finish(
    request: Request,
    hazard: str = "other",
    people: str = "",
    severity: int = 3,
    lang: str = "en",
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Terminal step: resolve location, create the report, read the code back."""
    pincode = await _digits(request)
    district_id = settings.district_id

    lat = lng = None
    accuracy = 25000
    if pincode.isdigit() and len(pincode) == 6:
        row = (
            await session.execute(
                text(
                    "SELECT ST_Y(geom::geometry) AS lat, ST_X(geom::geometry) AS lng "
                    "FROM pincodes WHERE pincode = :pin AND district_id = :did"
                ),
                {"pin": pincode, "did": district_id},
            )
        ).mappings().one_or_none()
        if row:
            lat, lng, accuracy = float(row["lat"]), float(row["lng"]), 3000

    if lat is None:
        # No usable pincode. The district centroid at an honest 25 km beats
        # refusing the call — an operator can still ring back on the caller ID.
        row = (
            await session.execute(
                text(
                    "SELECT ST_Y(centroid::geometry) AS lat, "
                    "ST_X(centroid::geometry) AS lng FROM districts WHERE id = :did"
                ),
                {"did": district_id},
            )
        ).mappings().one_or_none()
        if not row:
            return _xml(finish_twiml(None, lang=lang))
        lat, lng = float(row["lat"]), float(row["lng"])

    caller: Optional[str] = None
    try:
        form = await request.form()
        caller = form.get("From") or form.get("Caller")
    except Exception:
        caller = None

    try:
        result = await ingest_report(
            session,
            district_id=district_id,
            lat=lat, lng=lng, hazard=hazard,
            severity=int(severity), source="ivr",
            description=f"IVR call · {hazard} · {people or 'unknown'} people",
            gps_accuracy_m=accuracy,
            people_reported=int(people) if people.isdigit() else None,
            phone=str(caller) if caller else None,
            raw_text=f"ivr hazard={hazard} people={people} severity={severity} pin={pincode}",
        )
    except Exception:
        # Never leave a caller with dead air. They hear "call again or SMS",
        # which is actionable, instead of a disconnect they cannot interpret.
        return _xml(finish_twiml(None, lang=lang))

    return _xml(finish_twiml(result.get("reference_code"), lang=lang))
