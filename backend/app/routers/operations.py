"""Read models and the operator decision path (§7.1).

Everything here serves one screen. The dashboard has no tabs and no modal
stack, so these endpoints are shaped to fill panels directly rather than to be
composed client-side.
"""

import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..bus import publish, trigger_optimize
from ..config import settings
from ..db import get_session
from ..schemas.report import Metrics
from ..services.conditions import all_district_conditions, district_conditions
from ..services.supplies import FOOD_KG_PER_PERSON_DAY, WATER_L_PER_PERSON_DAY

router = APIRouter(prefix="/api/v1", tags=["operations"])

CRITICAL_THRESHOLD = 70.0


async def _audit(
    session: AsyncSession, district_id: int, actor: str, action: str,
    entity_type: str, entity_id: Any, before: dict | None, after: dict | None,
    reason: str | None = None,
) -> None:
    """Append-only. This log is what turns a demo into a compensation and
    claims record afterwards — and it is non-repudiable by construction."""
    await session.execute(
        text(
            """
            INSERT INTO audit_log (district_id, actor, action, entity_type, entity_id,
                                   before, after, reason)
            VALUES (:did, :actor, :action, :etype, :eid,
                    CAST(:before AS jsonb), CAST(:after AS jsonb), :reason)
            """
        ),
        {
            "did": district_id, "actor": actor, "action": action,
            "etype": entity_type, "eid": str(entity_id),
            "before": json.dumps(before) if before else None,
            "after": json.dumps(after) if after else None,
            "reason": reason,
        },
    )


# ── district ───────────────────────────────────────────────────────────────
@router.get("/districts")
async def list_districts(session: AsyncSession = Depends(get_session)) -> list[dict]:
    """Every seeded district, with a live count of what is happening in it.

    Drives the dashboard's district picker. The counts matter as much as the
    names: an operator switching districts wants to know where the load is, not
    just that sixteen districts exist.
    """
    rows = (
        await session.execute(
            text(
                """
                SELECT d.id, d.name, d.state, d.lgd_code,
                       ST_Y(d.centroid::geometry) AS lat,
                       ST_X(d.centroid::geometry) AS lng,
                       (SELECT COUNT(*) FROM incidents i
                         WHERE i.district_id = d.id
                           AND i.status IN ('open','assigned')) AS open_incidents,
                       (SELECT COUNT(*) FROM incidents i
                         WHERE i.district_id = d.id
                           AND i.status IN ('open','assigned')
                           AND i.severity_score >= 70) AS critical,
                       (SELECT COUNT(*) FROM resources r
                         WHERE r.district_id = d.id
                           AND r.status IN ('idle','returning')) AS units_free,
                       (SELECT COUNT(*) FROM shelters s
                         WHERE s.district_id = d.id AND s.status = 'open') AS shelters_open
                FROM districts d
                ORDER BY d.state, d.name
                """
            )
        )
    ).mappings().all()
    return [dict(r) for r in rows]


