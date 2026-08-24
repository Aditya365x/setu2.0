"""§6.8 — the re-optimization loop.

Runs on: new report, unit status change, new alert, or a 30-second tick.

Three rules keep it stable, and all three are load-bearing:

1. **Debounce.** A 2-second collection window, so a burst of 80 reports
   triggers one solver run rather than eighty.
2. **Commitment locking.** Once an operator commits and a unit is en route,
   that pairing is a fixed input to every subsequent run. Without this the map
   thrashes and the demo looks broken.
3. **Per-district lock.** Two concurrent runs on one district produce
   conflicting writes. One lock, held for the run.
"""

import time
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..bus import publish, redis
from ..config import settings
from .assignment import evaluate, solve
from .clustering import cluster_and_score
from .routing import eta_matrix
from .shelters import allocate_shelters
from .types import IncidentView, ResourceView, ShelterView

CRITICAL_THRESHOLD = 70.0

# Only idle and returning units are candidates. Enforced here in SQL and
# re-asserted in the solver (§6.5) — every live-demo dispatch bug traces back
# to a busy unit leaking into the pool.
FREE_RESOURCES_SQL = text(
    """
    SELECT id, name, type::text AS type, agency, capabilities, capacity, status,
           committed_incident_id,
           ST_Y(current_geom::geometry) AS lat,
           ST_X(current_geom::geometry) AS lng
    FROM resources
    WHERE district_id = :did
      AND status IN ('idle','returning')
    """
)

# The 50 km spatial pre-filter keeps the matrix small and the O(n^3) term
# harmless. Beyond that, the district itself is the natural shard.
OPEN_INCIDENTS_SQL = text(
    """
    SELECT i.id, i.hazard_type::text AS hazard_type, i.severity_score,
           i.people_affected_est, i.status, i.needs_medical, i.sla_deadline,
           ST_Y(i.centroid::geometry) AS lat,
           ST_X(i.centroid::geometry) AS lng
    FROM incidents i
    WHERE i.district_id = :did
      AND i.status IN ('open','assigned')
      AND EXISTS (
          SELECT 1 FROM resources r
          WHERE r.district_id = i.district_id
            AND ST_DWithin(r.current_geom, i.centroid, :radius_m)
      )
    ORDER BY i.severity_score DESC
    """
)

OPEN_SHELTERS_SQL = text(
    """
    SELECT id, name, capacity_total, occupancy, has_medical, status,
           ST_Y(geom::geometry) AS lat, ST_X(geom::geometry) AS lng
    FROM shelters
    WHERE district_id = :did AND status = 'open'
    """
)


def _resource_views(rows) -> list[ResourceView]:
    return [
        ResourceView(
            id=r["id"], name=r["name"], type=r["type"], agency=r["agency"] or "",
            lat=float(r["lat"]), lng=float(r["lng"]),
            capabilities=set(r["capabilities"] or []),
            capacity=int(r["capacity"] or 1), status=r["status"],
            committed_incident_id=r["committed_incident_id"],
        )
        for r in rows
    ]


def _incident_views(rows) -> list[IncidentView]:
    return [
        IncidentView(
            id=r["id"], hazard_type=r["hazard_type"],
            lat=float(r["lat"]), lng=float(r["lng"]),
            severity_score=float(r["severity_score"] or 0),
            people_affected_est=int(r["people_affected_est"] or 0),
            status=r["status"], needs_medical=bool(r["needs_medical"]),
            sla_deadline=r["sla_deadline"],
        )
        for r in rows
    ]


def _shelter_views(rows) -> list[ShelterView]:
    return [
        ShelterView(
            id=r["id"], name=r["name"], lat=float(r["lat"]), lng=float(r["lng"]),
            capacity_total=int(r["capacity_total"]), occupancy=int(r["occupancy"] or 0),
            has_medical=bool(r["has_medical"]), status=r["status"],
        )
        for r in rows
    ]


async def _persist_assignments(
    session: AsyncSession, district_id: int, run_id: uuid.UUID,
    resources, incidents, eta, pairs, strategy: str,
) -> None:
    for i, j in pairs:
        await session.execute(
            text(
                """
                INSERT INTO assignments (district_id, incident_id, resource_id, kind,
                                         eta_seconds, cost, solver_run_id, strategy, status)
                VALUES (:did, :iid, :rid, 'dispatch', :eta, :cost, :run, :strategy, 'proposed')
                """
            ),
            {
                "did": district_id, "iid": incidents[j].id, "rid": resources[i].id,
                "eta": int(eta[i][j]), "cost": round(float(eta[i][j]) / 60.0, 3),
                "run": str(run_id), "strategy": strategy,
            },
        )


