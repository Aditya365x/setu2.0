"""Ingest — the return channel that does not exist today.

Four rungs of the degradation ladder land here (§9.5), and every one of them
produces the same normalised report object. The optimizer never learns which
channel a report arrived on: it sees only location, hazard, severity and
accuracy. That uniformity is the architectural point.

Ingest does the minimum and returns. Clustering, scoring and solving all happen
in the worker, so a burst of reports never blocks on the solver.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..bus import publish, trigger_optimize
from ..config import settings
from ..db import get_session
from ..identity import reference_code, reporter_hash
from ..integrations.sms.gateways import get_gateway
from ..integrations.sms.parser import parse_sms
from ..schemas.report import ReportAccepted
from ..services.scoring import ReportTrustInput, is_quarantined, trust_score
from ..services.storage import store_photo

# One GSM-7 segment. Beyond this the carrier splits the message, bills it
# twice, and on a bad tower delivers half of it.
SMS_SEGMENT = 160

log = logging.getLogger("setu.ingest")

router = APIRouter(prefix="/api/v1/ingest", tags=["ingest"])

INSERT_REPORT_SQL = text(
    """
    INSERT INTO reports (district_id, geom, gps_accuracy_m, hazard_type, severity_raw,
                         description, photo_url, photo_exif_ts, source, reporter_hash,
                         people_reported, trust_score, trust_breakdown, status,
                         reference_code, client_report_uuid, raw_text,
                         has_children, has_elderly, has_injured, has_disabled)
    VALUES (:did, ST_MakePoint(:lng, :lat)::geography, :acc, CAST(:hazard AS hazard),
            :severity, :description, :photo_url, :exif_ts, CAST(:source AS report_source),
            :reporter, :people, :trust, CAST(:trust_parts AS jsonb), :status,
            :ref, :client_uuid, :raw_text,
            :children, :elderly, :injured, :disabled)
    RETURNING id
    """
)

# Independent corroboration within 300 m / 20 min, by distinct reporters.
CORROBORATION_SQL = text(
    """
    SELECT COUNT(DISTINCT reporter_hash) AS n
    FROM reports
    WHERE district_id = :did
      AND hazard_type = CAST(:hazard AS hazard)
      AND created_at > now() - interval '20 minutes'
      AND reporter_hash IS NOT NULL
      AND reporter_hash <> COALESCE(:reporter, '')
      AND ST_DWithin(geom, ST_MakePoint(:lng, :lat)::geography, 300)
    """
)

REPORTER_HISTORY_SQL = text(
    """
    SELECT
      COUNT(*) FILTER (WHERE created_at > now() - interval '10 minutes') AS recent,
      COUNT(*) FILTER (WHERE status = 'false_alarm')                     AS false_alarms,
      COUNT(*) FILTER (WHERE status = 'resolved')                        AS confirmed
    FROM reports WHERE reporter_hash = :reporter
    """
)


async def _score_trust(
    session: AsyncSession, district_id: int, lat: float, lng: float, hazard: str,
    source: str, gps_accuracy_m: Optional[int], photo_url: Optional[str],
    exif_ts: Optional[datetime], rhash: Optional[str],
) -> tuple[float, dict]:
    corroborators = (
        await session.execute(
            CORROBORATION_SQL,
            {"did": district_id, "hazard": hazard, "lat": lat, "lng": lng, "reporter": rhash},
        )
    ).scalar_one()

    recent = false_alarms = confirmed = 0
    if rhash:
        hist = (
            await session.execute(REPORTER_HISTORY_SQL, {"reporter": rhash})
        ).mappings().one()
        recent, false_alarms, confirmed = hist["recent"], hist["false_alarms"], hist["confirmed"]

    fresh = False
    if exif_ts is not None:
        age = (datetime.now(timezone.utc) - exif_ts).total_seconds()
        fresh = 0 <= age <= 1800

    return trust_score(
        ReportTrustInput(
            source=source,
            gps_accuracy_m=gps_accuracy_m,
            has_photo=bool(photo_url),
            photo_exif_within_30min=fresh,
            distinct_corroborators=int(corroborators or 0),
            reporter_false_alarms=int(false_alarms or 0),
            reporter_confirmed_reports=int(confirmed or 0),
            reporter_reports_last_10min=int(recent or 0),
        )
    )


RESOLVE_DISTRICT_SQL = text(
    """
    -- ST_SetSRID is load-bearing. ST_MakePoint alone yields SRID 0, and
    -- ST_Contains refuses to compare that against our 4326 boundary column:
    -- "Operation on mixed SRID geometries". The ::geography casts below happen
    -- to work because a geography cast assumes 4326, which is exactly what
    -- makes the bare-geometry case easy to miss.
    SELECT id, name,
           ST_Contains(boundary::geometry,
                       ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)) AS inside,
           ST_Distance(centroid, ST_MakePoint(:lng, :lat)::geography) AS centroid_m
    FROM districts
    ORDER BY
        -- A containing district always wins. Only when the point is inside
        -- nothing do we fall back to the nearest one.
        ST_Contains(boundary::geometry,
                    ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)) DESC,
        centroid <-> ST_MakePoint(:lng, :lat)::geography
    LIMIT 1
    """
)


async def resolve_district(
    session: AsyncSession, lat: float, lng: float, fallback: int
) -> tuple[int, bool]:
    """Which district is this report actually in?

    Returns (district_id, inside_a_boundary).

    This used to not exist, and with one district seeded it did not need to:
    everything was Ganjam. With the whole east-coast corridor seeded it became a
    real bug — a report submitted from Bapatla was filed under Ganjam because
    the PWA does not send a district and the server default is 1. It then never
    appeared in Bapatla's queue, was matched against Ganjam's units, and came
    back with a 590-minute ETA that looked like a solver failure rather than a
    filing error.

    Geography decides, not configuration. A point inside a boundary belongs to
    that district. A point inside none — offshore, or outside the corridor —
    goes to the nearest district rather than being rejected, because a report
    that cannot be filed is a report that is lost, and the operator can
    reassign. The caller is told which case it was so that can be surfaced.
    """
    row = (
        await session.execute(RESOLVE_DISTRICT_SQL, {"lat": lat, "lng": lng})
    ).mappings().one_or_none()
    if not row:
        return fallback, False
    return int(row["id"]), bool(row["inside"])


async def ingest_report(
    session: AsyncSession, *, district_id: int, lat: float, lng: float, hazard: str,
    severity: int, source: str, description: str | None = None,
    gps_accuracy_m: int | None = None, people_reported: int | None = None,
    phone: str | None = None, photo_url: str | None = None,
    exif_ts: datetime | None = None, client_report_uuid: str | None = None,
    raw_text: str | None = None,
    has_children: bool = False, has_elderly: bool = False,
    has_injured: bool = False, has_disabled: bool = False,
) -> dict:
    """The one path every channel funnels through."""
    # Idempotency. Replaying the PWA outbox after a reconnect must never create
    # a duplicate report — this is what makes the offline queue safe.
    if client_report_uuid:
        existing = (
            await session.execute(
                text(
                    "SELECT id, reference_code FROM reports WHERE client_report_uuid = :u"
                ),
                {"u": client_report_uuid},
            )
        ).mappings().one_or_none()
        if existing:
            return {
                "report_id": str(existing["id"]),
                "reference_code": existing["reference_code"],
                "status": "duplicate_ignored",
            }

    # File the report where it actually is, not where the server is configured.
    district_id, inside = await resolve_district(session, lat, lng, district_id)

    rhash = reporter_hash(phone)
    trust, trust_parts = await _score_trust(
        session, district_id, lat, lng, hazard, source, gps_accuracy_m,
        photo_url, exif_ts, rhash,
    )

    # Low trust is quarantined into a visible operator queue, never dropped.
    # Someone in the water may be typing badly.
    status = "quarantined" if is_quarantined(trust) else "received"
    ref = reference_code()

    report_id = (
        await session.execute(
            INSERT_REPORT_SQL,
            {
                "did": district_id, "lat": lat, "lng": lng, "acc": gps_accuracy_m,
                "hazard": hazard, "severity": severity, "description": description,
                "photo_url": photo_url, "exif_ts": exif_ts, "source": source,
                "reporter": rhash, "people": people_reported, "trust": trust,
                "trust_parts": json.dumps(trust_parts), "status": status,
                "ref": ref, "client_uuid": client_report_uuid, "raw_text": raw_text,
                "children": has_children, "elderly": has_elderly,
                "injured": has_injured, "disabled": has_disabled,
            },
        )
    ).scalar_one()
    await session.commit()

    payload = {
        "id": str(report_id), "lat": lat, "lng": lng, "hazard_type": hazard,
        "severity_raw": severity, "trust_score": trust, "status": status,
        "source": source, "gps_accuracy_m": gps_accuracy_m,
        "reference_code": ref, "photo_url": photo_url,
        "district_id": district_id, "in_district": inside,
    }
    await publish(district_id, "report.created", payload)

    if status != "quarantined":
        await trigger_optimize(district_id, "report")

    return {
        "report_id": str(report_id), "reference_code": ref, "status": status,
        "district_id": district_id,
        # False means the point sits outside every seeded boundary and was filed
        # to the nearest district. Honest signal, not a silent guess.
        "in_district": inside,
    }


@router.post("/report", status_code=202, response_model=ReportAccepted)
async def create_report(
    lat: float = Form(...),
    lng: float = Form(...),
    hazard_type: str = Form(...),
    severity_raw: int = Form(3),
    description: Optional[str] = Form(None),
    gps_accuracy_m: Optional[int] = Form(None),
    people_reported: Optional[int] = Form(None),
    phone: Optional[str] = Form(None),
    client_report_uuid: Optional[str] = Form(None),
    district_id: int = Form(settings.district_id),
    # §6 — who is affected. A rescue team sizes its response differently for
    # children or a stretcher case, and the operator should not have to infer
    # that from free text.
    has_children: bool = Form(False),
    has_elderly: bool = Form(False),
    has_injured: bool = Form(False),
    has_disabled: bool = Form(False),
    photo: Optional[UploadFile] = None,
    session: AsyncSession = Depends(get_session),
) -> ReportAccepted:
    if not 1 <= severity_raw <= 5:
        raise HTTPException(422, "severity_raw must be 1..5")

    photo_url, exif_ts = None, None
    if photo is not None:
        raw = await photo.read()
        if raw:
            photo_url, meta = await store_photo(raw)
            parsed = meta.get("exif_ts")
            if parsed:
                try:
                    exif_ts = datetime.strptime(parsed, "%Y:%m:%d %H:%M:%S").replace(
                        tzinfo=timezone.utc
                    )
                except (ValueError, TypeError):
                    exif_ts = None

    result = await ingest_report(
        session, district_id=district_id, lat=lat, lng=lng, hazard=hazard_type,
        severity=severity_raw, source="app", description=description,
        gps_accuracy_m=gps_accuracy_m, people_reported=people_reported, phone=phone,
        photo_url=photo_url, exif_ts=exif_ts, client_report_uuid=client_report_uuid,
        has_children=has_children, has_elderly=has_elderly,
        has_injured=has_injured, has_disabled=has_disabled,
    )
    return ReportAccepted(**result, queued_offline=False)


NEAREST_SHELTER_SQL = text(
    """
    SELECT s.name,
           round((ST_Distance(s.geom, ST_MakePoint(:lng, :lat)::geography) / 1000)::numeric, 1)
               AS km
    FROM shelters s
    WHERE s.status = 'open' AND s.capacity_total > s.occupancy
    ORDER BY s.geom <-> ST_MakePoint(:lng, :lat)::geography
    LIMIT 1
    """
)


async def _sms_confirmation(session: AsyncSession, result: dict, parsed) -> str:
    """One segment. Reference, what we understood, where to walk.

    Deliberately not "your report has been received, stay safe". That is a
    sentence that costs 40 characters and tells somebody standing in water
    nothing they can act on. §9.4: an instruction, not a reassurance.
    """
    where = parsed.pincode or "your area"
    hazard = parsed.hazard.replace("_", " ").upper()

    shelter_line = ""
    try:
        row = (
            await session.execute(
                NEAREST_SHELTER_SQL, {"lat": parsed.lat, "lng": parsed.lng}
            )
        ).mappings().one_or_none()
        if row:
            shelter_line = f" Shelter: {row['name']} ({row['km']}km)."
    except Exception:
        shelter_line = ""

    # A quarantined report is visible to an operator but is not being
    # auto-dispatched, and saying "help is coming" would be a lie.
    if result.get("status") == "quarantined":
        state = "Under review."
    else:
        state = "Control room notified."

    # Budget: 160 characters, one GSM segment. Anything longer is split and
    # billed twice, and on a congested tower a two-part SMS routinely arrives
    # with one half missing — which is worse than a shorter message.
    #
    # Built in priority order and truncated from the least important end, so
    # what survives is always the reference code and what we understood.
    head = f"SETU: Ref {result['reference_code']}. {hazard} sev {parsed.severity} at {where}."
    tail = " Do not cross flowing water."

    body = head
    if len(body) + len(state) + 1 + len(tail) <= SMS_SEGMENT:
        body += f" {state}"
    if shelter_line and len(body) + len(shelter_line) + len(tail) <= SMS_SEGMENT:
        body += shelter_line
    if len(body) + len(tail) <= SMS_SEGMENT:
        body += tail

    return body[:SMS_SEGMENT]


@router.post("/sms", status_code=202)
async def ingest_sms(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Gateway webhook. Body shape varies by provider, so accept both form and
    JSON and let the adapter normalise."""
    try:
        body = dict(await request.form())
    except Exception:
        body = {}
    if not body:
        body = await request.json()

    sender = str(body.get("From") or body.get("from") or "")
    message = str(body.get("Body") or body.get("body") or "")
    if not message:
        raise HTTPException(422, "empty message")

    parsed = await parse_sms(session, message, settings.district_id)

    result = await ingest_report(
        session,
        district_id=settings.district_id,
        lat=parsed.lat, lng=parsed.lng, hazard=parsed.hazard,
        severity=parsed.severity, source="sms",
        description=parsed.landmark, gps_accuracy_m=parsed.accuracy_m,
        phone=sender or None, raw_text=message,
    )
    result["parsed"] = parsed.as_dict()

    # ── reply ─────────────────────────────────────────────────────────────
    # Without this the SMS channel was write-only: the report was filed
    # correctly and the sender heard nothing at all. For somebody with no data
    # connection that is indistinguishable from the message never arriving, and
    # they will send it again, and again — which is both a flood of duplicates
    # and a person who does not know whether anyone is coming.
    #
    # The reply carries three things and nothing else, because it has to fit in
    # one 160-character segment on a bad network:
    #   1. the reference code — the only handle they have on their own report
    #   2. what we UNDERSTOOD — so a misparse is visible and correctable
    #   3. the nearest shelter — the one instruction that is actionable now
    if sender:
        try:
            reply = await _sms_confirmation(session, result, parsed)
            await get_gateway().send(sender, reply)
            result["reply"] = reply
        except Exception:
            # A failed confirmation must never fail the report. The person is
            # already in the system; losing the receipt is bad, losing the
            # report is unacceptable.
            log.exception("could not send SMS confirmation")

    return result


