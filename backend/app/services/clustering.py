"""§6.1 — spatio-temporal clustering. Deduplication.

Ten people report the same collapsed bridge. Left raw, the DEOC sees ten tasks
and dispatches against noise. PostGIS does this natively, so there is no
clustering code in the application layer at all — the whole stage is one query.

Two design notes that are load-bearing:

* **Partition by hazard type.** A medical emergency and a flood report 50 m
  apart are two different incidents requiring two different capabilities.
  Clustering across hazard types merges them and dispatches the wrong unit.
* **Merge before creating.** A new cluster near an existing open incident of
  the same hazard attaches to it rather than fragmenting the operating picture.
"""

import json
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from .scoring import IncidentScoringInput, severity_score

# eps = 300 m matches typical urban-ward granularity. Configurable per district:
# a dense town wants 150 m, a sparse block 800 m.
CLUSTER_SQL = text(
    """
    WITH recent AS (
        SELECT id, hazard_type, geom::geometry AS g, severity_raw,
               trust_score, people_reported
        FROM reports
        WHERE district_id = :did
          AND incident_id IS NULL
          AND status <> 'quarantined'
          AND trust_score >= :trust_floor
          AND created_at > now() - make_interval(mins => :window_min)
    ),
    clustered AS (
        SELECT id, hazard_type, g, severity_raw, trust_score, people_reported,
               -- eps is in the units of the input geometry. Our column is
               -- lat/lng degrees, so clustering it raw would read eps=300 as
               -- 300 DEGREES and collapse the entire district into a single
               -- incident. Project to UTM 45N (metres) first; the centroid
               -- below is still taken in 4326.
               ST_ClusterDBSCAN(ST_Transform(g, 32645), eps := :eps, minpoints := 1)
                   OVER (PARTITION BY hazard_type) AS cid
        FROM recent
    )
    SELECT hazard_type::text                 AS hazard_type,
           cid,
           ST_Y(ST_Centroid(ST_Collect(g)))  AS lat,
           ST_X(ST_Centroid(ST_Collect(g)))  AS lng,
           COUNT(*)                          AS report_count,
           AVG(severity_raw)                 AS mean_severity,
           AVG(trust_score)                  AS mean_trust,
           COALESCE(SUM(people_reported), 0) AS people_reported,
           ARRAY_AGG(id)                     AS report_ids
    FROM clustered
    GROUP BY hazard_type, cid
    """
)

# A cluster landing within eps of an existing open incident of the same hazard
# attaches to it. Without this the map fragments as a situation develops.
FIND_EXISTING_SQL = text(
    """
    SELECT id
    FROM incidents
    WHERE district_id = :did
      AND hazard_type = CAST(:hazard AS hazard)
      AND status IN ('open','assigned','onsite')
      AND ST_DWithin(centroid, ST_MakePoint(:lng, :lat)::geography, :eps)
    ORDER BY ST_Distance(centroid, ST_MakePoint(:lng, :lat)::geography)
    LIMIT 1
    """
)

POP_DENSITY_SQL = text(
    """
    SELECT COALESCE(
        (SELECT density FROM population_cells
          WHERE district_id = :did
          -- KNN on the geometry index. ST_MakePoint alone yields SRID 0, which
          -- PostGIS refuses to compare against our 4326 column, so set it
          -- explicitly rather than relying on a geography cast here.
          ORDER BY geom::geometry <-> ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)
          LIMIT 1), 0) AS density
    """
)

P95_DENSITY_SQL = text(
    """
    SELECT COALESCE(
        percentile_cont(0.95) WITHIN GROUP (ORDER BY density), 1)::float AS p95
    FROM population_cells WHERE district_id = :did
    """
)

ACTIVE_ALERT_SQL = text(
    """
    SELECT severity
    FROM alerts
    WHERE district_id = :did
      AND expires_at > now()
      AND area_polygon IS NOT NULL
      AND ST_Intersects(area_polygon, ST_MakePoint(:lng, :lat)::geography)
    ORDER BY CASE severity
                 WHEN 'Extreme'  THEN 4
                 WHEN 'Severe'   THEN 3
                 WHEN 'Moderate' THEN 2
                 WHEN 'Minor'    THEN 1
                 ELSE 0 END DESC
    LIMIT 1
    """
)

INCIDENT_AGG_SQL = text(
    """
    SELECT COUNT(*) AS n,
           AVG(severity_raw)::float AS mean_sev,
           AVG(trust_score)::float  AS mean_trust,
           -- MAX, emphatically not SUM. These reports all describe the SAME
           -- event, so ten witnesses saying "3 inside" means 3 people, not 30.
           -- Summing double-counts by the duplication factor, inflates every
           -- incident past every unit's capacity, and the capacity constraint
           -- then quietly renders the incident unassignable.
           COALESCE(MAX(people_reported), 0) AS people,
           EXTRACT(EPOCH FROM (now() - MIN(created_at))) / 60 AS age_min,
           -- §6 roll-up: bool_or, deliberately. If ANY witness to this event
           -- saw children, the incident involves children — a single negative
           -- report must not cancel a positive one, because the cost of
           -- under-responding is not symmetric with the cost of over-responding.
           COALESCE(bool_or(has_children), false) AS has_children,
           COALESCE(bool_or(has_elderly),  false) AS has_elderly,
           COALESCE(bool_or(has_injured),  false) AS has_injured,
           COALESCE(bool_or(has_disabled), false) AS has_disabled,
           ST_Y(ST_Centroid(ST_Collect(geom::geometry))) AS lat,
           ST_X(ST_Centroid(ST_Collect(geom::geometry))) AS lng
    FROM reports WHERE incident_id = :iid
    """
)