async def optimization_cycle(session: AsyncSession, district_id: int) -> dict[str, Any]:
    """One full pass. Returns the run summary that drives the metric strip."""
    lock_key = f"opt:lock:{district_id}"
    # NX + TTL: if a worker dies mid-run the lock releases itself rather than
    # wedging the district for the rest of the demo.
    got_lock = await redis.set(lock_key, "1", nx=True, ex=30)
    if not got_lock:
        return {"skipped": "locked"}

    started = time.perf_counter()
    try:
        cluster_stats = await cluster_and_score(session, district_id)

        resources = _resource_views(
            (await session.execute(FREE_RESOURCES_SQL, {"did": district_id})).mappings().all()
        )
        incidents = _incident_views(
            (
                await session.execute(
                    OPEN_INCIDENTS_SQL,
                    {"did": district_id, "radius_m": settings.spatial_prefilter_km * 1000},
                )
            ).mappings().all()
        )

        if not incidents or not resources:
            return {"skipped": "nothing to solve", **cluster_stats}

        run_id = uuid.uuid4()
        eta, degraded = await eta_matrix(resources, incidents)

        # Both strategies, every cycle. The toggle then reads two real stored
        # plans rather than re-simulating one on demand.
        opt_pairs = solve(resources, incidents, eta.copy(), "optimized")
        greedy_pairs = solve(resources, incidents, eta.copy(), "greedy")

        opt = evaluate(opt_pairs, incidents, eta, CRITICAL_THRESHOLD)
        grd = evaluate(greedy_pairs, incidents, eta, CRITICAL_THRESHOLD)

        # Supersede the previous proposal set; committed rows are untouched,
        # which is what makes commitment locking hold across runs.
        await session.execute(
            text(
                "UPDATE assignments SET status = 'cancelled' "
                "WHERE district_id = :did AND status = 'proposed' AND kind = 'dispatch'"
            ),
            {"did": district_id},
        )
        await _persist_assignments(
            session, district_id, run_id, resources, incidents, eta, opt_pairs, "optimized"
        )
        await _persist_assignments(
            session, district_id, run_id, resources, incidents, eta, greedy_pairs, "greedy"
        )

        # ── §6.6 evacuation routing ──────────────────────────────────────
        shelters = _shelter_views(
            (await session.execute(OPEN_SHELTERS_SQL, {"did": district_id})).mappings().all()
        )
        shortfall = 0
        if shelters:
            eta_is, _ = await eta_matrix(incidents, shelters)
            plan, shortfall = allocate_shelters(incidents, shelters, eta_is)
            await session.execute(
                text(
                    "UPDATE assignments SET status = 'cancelled' "
                    "WHERE district_id = :did AND status = 'proposed' AND kind = 'evacuation'"
                ),
                {"did": district_id},
            )
            for p in plan:
                await session.execute(
                    text(
                        """
                        INSERT INTO assignments (district_id, incident_id, shelter_id, kind,
                                                 eta_seconds, people, solver_run_id, status)
                        VALUES (:did, :iid, :sid, 'evacuation', :eta, :people, :run, 'proposed')
                        """
                    ),
                    {
                        "did": district_id, "iid": p["incident_id"], "sid": p["shelter_id"],
                        "eta": p["eta_seconds"], "people": p["people"], "run": str(run_id),
                    },
                )

        cycle_ms = int((time.perf_counter() - started) * 1000)

        await session.execute(
            text(
                """
                INSERT INTO solver_runs (id, district_id, n_incidents, n_resources,
                    mean_response_opt, mean_response_greedy, worst_case_opt,
                    worst_case_greedy, unassigned_critical_opt,
                    unassigned_critical_greedy, served_opt, served_greedy,
                    total_response_opt, total_response_greedy, cycle_ms, degraded_eta)
                VALUES (:id, :did, :ni, :nr, :mo, :mg, :wo, :wg, :uo, :ug,
                        :so, :sg, :to, :tg, :ms, :deg)
                """
            ),
            {
                "id": str(run_id), "did": district_id,
                "ni": len(incidents), "nr": len(resources),
                "mo": opt["mean_response_min"], "mg": grd["mean_response_min"],
                "wo": opt["worst_case_min"], "wg": grd["worst_case_min"],
                "uo": opt["unassigned_critical"], "ug": grd["unassigned_critical"],
                "so": opt["assigned"], "sg": grd["assigned"],
                "to": opt["total_response_min"], "tg": grd["total_response_min"],
                "ms": cycle_ms, "deg": degraded,
            },
        )
        await session.commit()

        summary = {
            "run_id": str(run_id),
            "incidents": len(incidents),
            "resources": len(resources),
            "optimized": opt,
            "greedy": grd,
            "shelter_shortfall": shortfall,
            "cycle_ms": cycle_ms,
            "degraded_eta": degraded,
            **cluster_stats,
        }

        await publish(district_id, "reoptimized", summary)
        if shortfall > 0:
            # The number no existing system produces: this district is short of
            # shelter capacity right now, and that is an SDMA escalation.
            await publish(
                district_id, "alarm",
                {"code": "SHELTER_CAPACITY_SHORTFALL", "detail": {"people": shortfall}},
            )

        return summary
    finally:
        await redis.delete(lock_key)