@router.get("/sms/outbox")
async def sms_outbox(limit: int = 20) -> list[dict]:
    """What SETU has texted back.

    With SMS_PROVIDER=mock — the offline default — outbound messages go to an
    in-memory log instead of a telecom gateway. This exposes that log so the
    whole SMS round trip is demonstrable with no Twilio account, no SIM and no
    internet: send a message to /ingest/sms, read the reply here.

    Swap SMS_PROVIDER to twilio or exotel and the identical code path puts the
    same text on a real handset; only the adapter changes.
    """
    gateway = get_gateway()
    outbox = getattr(gateway, "outbox", None)
    if outbox is None:
        return []
    return list(reversed(outbox[-limit:]))


@router.post("/ivr", status_code=202)
async def ingest_ivr(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """DTMF callback. The tree collects hazard, severity and people count one
    key at a time; this is the final step, once all three are known."""
    body = await request.json()
    hazard = body.get("hazard", "other")
    severity = int(body.get("severity", 3))
    people = body.get("people")
    lat = float(body.get("lat", 19.3149))
    lng = float(body.get("lng", 84.7941))

    return await ingest_report(
        session, district_id=settings.district_id, lat=lat, lng=lng, hazard=hazard,
        severity=severity, source="ivr", people_reported=people,
        # Cell-tower location, not GPS. Stored honestly so the operator sees an
        # uncertainty circle rather than a false-precision pin.
        gps_accuracy_m=int(body.get("accuracy_m", 3000)),
        phone=body.get("From") or body.get("caller"),
    )