INSERT_INCIDENT_SQL = text(
    """
    INSERT INTO incidents (district_id, centroid, hazard_type, report_count,
                           people_affected_est, needs_medical, sla_deadline)
    VALUES (:did, ST_MakePoint(:lng, :lat)::geography, CAST(:hazard AS hazard),
            0, 0, :needs_medical, now() + make_interval(mins => :sla))
    RETURNING id
    """
)

UPDATE_INCIDENT_SQL = text(
    """
    UPDATE incidents
       SET severity_score      = :score,
           severity_breakdown  = CAST(:parts AS jsonb),
           report_count        = :n,
           people_affected_est = :people,
           has_children        = :children,
           has_elderly         = :elderly,
           has_injured         = :injured,
           has_disabled        = :disabled,
           centroid            = ST_MakePoint(:lng, :lat)::geography
     WHERE id = :iid
    """
)

# How long the DEOC has before an incident of this hazard counts as
# unacceptably unserved. Drives the SLA breach penalty in §6.5.
SLA_MINUTES = {
    "medical": 30,
    "fire": 30,
    "building_collapse": 45,
    "landslide": 60,
    "stranded": 90,
    "flood": 120,
    "power_line": 120,
    "cyclone_damage": 180,
    "other": 180,
}


async def cluster_and_score(session: AsyncSession, district_id: int) -> dict[str, Any]:
    """Collapse loose reports into incidents, then score them. Returns run stats."""
    started = time.perf_counter()

    rows = (
        await session.execute(
            CLUSTER_SQL,
            {
                "did": district_id,
                "eps": settings.cluster_eps_m,
                "window_min": settings.cluster_window_min,
                "trust_floor": settings.trust_quarantine_threshold,
            },
        )
    ).mappings().all()

    cluster_ms = int((time.perf_counter() - started) * 1000)
    p95 = (await session.execute(P95_DENSITY_SQL, {"did": district_id})).scalar_one()

    created, merged = 0, 0

    for row in rows:
        lat, lng = float(row["lat"]), float(row["lng"])
        hazard = row["hazard_type"]
        geo = {"did": district_id, "lat": lat, "lng": lng}

        existing_id = (
            await session.execute(
                FIND_EXISTING_SQL, {**geo, "hazard": hazard, "eps": settings.cluster_eps_m}
            )
        ).scalar_one_or_none()

        density = (await session.execute(POP_DENSITY_SQL, geo)).scalar_one()
        alert_severity = (
            await session.execute(ACTIVE_ALERT_SQL, geo)
        ).scalar_one_or_none()

        if existing_id:
            incident_id = existing_id
            merged += 1
        else:
            incident_id = (
                await session.execute(
                    INSERT_INCIDENT_SQL,
                    {
                        **geo,
                        "hazard": hazard,
                        "needs_medical": hazard == "medical",
                        "sla": SLA_MINUTES.get(hazard, 180),
                    },
                )
            ).scalar_one()
            created += 1

        await session.execute(
            text(
                "UPDATE reports SET incident_id = :iid, status = 'clustered' "
                "WHERE id = ANY(:ids)"
            ),
            {"iid": incident_id, "ids": list(row["report_ids"])},
        )

        # Recompute over *all* reports on the incident, not just this cluster —
        # a merge must reflect the whole picture, not the newest fragment.
        agg = (
            await session.execute(INCIDENT_AGG_SQL, {"iid": incident_id})
        ).mappings().one()

        status = (
            await session.execute(
                text("SELECT status FROM incidents WHERE id = :iid"), {"iid": incident_id}
            )
        ).scalar_one()

        score, parts = severity_score(
            IncidentScoringInput(
                hazard_type=hazard,
                mean_severity=agg["mean_sev"] or 3.0,
                mean_trust=agg["mean_trust"] or 0.5,
                report_count=int(agg["n"]),
                pop_density_1km=float(density or 0),
                district_p95_density=float(p95 or 1),
                active_alert_severity=alert_severity,
                people_reported=int(agg["people"] or 0),
                age_minutes=float(agg["age_min"] or 0),
                status=status,
                has_children=bool(agg["has_children"]),
                has_elderly=bool(agg["has_elderly"]),
                has_injured=bool(agg["has_injured"]),
                has_disabled=bool(agg["has_disabled"]),
            )
        )

        # Nobody gave a number: fall back to a small per-incident estimate
        # rather than dispatching a boat for "0 people". Deliberately modest —
        # over-estimating here silently trips the capacity constraint.
        people_est = int(agg["people"] or 0) or 3

        await session.execute(
            UPDATE_INCIDENT_SQL,
            {
                "score": score,
                "parts": json.dumps(parts),
                "n": int(agg["n"]),
                "people": people_est,
                "lat": float(agg["lat"]),
                "lng": float(agg["lng"]),
                "children": bool(agg["has_children"]),
                "elderly": bool(agg["has_elderly"]),
                "injured": bool(agg["has_injured"]),
                "disabled": bool(agg["has_disabled"]),
                "iid": incident_id,
            },
        )

    await session.commit()
    return {
        "clusters": len(rows),
        "incidents_created": created,
        "incidents_merged": merged,
        "cluster_ms": cluster_ms,
    }