@router.get("/district")
async def district(
    district_id: int = Query(default=settings.district_id),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Boundary and centroid — the map needs both before it can frame itself."""
    row = (
        await session.execute(
            text(
                """
                SELECT id, name, state, lgd_code,
                       ST_AsGeoJSON(boundary::geometry) AS boundary_geojson,
                       ST_Y(centroid::geometry) AS lat, ST_X(centroid::geometry) AS lng
                FROM districts WHERE id = :did
                """
            ),
            {"did": district_id},
        )
    ).mappings().one_or_none()
    if not row:
        raise HTTPException(404, "district not seeded — run `make seed`")
    return {**dict(row), "boundary_geojson": json.loads(row["boundary_geojson"])}


# ── geocoding fallback ─────────────────────────────────────────────────────
@router.get("/geocode/search")
async def geocode_search(
    q: str,
    limit: int = 8,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Type where you are.

    The third way into a location, after GPS and a PIN code, and the one that
    works when the other two do not: no satellite fix, and no idea what the
    local PIN code is. Somebody can still type the name of their village, or of
    the school they are sheltering behind.

    Searched against our OWN gazetteer — block headquarters and the shelter
    register — not an external geocoder. Two reasons, and both are load-bearing:
    Nominatim needs the internet, which is the one thing this product assumes it
    does not have; and it would cheerfully return a match 2,000 km outside the
    covered corridor, which is worse than no answer because it looks like one.

    Ranked so that exact and prefix matches beat fuzzy ones, with trigram
    similarity underneath to absorb the spelling of somebody typing one-handed
    in the rain. `accuracy_m` travels with the answer: a named building is a
    point, a block name stands for the whole block, and the report should say
    which rather than implying precision it does not have.
    """
    term = q.strip()
    if len(term) < 2:
        return []

    rows = (
        await session.execute(
            text(
                """
                SELECT p.id, p.name, p.kind, p.accuracy_m,
                       d.id AS district_id, d.name AS district_name, d.state,
                       ST_Y(p.geom::geometry) AS lat,
                       ST_X(p.geom::geometry) AS lng,
                       similarity(p.name, :q) AS sim
                FROM places p JOIN districts d ON d.id = p.district_id
                WHERE p.name ILIKE :contains
                   OR similarity(p.name, :q) > 0.25
                ORDER BY
                    -- exact, then starts-with, then anywhere, then fuzzy
                    (lower(p.name) = lower(:q)) DESC,
                    (p.name ILIKE :prefix) DESC,
                    (p.name ILIKE :contains) DESC,
                    similarity(p.name, :q) DESC,
                    -- a town beats a building of the same score: somebody
                    -- typing "Chikiti" means the place, not a school in it
                    (p.kind = 'town') DESC,
                    p.name
                LIMIT :lim
                """
            ),
            {"q": term, "prefix": f"{term}%", "contains": f"%{term}%", "lim": limit},
        )
    ).mappings().all()

    return [
        {
            "name": r["name"],
            "kind": r["kind"],
            "lat": r["lat"],
            "lng": r["lng"],
            "accuracy_m": r["accuracy_m"],
            "district_id": r["district_id"],
            "district_name": r["district_name"],
            "state": r["state"],
            # What the citizen actually reads in the results list.
            "label": f"{r['name']} · {r['district_name']}, {r['state']}",
        }
        for r in rows
    ]



@router.get("/geocode/pincode/{pincode}")
async def geocode_pincode(
    pincode: str,
    district_id: int = Query(default=settings.district_id),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Resolve a pincode to a centroid, for reporters with no usable GPS.

    Same path the SMS channel uses (§9.2), and the same honesty about
    precision: ~2-5 km, stored as gps_accuracy_m = 3000 so trust drops and the
    accuracy travels with the report rather than being rounded away.

    Three outcomes, and the third one matters. SETU is deployed per district,
    so a pincode from another state is not a low-precision answer — it is a
    question this deployment cannot answer. Silently returning the Ganjam
    centroid for a Bihar pincode puts a pin 800 km from the person who typed
    it, which is worse than saying no.
    """
    pin = pincode.strip()
    if not (len(pin) == 6 and pin.isdigit()):
        raise HTTPException(422, "pincode must be six digits")

    row = (
        await session.execute(
            text(
                """
                SELECT name, ST_Y(geom::geometry) AS lat, ST_X(geom::geometry) AS lng
                FROM pincodes WHERE pincode = :pin AND district_id = :did
                """
            ),
            {"pin": pin, "did": district_id},
        )
    ).mappings().one_or_none()

    if row:
        return {"lat": row["lat"], "lng": row["lng"], "name": row["name"],
                "accuracy_m": 3000, "source": "pincode", "in_district": True}

    centroid = (
        await session.execute(
            text(
                "SELECT name, ST_Y(centroid::geometry) AS lat, "
                "ST_X(centroid::geometry) AS lng FROM districts WHERE id = :did"
            ),
            {"did": district_id},
        )
    ).mappings().one_or_none()
    if not centroid:
        raise HTTPException(404, "district not seeded")

    # Before rejecting: does this pincode belong to a DIFFERENT seeded district?
    #
    # With the whole coastal corridor seeded, "not Ganjam" is usually not the
    # same as "not covered". Somebody in Puri typing 752001 into a Ganjam-framed
    # app should be routed to Puri, not turned away — the deployment does cover
    # them, just under a different district_id. Rejecting them here would be the
    # out-of-district bug all over again, one level up.
    neighbour = (
        await session.execute(
            text(
                """
                SELECT p.district_id, p.name, d.name AS district_name,
                       ST_Y(p.geom::geometry) AS lat, ST_X(p.geom::geometry) AS lng
                FROM pincodes p JOIN districts d ON d.id = p.district_id
                WHERE p.pincode = :pin
                ORDER BY p.district_id
                LIMIT 1
                """
            ),
            {"pin": pin},
        )
    ).mappings().one_or_none()

    if neighbour:
        return {
            "lat": neighbour["lat"], "lng": neighbour["lng"],
            "name": neighbour["name"],
            "accuracy_m": 3000, "source": "pincode", "in_district": True,
            # The caller asked about one district and got an answer from
            # another. Say so rather than letting a report land under a
            # district_id the reporter never chose.
            "district_id": neighbour["district_id"],
            "district_name": neighbour["district_name"],
            "redirected": neighbour["district_id"] != district_id,
        }

    # Which pincodes plausibly belong to this deployment, derived from the
    # seeded table rather than hardcoded. India Post allocates contiguous
    # prefixes per sorting district, so the first three digits of the seeded
    # rows are the covered prefixes — 760/761 Ganjam, 752 Puri, 754
    # Jagatsinghpur/Kendrapara, 756 Bhadrak/Balasore. Swapping in the full India
    # Post file widens this automatically; nothing here needs editing.
    covered = (
        await session.execute(
            text(
                """
                SELECT DISTINCT left(p.pincode, 3) AS prefix, p.district_id,
                       d.name AS district_name
                FROM pincodes p JOIN districts d ON d.id = p.district_id
                """
            )
        )
    ).mappings().all()
    prefixes = {c["prefix"] for c in covered}

    # An unseeded deployment has no prefixes to compare against. Degrade to the
    # permissive behaviour rather than rejecting every pincode.
    if prefixes and pin[:3] not in prefixes:
        covered_names = sorted({c["district_name"] for c in covered})
        raise HTTPException(
            404,
            {
                "error": "pincode_out_of_district",
                "pincode": pin,
                "district": centroid["name"],
                "covered_districts": covered_names,
                "message": (
                    f"PIN {pin} is not in any district this SETU deployment "
                    f"covers. Covered: {', '.join(covered_names)}."
                ),
            },
        )

    # In-corridor prefix but not an individually seeded pincode. Attribute it to
    # the district that owns that prefix rather than to whichever district the
    # caller happened to ask about.
    owner = next((c for c in covered if c["prefix"] == pin[:3]), None)
    if owner and owner["district_id"] != district_id:
        home = (
            await session.execute(
                text(
                    "SELECT name, ST_Y(centroid::geometry) AS lat, "
                    "ST_X(centroid::geometry) AS lng FROM districts WHERE id = :did"
                ),
                {"did": owner["district_id"]},
            )
        ).mappings().one_or_none()
        if home:
            return {
                "lat": home["lat"], "lng": home["lng"],
                "name": f"{home['name']} (approximate)",
                "accuracy_m": 25000, "source": "district_centroid",
                "in_district": True,
                "district_id": owner["district_id"],
                "district_name": home["name"],
                "redirected": True,
            }

    # In-district but not individually seeded. The district centroid with its
    # true 25 km accuracy stated is far more useful than no report at all.
    return {"lat": centroid["lat"], "lng": centroid["lng"],
            "name": f"{centroid['name']} (approximate)",
            "accuracy_m": 25000, "source": "district_centroid", "in_district": True}


# ── live conditions ────────────────────────────────────────────────────────
@router.get("/conditions")
async def all_conditions(session: AsyncSession = Depends(get_session)) -> list[dict]:
    """Every district's hazard outlook, worst first. Drives the live feed."""
    return await all_district_conditions(session)


@router.get("/conditions/{district_id}")
async def one_condition(
    district_id: int, session: AsyncSession = Depends(get_session)
) -> dict:
    """One district: weather now, official alerts, what is actually happening,
    and a per-hazard likelihood with its reasons."""
    return await district_conditions(session, district_id)


# ── incidents ──────────────────────────────────────────────────────────────
@router.get("/incidents")
async def list_incidents(
    status: Optional[str] = None,
    hazard: Optional[str] = None,
    min_severity: float = 0.0,
    district_id: int = Query(default=settings.district_id),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    rows = (
        await session.execute(
            text(
                """
                SELECT i.id, i.hazard_type::text AS hazard_type, i.status,
                       i.severity_score::float AS severity_score, i.severity_breakdown,
                       i.report_count, i.people_affected_est, i.needs_medical,
                       i.has_children, i.has_elderly, i.has_injured, i.has_disabled,
                       i.sla_deadline, i.opened_at,
                       -- The citizen-facing reference codes that make up this
                       -- incident. An incident is a CLUSTER of reports, so it
                       -- carries several — the operator needs to be able to
                       -- match any of them to a caller on the phone. Ordered by
                       -- arrival so the first is the one that opened it.
                       COALESCE(
                           (SELECT array_agg(r.reference_code ORDER BY r.created_at)
                              FROM reports r
                             WHERE r.incident_id = i.id
                               AND r.reference_code IS NOT NULL),
                           ARRAY[]::text[]
                       ) AS reference_codes,
                       ST_Y(i.centroid::geometry) AS lat, ST_X(i.centroid::geometry) AS lng
                FROM incidents i
                WHERE i.district_id = :did
                  AND (CAST(:status AS text) IS NULL OR i.status = CAST(:status AS text))
                  AND (CAST(:hazard AS text) IS NULL OR i.hazard_type::text = CAST(:hazard AS text))
                  AND i.severity_score >= :min_sev
                ORDER BY i.severity_score DESC
                """
            ),
            {"did": district_id, "status": status, "hazard": hazard, "min_sev": min_severity},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


@router.get("/incidents/{incident_id}")
async def incident_detail(
    incident_id: int, session: AsyncSession = Depends(get_session)
) -> dict:
    inc = (
        await session.execute(
            text(
                """
                SELECT id, district_id, hazard_type::text AS hazard_type, status,
                       severity_score::float AS severity_score, severity_breakdown,
                       report_count, people_affected_est, needs_medical, sla_deadline,
                       has_children, has_elderly, has_injured, has_disabled,
                       COALESCE(water_delivered_l, 0) AS water_delivered_l,
                       COALESCE(food_delivered_kg, 0) AS food_delivered_kg,
                       opened_at, ST_Y(centroid::geometry) AS lat,
                       ST_X(centroid::geometry) AS lng
                FROM incidents WHERE id = :id
                """
            ),
            {"id": incident_id},
        )
    ).mappings().one_or_none()
    if not inc:
        raise HTTPException(404, "incident not found")

    reports = (
        await session.execute(
            text(
                """
                SELECT id, source::text AS source, severity_raw, description, photo_url,
                       trust_score::float AS trust_score, trust_breakdown,
                       gps_accuracy_m, reference_code, created_at,
                       has_children, has_elderly, has_injured, has_disabled,
                       last_location_update
                FROM reports WHERE incident_id = :id ORDER BY created_at
                """
            ),
            {"id": incident_id},
        )
    ).mappings().all()

    assignment = (
        await session.execute(
            text(
                """
                SELECT a.id, a.resource_id, r.name AS resource_name, a.eta_seconds,
                       a.status, a.strategy
                FROM assignments a
                LEFT JOIN resources r ON r.id = a.resource_id
                WHERE a.incident_id = :id AND a.kind = 'dispatch'
                  AND a.status IN ('proposed','committed')
                  AND (a.strategy = 'optimized' OR a.strategy IS NULL)
                ORDER BY a.status = 'committed' DESC, a.created_at DESC
                LIMIT 1
                """
            ),
            {"id": incident_id},
        )
    ).mappings().one_or_none()

    supply = (
        await session.execute(
            text(
                """
                SELECT a.id, a.status, a.resource_id, r.name AS resource_name,
                       a.water_l, a.food_kg, a.eta_seconds,
                       r.stock_water_l, r.stock_food_kg
                FROM assignments a JOIN resources r ON r.id = a.resource_id
                WHERE a.incident_id = :id AND a.kind = 'supply'
                  AND a.status IN ('proposed','committed')
                ORDER BY a.eta_seconds
                """
            ),
            {"id": incident_id},
        )
    ).mappings().all()

    evacuation = (
        await session.execute(
            text(
                """
                SELECT a.id, a.status, a.shelter_id, s.name AS shelter_name,
                       a.people, a.eta_seconds,
                       s.occupancy, s.capacity_total,
                       GREATEST(s.capacity_total - s.occupancy, 0) AS beds_free
                FROM assignments a JOIN shelters s ON s.id = a.shelter_id
                WHERE a.incident_id = :id AND a.kind = 'evacuation'
                  AND a.status IN ('proposed','committed')
                ORDER BY a.eta_seconds
                """
            ),
            {"id": incident_id},
        )
    ).mappings().all()

    return {
        **dict(inc),
        "reports": [dict(r) for r in reports],
        "assignment": dict(assignment) if assignment else None,
        "evacuation_plan": [dict(e) for e in evacuation],
        "supply_plan": [dict(x) for x in supply],
        # What this incident still needs at Sphere standards, net of what has
        # already arrived. Shown next to the plan so an operator can see whether
        # the plan actually closes the gap.
        "supply_need": {
            "water_l": max(
                0,
                int((inc["people_affected_est"] or 0) * WATER_L_PER_PERSON_DAY)
                - int(inc["water_delivered_l"] or 0),
            ),
            "food_kg": max(
                0,
                int((inc["people_affected_est"] or 0) * FOOD_KG_PER_PERSON_DAY)
                - int(inc["food_delivered_kg"] or 0),
            ),
            "water_delivered_l": int(inc["water_delivered_l"] or 0),
            "food_delivered_kg": int(inc["food_delivered_kg"] or 0),
        },
    }


@router.patch("/incidents/{incident_id}")
async def update_incident(
    incident_id: int, body: dict, session: AsyncSession = Depends(get_session)
) -> dict:
    """Operator override. Always audited — the override and its reason feed the
    after-action report."""
    before = (
        await session.execute(
            text("SELECT status, severity_score::float AS severity_score, "
                 "people_affected_est, district_id FROM incidents WHERE id = :id"),
            {"id": incident_id},
        )
    ).mappings().one_or_none()
    if not before:
        raise HTTPException(404, "incident not found")

    fields, params = [], {"id": incident_id}
    for key in ("status", "severity_score", "people_affected_est"):
        if key in body:
            fields.append(f"{key} = :{key}")
            params[key] = body[key]
    if body.get("status") == "resolved":
        fields.append("resolved_at = now()")
    if not fields:
        raise HTTPException(422, "nothing to update")

    await session.execute(
        text(f"UPDATE incidents SET {', '.join(fields)} WHERE id = :id"), params
    )

    # Resolving frees the unit, which re-enters the solver pool immediately.
    if body.get("status") in ("resolved", "false_alarm"):
        await session.execute(
            text(
                "UPDATE resources SET status = 'returning', committed_incident_id = NULL "
                "WHERE committed_incident_id = :id"
            ),
            {"id": incident_id},
        )

    await _audit(
        session, before["district_id"], body.get("actor", "operator"), "override_incident",
        "incident", incident_id, dict(before), body, body.get("reason"),
    )
    await session.commit()
    await publish(before["district_id"], "incident.updated", {"id": incident_id, **body})
    await trigger_optimize(before["district_id"], "incident_updated")
    return {"ok": True}


# ── reports ────────────────────────────────────────────────────────────────
@router.get("/reports")
async def list_reports(
    limit: int = Query(100, le=500),
    offset: int = 0,
    source: Optional[str] = None,
    status: Optional[str] = None,
    district_id: int = Query(default=settings.district_id),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """The raw inbound stream, newest first.

    Incidents are what the DEOC acts on, but an operator sometimes needs to see
    what actually arrived — which channel, how precise, how trusted, and whether
    it was folded into an incident or held back.
    """
    total = (
        await session.execute(
            text("SELECT COUNT(*) FROM reports WHERE district_id = :did"),
            {"did": district_id},
        )
    ).scalar_one()

    rows = (
        await session.execute(
            text(
                """
                SELECT r.id, r.reference_code, r.hazard_type::text AS hazard_type,
                       r.severity_raw, r.description, r.raw_text, r.photo_url,
                       r.source::text AS source, r.status, r.gps_accuracy_m,
                       r.trust_score::float AS trust_score, r.people_reported,
                       r.incident_id, r.created_at,
                       ST_Y(r.geom::geometry) AS lat, ST_X(r.geom::geometry) AS lng,
                       i.severity_score::float AS incident_severity
                FROM reports r
                LEFT JOIN incidents i ON i.id = r.incident_id
                WHERE r.district_id = :did
                  -- Cast optional filters to text explicitly. A bare
                  -- "IS NULL" test on an enum-typed bind leaves Postgres unable
                  -- to infer the parameter type, and it refuses the statement.
                  -- (Also: never write a colon-prefixed word in a text() SQL
                  -- comment — SQLAlchemy parses it as a real bind parameter.)
                  AND (CAST(:source AS text) IS NULL OR r.source::text = CAST(:source AS text))
                  AND (CAST(:status AS text) IS NULL OR r.status = CAST(:status AS text))
                ORDER BY r.created_at DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {"did": district_id, "source": source, "status": status,
             "limit": limit, "offset": offset},
        )
    ).mappings().all()

    return {"total": total, "count": len(rows), "reports": [dict(r) for r in rows]}


@router.get("/reports/{reference_code}")
async def report_status(
    reference_code: str, session: AsyncSession = Depends(get_session)
) -> dict:
    """Citizen-facing status by reference code.

    The PWA and the SMS auto-reply both hand out a short code; this is what
    makes it mean something. "Know they were heard" is a stated need, and a
    reference number that resolves to nothing is worse than none at all.
    """
    row = (
        await session.execute(
            text(
                """
                SELECT r.reference_code, r.hazard_type::text AS hazard_type,
                       r.status AS report_status, r.created_at,
                       i.id AS incident_id, i.status AS incident_status,
                       res.name AS resource_name, a.status AS assignment_status,
                       a.eta_seconds
                FROM reports r
                LEFT JOIN incidents i ON i.id = r.incident_id
                LEFT JOIN assignments a ON a.incident_id = i.id
                     AND a.kind = 'dispatch' AND a.status = 'committed'
                LEFT JOIN resources res ON res.id = a.resource_id
                WHERE UPPER(r.reference_code) = UPPER(:ref)
                LIMIT 1
                """
            ),
            {"ref": reference_code},
        )
    ).mappings().one_or_none()

    if not row:
        raise HTTPException(404, "No report with that reference code")

    # A ladder the citizen can actually follow, rather than internal state names.
    if row["assignment_status"] == "committed":
        stage, detail = "team_assigned", f"{row['resource_name']} is on the way"
    elif row["incident_status"] == "resolved":
        stage, detail = "resolved", "This incident has been closed"
    elif row["report_status"] == "quarantined":
        stage, detail = "under_review", "An operator is reviewing your report"
    elif row["incident_id"]:
        stage, detail = "verified", "Your report has been confirmed and is being triaged"
    else:
        stage, detail = "received", "Your report has reached the control room"

    # §11 — the whole ladder, not just the current rung. Someone waiting for a
    # boat wants to see what has already happened and what comes next; a bare
    # status string tells them neither. Rendered as a timeline in the PWA.
    ladder = [
        ("received", "Report submitted"),
        ("verified", "Control room notified"),
        ("team_assigned", "Rescue team assigned"),
        ("en_route", "Team en route"),
        ("resolved", "Rescue completed"),
    ]
    reached = "en_route" if row["assignment_status"] == "committed" else stage
    if row["incident_status"] == "resolved":
        reached = "resolved"
    order = [k for k, _ in ladder]
    # `under_review` sits off the ladder deliberately: a quarantined report is
    # not progressing, and drawing it as step two would imply that it is.
    reached_idx = order.index(reached) if reached in order else 0

    return {
        "reference_code": row["reference_code"],
        "hazard_type": row["hazard_type"],
        "stage": stage,
        "detail": detail,
        "eta_minutes": round(row["eta_seconds"] / 60) if row["eta_seconds"] else None,
        "created_at": row["created_at"],
        "under_review": stage == "under_review",
        "timeline": [
            {"key": k, "label": lbl, "done": i <= reached_idx, "current": i == reached_idx}
            for i, (k, lbl) in enumerate(ladder)
        ],
    }


@router.get("/lookup/{reference_code}")
async def operator_lookup(
    reference_code: str, session: AsyncSession = Depends(get_session)
) -> dict:
    """Resolve a citizen reference code to everything the operator needs.

    This closes the accountability loop. The citizen is handed a four-character
    code at submission; that code is the only handle they have on their own
    report, and until now it existed on their phone and nowhere an operator
    could search. Somebody phoning the control room to say "I sent report 8KPN,
    has anyone come?" could not be answered.

    The code is the citizen-facing primary key. It resolves to exactly one
    report, which resolves to the incident it was clustered into, which resolves
    to the unit assigned and its ETA — so the question is answerable in one
    lookup, by a person, out loud, over a radio.
    """
    row = (
        await session.execute(
            text(
                """
                SELECT r.id AS report_id, r.reference_code, r.hazard_type::text AS hazard_type,
                       r.severity_raw, r.description, r.photo_url, r.status AS report_status,
                       r.source::text AS source, r.people_reported, r.gps_accuracy_m,
                       r.trust_score::float AS trust_score, r.created_at,
                       r.last_location_update,
                       r.has_children, r.has_elderly, r.has_injured, r.has_disabled,
                       ST_Y(r.geom::geometry) AS lat, ST_X(r.geom::geometry) AS lng,
                       i.id AS incident_id, i.status AS incident_status,
                       i.severity_score::float AS severity_score,
                       i.report_count, i.people_affected_est,
                       ST_Y(i.centroid::geometry) AS incident_lat,
                       ST_X(i.centroid::geometry) AS incident_lng,
                       -- Is this report inside the district we can actually
                       -- dispatch into? A report 300 km away is accepted and
                       -- queued but no unit will ever reach it, and an operator
                       -- must be able to see that rather than infer it.
                       ST_Contains(d.boundary::geometry, r.geom::geometry) AS in_district
                FROM reports r
                LEFT JOIN incidents i ON i.id = r.incident_id
                LEFT JOIN districts d ON d.id = r.district_id
                WHERE UPPER(r.reference_code) = UPPER(:ref)
                LIMIT 1
                """
            ),
            {"ref": reference_code.strip()},
        )
    ).mappings().one_or_none()

    if not row:
        raise HTTPException(404, f"No report with reference {reference_code.upper()}")

    result = dict(row)

    if row["incident_id"]:
        assignment = (
            await session.execute(
                text(
                    """
                    SELECT a.id, a.status, a.strategy,
                           a.eta_seconds, a.committed_at,
                           res.name AS resource_name, res.status AS resource_status,
                           res.contact AS resource_contact
                    FROM assignments a
                    LEFT JOIN resources res ON res.id = a.resource_id
                    WHERE a.incident_id = :iid AND a.kind = 'dispatch'
                      AND (a.status = 'committed'
                           OR (a.status = 'proposed' AND a.strategy = 'optimized'))
                    ORDER BY CASE a.status WHEN 'committed' THEN 0 ELSE 1 END
                    LIMIT 1
                    """
                ),
                {"iid": row["incident_id"]},
            )
        ).mappings().one_or_none()
        result["assignment"] = dict(assignment) if assignment else None
    else:
        result["assignment"] = None

    return result


@router.patch("/reports/{reference_code}/location")
async def update_report_location(
    reference_code: str, body: dict, session: AsyncSession = Depends(get_session)
) -> dict:
    """§21 — the reporter moved.

    People move during an evacuation: onto a roof, to a neighbour's upper
    floor, toward a road. A pin that silently goes stale sends a boat to where
    somebody used to be, so a correction has to be cheap and has to be visible.

    The revised position replaces `geom` and stamps `last_location_update`, so
    an operator can tell a corrected pin from one nobody has touched in an
    hour. Re-triggers optimisation, because the ETA matrix just changed.
    """
    try:
        lat, lng = float(body["lat"]), float(body["lng"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(422, "lat and lng are required and must be numeric")
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise HTTPException(422, "lat/lng out of range")

    row = (
        await session.execute(
            text(
                """
                UPDATE reports
                   SET geom = ST_MakePoint(:lng, :lat)::geography,
                       gps_accuracy_m = COALESCE(:acc, gps_accuracy_m),
                       last_location_update = now()
                 WHERE UPPER(reference_code) = UPPER(:ref)
                RETURNING id, district_id, incident_id
                """
            ),
            {"ref": reference_code, "lat": lat, "lng": lng, "acc": body.get("accuracy_m")},
        )
    ).mappings().one_or_none()
    if not row:
        raise HTTPException(404, "No report with that reference code")

    # The centroid is the mean of the incident's reports, so it is recomputed
    # rather than nudged: one report moving 200 m does not move the centroid
    # 200 m.
    if row["incident_id"]:
        await session.execute(
            text(
                """
                UPDATE incidents i
                   SET centroid = sub.c
                  FROM (SELECT ST_Centroid(ST_Collect(geom::geometry))::geography AS c
                          FROM reports WHERE incident_id = :iid) sub
                 WHERE i.id = :iid
                """
            ),
            {"iid": row["incident_id"]},
        )
    await session.commit()

    await publish(
        row["district_id"], "report.moved",
        {"report_id": str(row["id"]), "incident_id": row["incident_id"],
         "lat": lat, "lng": lng},
    )
    await trigger_optimize(row["district_id"], "location_update")
    return {"ok": True, "lat": lat, "lng": lng, "incident_id": row["incident_id"]}


@router.get("/shelters/nearby")
async def shelters_nearby(
    lat: float,
    lng: float,
    limit: int = 5,
    max_km: float = 60.0,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """§12 — nearest shelters, for a citizen deciding where to walk.

    Searched across EVERY district, deliberately. This used to filter on
    `district_id`, defaulting to the deployment's configured district — so a
    reporter standing in Bapatla was handed shelters in Chikiti, 569 km away in
    another state, because those were the only ones the query could see. The
    distances were computed correctly and the answer was still useless.

    Somebody deciding which way to walk does not care whose district a building
    is in, and a district border is exactly where the nearest shelter is most
    likely to be on the other side. So this is a geographic query with no
    administrative filter; the owning district travels back in the response so
    the operator and the citizen can both see it.

    `max_km` bounds it so a location outside the covered corridor gets an empty
    list — an honest "nothing near you" — instead of a shelter 600 km away
    presented as the closest option.

    Full shelters are returned too rather than hidden: someone who can see the
    nearest one is full walks to the second instead of arriving and being turned
    away.
    """
    rows = (
        await session.execute(
            text(
                """
                SELECT s.id, s.name, s.capacity_total, s.occupancy, s.status,
                       s.has_medical, s.has_water, s.has_power, s.contact,
                       d.name AS district_name, d.state,
                       ST_Y(s.geom::geometry) AS lat, ST_X(s.geom::geometry) AS lng,
                       ST_Distance(s.geom, ST_MakePoint(:lng, :lat)::geography) AS distance_m
                FROM shelters s
                LEFT JOIN districts d ON d.id = s.district_id
                WHERE ST_DWithin(s.geom, ST_MakePoint(:lng, :lat)::geography, :max_m)
                ORDER BY s.geom <-> ST_MakePoint(:lng, :lat)::geography
                LIMIT :lim
                """
            ),
            {"lat": lat, "lng": lng, "lim": limit, "max_m": max_km * 1000.0},
        )
    ).mappings().all()

    out = []
    for r in rows:
        total = int(r["capacity_total"] or 0)
        free = max(0, total - int(r["occupancy"] or 0))
        pct = (int(r["occupancy"] or 0) / total) if total else 1.0
        out.append(
            {
                "id": r["id"], "name": r["name"],
                "lat": r["lat"], "lng": r["lng"],
                "distance_km": round(r["distance_m"] / 1000.0, 1),
                "capacity_total": total,
                "available": free,
                # A blunt label, because "82% occupancy" is not a decision and
                # "almost full" is.
                "occupancy_label": (
                    "full" if r["status"] != "open" or free == 0
                    else "almost_full" if pct >= 0.85
                    else "open"
                ),
                "status": r["status"],
                "has_medical": r["has_medical"],
                "has_water": r["has_water"],
                "has_power": r["has_power"],
                "contact": r["contact"],
                # Which district actually owns this building. Near a border the
                # nearest shelter is routinely in the next district, and the
                # citizen should be able to see that rather than be surprised
                # by it on arrival.
                "district_name": r["district_name"],
                "state": r["state"],
            }
        )
    return out


@router.get("/alerts/for-location")
async def alerts_for_location(
    lat: float,
    lng: float,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """§13 — official alerts covering THIS point, for the citizen app.

    Distinct from /alerts/active, which is every alert in the district. A
    citizen should be told about the polygon they are standing in, not warned
    about weather 60 km away — otherwise they learn to ignore the banner, which
    is the one outcome a warning system cannot afford.
    """
    rows = (
        await session.execute(
            text(
                """
                SELECT cap_identifier, event, severity, urgency, certainty,
                       headline, instruction, effective_from, expires_at,
                       source_agency
                FROM alerts
                -- No district filter, for the same reason as shelters above: a
                -- CAP polygon is a weather system, and weather does not stop at
                -- a district line. The polygon test already answers the only
                -- question that matters — does this alert cover where you are
                -- standing.
                WHERE expires_at > now()
                  AND area_polygon IS NOT NULL
                  AND ST_Intersects(area_polygon, ST_MakePoint(:lng, :lat)::geography)
                ORDER BY CASE severity
                             WHEN 'Extreme'  THEN 4
                             WHEN 'Severe'   THEN 3
                             WHEN 'Moderate' THEN 2
                             WHEN 'Minor'    THEN 1
                             ELSE 0 END DESC
                """
            ),
            {"lat": lat, "lng": lng},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


# ── resources ──────────────────────────────────────────────────────────────
@router.get("/resources")
async def list_resources(
    district_id: int = Query(default=settings.district_id),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    rows = (
        await session.execute(
            text(
                """
                SELECT id, name, type::text AS type, agency, capabilities, capacity,
                       COALESCE(stock_water_l, 0) AS stock_water_l,
                       COALESCE(stock_food_kg, 0) AS stock_food_kg,
                       load, status, committed_incident_id, contact,
                       ST_Y(current_geom::geometry) AS lat,
                       ST_X(current_geom::geometry) AS lng
                FROM resources WHERE district_id = :did ORDER BY name
                """
            ),
            {"did": district_id},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


@router.patch("/resources/{resource_id}/status")
async def update_resource_status(
    resource_id: int, body: dict, session: AsyncSession = Depends(get_session)
) -> dict:
    """Field-unit status update. Three large buttons on a phone, one thumb.
    Each press flips capacity in the optimizer within a second."""
    new_status = body.get("status")
    if new_status not in ("idle", "enroute", "onsite", "returning", "offline"):
        raise HTTPException(422, "invalid status")

    before = (
        await session.execute(
            text("SELECT status, district_id, committed_incident_id FROM resources "
                 "WHERE id = :id"),
            {"id": resource_id},
        )
    ).mappings().one_or_none()
    if not before:
        raise HTTPException(404, "resource not found")

    clear_commitment = new_status in ("idle", "returning", "offline")
    await session.execute(
        text(
            "UPDATE resources SET status = :s, last_ping_at = now()"
            + (", committed_incident_id = NULL" if clear_commitment else "")
            + " WHERE id = :id"
        ),
        {"s": new_status, "id": resource_id},
    )
    await _audit(
        session, before["district_id"], body.get("actor", "field"), "resource_status",
        "resource", resource_id, dict(before), body,
    )
    await session.commit()
    await publish(
        before["district_id"], "resource.moved", {"id": resource_id, "status": new_status}
    )
    await trigger_optimize(before["district_id"], "resource_status")
    return {"ok": True}


# ── shelters ───────────────────────────────────────────────────────────────
@router.get("/shelters")
async def list_shelters(
    district_id: int = Query(default=settings.district_id),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    rows = (
        await session.execute(
            text(
                """
                SELECT id, name, capacity_total, occupancy, has_medical, has_power,
                       has_water, status, contact,
                       ST_Y(geom::geometry) AS lat, ST_X(geom::geometry) AS lng
                FROM shelters WHERE district_id = :did ORDER BY name
                """
            ),
            {"did": district_id},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


@router.patch("/shelters/{shelter_id}")
async def update_shelter(
    shelter_id: int, body: dict, session: AsyncSession = Depends(get_session)
) -> dict:
    fields, params = [], {"id": shelter_id}
    for key in ("occupancy", "status"):
        if key in body:
            fields.append(f"{key} = :{key}")
            params[key] = body[key]
    if not fields:
        raise HTTPException(422, "nothing to update")
    fields.append("last_verified_at = now()")

    await session.execute(
        text(f"UPDATE shelters SET {', '.join(fields)} WHERE id = :id"), params
    )
    # A shelter at capacity leaves the flow network on the next cycle without
    # any special-case code; overflow re-routes itself.
    await session.execute(
        text(
            "UPDATE shelters SET status = 'full' "
            "WHERE id = :id AND occupancy >= capacity_total AND status = 'open'"
        ),
        {"id": shelter_id},
    )
    await session.commit()
    await trigger_optimize(settings.district_id, "shelter_updated")
    return {"ok": True}


# ── assignments ────────────────────────────────────────────────────────────
@router.get("/assignments")
async def list_assignments(
    strategy: str = Query("optimized", pattern="^(optimized|greedy)$"),
    district_id: int = Query(default=settings.district_id),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """strategy=greedy|optimized powers the toggle. Both plans are already in
    the table — this reads them, it does not re-solve."""
    rows = (
        await session.execute(
            text(
                """
                SELECT a.id, a.incident_id, a.resource_id, a.eta_seconds, a.status,
                       a.strategy, a.kind, r.name AS resource_name,
                       ST_Y(r.current_geom::geometry) AS from_lat,
                       ST_X(r.current_geom::geometry) AS from_lng,
                       ST_Y(i.centroid::geometry) AS to_lat,
                       ST_X(i.centroid::geometry) AS to_lng,
                       i.severity_score::float AS severity_score
                FROM assignments a
                JOIN resources r ON r.id = a.resource_id
                JOIN incidents i ON i.id = a.incident_id
                WHERE a.district_id = :did AND a.kind = 'dispatch'
                  AND a.status IN ('proposed','committed')
                  AND (a.status = 'committed' OR a.strategy = :strategy)
                ORDER BY i.severity_score DESC
                """
            ),
            {"did": district_id, "strategy": strategy},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


@router.post("/assignments/{assignment_id}/commit")
async def commit_assignment(
    assignment_id: int, body: dict | None = None, session: AsyncSession = Depends(get_session)
) -> dict:
    """The human commits. Nothing dispatches itself — a person is always in the
    loop, and the log records who."""
    body = body or {}
    row = (
        await session.execute(
            text(
                "SELECT district_id, incident_id, resource_id, shelter_id, kind, "
                "       people, water_l, food_kg, status "
                "FROM assignments WHERE id = :id"
            ),
            {"id": assignment_id},
        )
    ).mappings().one_or_none()
    if not row:
        raise HTTPException(404, "assignment not found")
    if row["status"] == "committed":
        return {"ok": True, "already": True}

    await session.execute(
        text("UPDATE assignments SET status = 'committed', committed_at = now() "
             "WHERE id = :id"),
        {"id": assignment_id},
    )

    if row["kind"] == "supply":
        # Delivering relief moves real stock off a real truck. Without this the
        # plan would re-deliver the same water every cycle and report full
        # coverage while nothing left the depot — the same failure the shelter
        # occupancy had before it was wired up.
        #
        # GREATEST(... , 0) clamps so two operators committing overlapping plans
        # cannot drive a truck's stock negative.
        water = int(row["water_l"] or 0)
        food = int(row["food_kg"] or 0)

        await session.execute(
            text(
                """
                UPDATE resources
                   SET stock_water_l = GREATEST(COALESCE(stock_water_l, 0) - :water, 0),
                       stock_food_kg = GREATEST(COALESCE(stock_food_kg, 0) - :food, 0)
                 WHERE id = :rid
                """
            ),
            {"rid": row["resource_id"], "water": water, "food": food},
        )
        # Credited to the incident so the next cycle plans the remainder rather
        # than the whole need again.
        await session.execute(
            text(
                """
                UPDATE incidents
                   SET water_delivered_l = COALESCE(water_delivered_l, 0) + :water,
                       food_delivered_kg = COALESCE(food_delivered_kg, 0) + :food
                 WHERE id = :iid
                """
            ),
            {"iid": row["incident_id"], "water": water, "food": food},
        )

        await _audit(
            session, row["district_id"], body.get("actor", "operator"),
            "commit_supply", "assignment", assignment_id, dict(row),
            {"water_l": water, "food_kg": food}, body.get("reason"),
        )
        await session.commit()
        await publish(
            row["district_id"], "supply.delivered",
            {"incident_id": row["incident_id"], "resource_id": row["resource_id"],
             "water_l": water, "food_kg": food},
        )
        await trigger_optimize(row["district_id"], "supply_committed")
        return {"ok": True, "kind": "supply", "water_l": water, "food_kg": food}

    if row["kind"] == "evacuation":
        # §6.6 — actually move the people.
        #
        # The min-cost flow has been computing evacuation plans since P1, but
        # nothing ever wrote the result back: `shelters.occupancy` stayed at 0
        # forever, so every shelter read "1382 free / 1382", "people evacuated"
        # read 0, and the shortfall alarm could never fire because the district
        # never appeared to fill. The plan was real and permanently unactionable.
        #
        # LEAST() clamps to the free beds that exist AT COMMIT TIME rather than
        # when the plan was computed — two operators committing overlapping
        # plans must not be able to push a shelter past its own capacity.
        placed = (
            await session.execute(
                text(
                    """
                    UPDATE shelters
                       SET occupancy = occupancy
                                     + LEAST(:people, GREATEST(capacity_total - occupancy, 0)),
                           status = CASE
                                        WHEN occupancy
                                             + LEAST(:people,
                                                     GREATEST(capacity_total - occupancy, 0))
                                             >= capacity_total THEN 'full'
                                        ELSE status
                                    END
                     WHERE id = :sid
                    RETURNING occupancy, capacity_total,
                              LEAST(:people, GREATEST(capacity_total - occupancy, 0)) AS taken
                    """
                ),
                {"sid": row["shelter_id"], "people": int(row["people"] or 0)},
            )
        ).mappings().one_or_none()

        await _audit(
            session, row["district_id"], body.get("actor", "operator"),
            "commit_evacuation", "assignment", assignment_id, dict(row),
            {"shelter_id": row["shelter_id"], "people": int(row["people"] or 0)},
            body.get("reason"),
        )
        await session.commit()
        await publish(
            row["district_id"], "shelter.updated",
            {"shelter_id": row["shelter_id"],
             "occupancy": placed["occupancy"] if placed else None,
             "people": int(row["people"] or 0)},
        )
        return {
            "ok": True,
            "kind": "evacuation",
            "shelter_id": row["shelter_id"],
            "people_placed": int(placed["taken"]) if placed else 0,
        }

    # Commitment locking: from here the pairing is a fixed input to every later
    # solver run, so the map stops thrashing.
    await session.execute(
        text(
            "UPDATE resources SET status = 'enroute', committed_incident_id = :iid "
            "WHERE id = :rid"
        ),
        {"iid": row["incident_id"], "rid": row["resource_id"]},
    )
    await session.execute(
        text("UPDATE incidents SET status = 'assigned' WHERE id = :iid AND status = 'open'"),
        {"iid": row["incident_id"]},
    )
    await _audit(
        session, row["district_id"], body.get("actor", "operator"), "commit_assignment",
        "assignment", assignment_id, dict(row), {"status": "committed"}, body.get("reason"),
    )
    await session.commit()

    await publish(
        row["district_id"], "assignment.committed",
        {"id": assignment_id, "resource_id": row["resource_id"],
         "incident_id": row["incident_id"]},
    )
    return {"ok": True}


@router.post("/assignments/{assignment_id}/override")
async def override_assignment(
    assignment_id: int, body: dict, session: AsyncSession = Depends(get_session)
) -> dict:
    """Reassign to a different unit. The operator always outranks the solver —
    but the override and its reason are logged and feed the after-action
    report."""
    new_resource_id = body.get("resource_id")
    if not new_resource_id:
        raise HTTPException(422, "resource_id required")

    row = (
        await session.execute(
            text("SELECT district_id, incident_id, resource_id FROM assignments "
                 "WHERE id = :id"),
            {"id": assignment_id},
        )
    ).mappings().one_or_none()
    if not row:
        raise HTTPException(404, "assignment not found")

    await session.execute(
        text("UPDATE resources SET status = 'idle', committed_incident_id = NULL "
             "WHERE id = :rid"),
        {"rid": row["resource_id"]},
    )
    await session.execute(
        text(
            "UPDATE assignments SET resource_id = :rid, status = 'committed', "
            "committed_at = now() WHERE id = :id"
        ),
        {"rid": new_resource_id, "id": assignment_id},
    )
    await session.execute(
        text(
            "UPDATE resources SET status = 'enroute', committed_incident_id = :iid "
            "WHERE id = :rid"
        ),
        {"iid": row["incident_id"], "rid": new_resource_id},
    )
    await _audit(
        session, row["district_id"], body.get("actor", "operator"), "override_assignment",
        "assignment", assignment_id, dict(row), {"resource_id": new_resource_id},
        body.get("reason", "operator judgement"),
    )
    await session.commit()
    await publish(row["district_id"], "assignment.committed", {"id": assignment_id})
    return {"ok": True}


# ── alerts, quarantine, metrics ────────────────────────────────────────────
@router.get("/alerts/active")
async def active_alerts(
    district_id: int = Query(default=settings.district_id),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    rows = (
        await session.execute(
            text(
                """
                SELECT id, cap_identifier, source_agency, event, severity, urgency,
                       headline, instruction, effective_from, expires_at,
                       ST_AsGeoJSON(area_polygon::geometry) AS area_geojson
                FROM alerts
                WHERE district_id = :did AND (expires_at IS NULL OR expires_at > now())
                ORDER BY ingested_at DESC
                """
            ),
            {"did": district_id},
        )
    ).mappings().all()
    return [{**dict(r), "area_geojson": json.loads(r["area_geojson"]) if r["area_geojson"] else None}
            for r in rows]


@router.get("/quarantine")
async def quarantine_queue(
    district_id: int = Query(default=settings.district_id),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Visible, reviewable, never auto-dispatched — and never silently
    dropped."""
    rows = (
        await session.execute(
            text(
                """
                SELECT id, hazard_type::text AS hazard_type, severity_raw, description,
                       raw_text, source::text AS source, trust_score::float AS trust_score,
                       trust_breakdown, reference_code, created_at,
                       ST_Y(geom::geometry) AS lat, ST_X(geom::geometry) AS lng
                FROM reports
                WHERE district_id = :did AND status = 'quarantined'
                ORDER BY created_at DESC LIMIT 100
                """
            ),
            {"did": district_id},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


@router.post("/quarantine/{report_id}/release")
async def release_from_quarantine(
    report_id: str, session: AsyncSession = Depends(get_session)
) -> dict:
    """An operator vouching for a report overrides the trust score. Logged."""
    await session.execute(
        text("UPDATE reports SET status = 'received', trust_score = 0.6 WHERE id = :id"),
        {"id": report_id},
    )
    await _audit(
        session, settings.district_id, "operator", "release_quarantine",
        "report", report_id, None, {"status": "received"},
    )
    await session.commit()
    await trigger_optimize(settings.district_id, "quarantine_release")
    return {"ok": True}


@router.get("/metrics", response_model=Metrics)
async def metrics(
    district_id: int = Query(default=settings.district_id),
    session: AsyncSession = Depends(get_session),
) -> Metrics:
    row = (
        await session.execute(
            text(
                """
                SELECT
                  (SELECT COUNT(*) FROM incidents
                    WHERE district_id = :did AND status IN ('open','assigned')) AS open_incidents,
                  -- Critical incidents with no unit on them in the current
                  -- optimized plan. Counting only committed rows would report
                  -- every critical incident as unassigned until an operator
                  -- clicks, which says nothing about the solver.
                  (SELECT COUNT(*) FROM incidents i
                    WHERE i.district_id = :did AND i.status IN ('open','assigned')
                      AND i.severity_score >= :crit
                      AND NOT EXISTS (SELECT 1 FROM assignments a
                                       WHERE a.incident_id = i.id
                                         AND a.kind = 'dispatch'
                                         AND (a.status = 'committed'
                                              OR (a.status = 'proposed'
                                                  AND a.strategy = 'optimized')))
                  ) AS critical_unassigned,
                  (SELECT COUNT(*) FROM resources
                    WHERE district_id = :did AND status IN ('idle','returning')) AS units_free,
                  (SELECT COUNT(*) FROM resources
                    WHERE district_id = :did AND status IN ('enroute','onsite')) AS units_committed,
                  (SELECT COUNT(*) FROM reports
                    WHERE district_id = :did AND status = 'quarantined') AS quarantined,
                  (SELECT COALESCE(SUM(occupancy),0) FROM shelters
                    WHERE district_id = :did) AS evacuated,
                  (SELECT COALESCE(SUM(capacity_total),0) FROM shelters
                    WHERE district_id = :did) AS capacity,
                  -- §32 severity bands. Thresholds match the queue colours so
                  -- the strip and the list can never disagree on screen.
                  (SELECT COUNT(*) FROM incidents WHERE district_id = :did
                    AND status IN ('open','assigned') AND severity_score >= 70) AS n_critical,
                  (SELECT COUNT(*) FROM incidents WHERE district_id = :did
                    AND status IN ('open','assigned')
                    AND severity_score >= 55 AND severity_score < 70) AS n_high,
                  (SELECT COUNT(*) FROM incidents WHERE district_id = :did
                    AND status IN ('open','assigned')
                    AND severity_score >= 40 AND severity_score < 55) AS n_medium,
                  (SELECT COUNT(*) FROM incidents WHERE district_id = :did
                    AND status IN ('open','assigned')
                    AND severity_score < 40) AS n_low,
                  (SELECT COUNT(*) FROM shelters
                    WHERE district_id = :did AND status = 'open') AS shelters_open,
                  (SELECT COALESCE(SUM(GREATEST(capacity_total - occupancy, 0)),0)
                     FROM shelters WHERE district_id = :did AND status = 'open')
                    AS beds_available,
                  -- Proposed but not yet committed: the operator's actual
                  -- to-do count, and the number that should be falling during
                  -- the demo.
                  (SELECT COUNT(*) FROM assignments
                    WHERE district_id = :did AND kind = 'dispatch'
                      AND status = 'proposed' AND strategy = 'optimized')
                    AS pending_allocations
                """
            ),
            {"did": district_id, "crit": CRITICAL_THRESHOLD},
        )
    ).mappings().one()

    run = (
        await session.execute(
            text(
                """
                SELECT mean_response_opt, mean_response_greedy, worst_case_opt,
                       mean_common_opt, mean_common_greedy, common_incidents,
                       worst_case_greedy, unassigned_critical_opt,
                       unassigned_critical_greedy, served_opt, served_greedy,
                       total_response_opt, total_response_greedy,
                       cycle_ms, degraded_eta
                FROM solver_runs WHERE district_id = :did
                ORDER BY created_at DESC LIMIT 1
                """
            ),
            {"did": district_id},
        )
    ).mappings().one_or_none()

    shortfall = (
        await session.execute(
            text(
                """
                SELECT GREATEST(0, COALESCE(SUM(i.people_affected_est),0)
                       - COALESCE((SELECT SUM(capacity_total - occupancy) FROM shelters
                                    WHERE district_id = :did AND status = 'open'), 0))::int
                FROM incidents i
                WHERE i.district_id = :did AND i.status IN ('open','assigned')
                """
            ),
            {"did": district_id},
        )
    ).scalar_one()

    capacity = int(row["capacity"] or 0)
    return Metrics(
        open_incidents=row["open_incidents"],
        critical_unassigned=row["critical_unassigned"],
        units_free=row["units_free"],
        units_committed=row["units_committed"],
        mean_response_min={
            "optimized": float(run["mean_response_opt"]) if run else 0.0,
            "greedy": float(run["mean_response_greedy"]) if run else 0.0,
        },
        worst_case_min={
            "optimized": float(run["worst_case_opt"]) if run else 0.0,
            "greedy": float(run["worst_case_greedy"]) if run else 0.0,
        },
        mean_common_min={
            "optimized": float(run["mean_common_opt"] or 0) if run else 0.0,
            "greedy": float(run["mean_common_greedy"] or 0) if run else 0.0,
        },
        common_incidents=int(run["common_incidents"] or 0) if run else 0,
        incidents_served={
            "optimized": int(run["served_opt"] or 0) if run else 0,
            "greedy": int(run["served_greedy"] or 0) if run else 0,
        },
        people_evacuated=int(row["evacuated"] or 0),
        shelter_occupancy_pct=round(100.0 * (row["evacuated"] or 0) / capacity, 1) if capacity else 0.0,
        shelter_shortfall=int(shortfall or 0),
        quarantined=int(row["quarantined"] or 0),
        degraded_eta=bool(run["degraded_eta"]) if run else False,
        last_cycle_ms=int(run["cycle_ms"]) if run else None,
        incidents_critical=int(row["n_critical"] or 0),
        incidents_high=int(row["n_high"] or 0),
        incidents_medium=int(row["n_medium"] or 0),
        incidents_low=int(row["n_low"] or 0),
        shelters_open=int(row["shelters_open"] or 0),
        shelter_capacity_available=int(row["beds_available"] or 0),
        pending_allocations=int(row["pending_allocations"] or 0),
    )


@router.post("/optimize")
async def force_optimize(
    district_id: int = Query(default=settings.district_id),
) -> dict:
    """Force a solver run. Also the hook the demo simulator uses."""
    await trigger_optimize(district_id, "manual")
    return {"queued": True}
