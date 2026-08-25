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
from .assignment import compare, evaluate, solve
from .clustering import cluster_and_score
from .routing import eta_matrix
from .shelters import allocate_shelters
from .supplies import SupplyDepotView, allocate_supplies
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

# EVERY open incident enters the solver. No exceptions, no pre-filter.
#
# There used to be a 50 km spatial pre-filter here, justified as keeping the
# matrix small and the O(n^3) term harmless. What it actually did was silently
# delete incidents from the problem: an incident with no unit within 50 km was
# dropped before the solver ever saw it, so it sat on the board forever with no
# recommendation, no error, and an SLA clock ticking past breach. The operator
# had no way to distinguish "nothing can help this" from "the system never
# looked".
#
# The performance argument does not survive contact with the numbers. A
# district runs ~40 units against tens of incidents; the full matrix is a few
# thousand cells and `linear_sum_assignment` solves it in single-digit
# milliseconds. We were trading correctness for nothing.
#
# Distance now costs what it should: a far unit is expensive in the cost matrix
# and loses to a near one, but it is never ineligible. If the only boat is two
# hours away, two hours away is the answer — and the operator can see it and
# decide.
OPEN_INCIDENTS_SQL = text(
    """
    SELECT i.id, i.hazard_type::text AS hazard_type, i.severity_score,
           i.people_affected_est, i.status, i.needs_medical, i.sla_deadline,
           ST_Y(i.centroid::geometry) AS lat,
           ST_X(i.centroid::geometry) AS lng
    FROM incidents i
    WHERE i.district_id = :did
      AND i.status IN ('open','assigned')
    ORDER BY i.severity_score DESC
    """
)

# Supply-carrying resources and what is actually on them. A truck with an empty
# tank is not relief capacity, so stock travels with the row rather than being
# assumed from the vehicle type.
SUPPLY_DEPOTS_SQL = text(
    """
    SELECT id, name, status,
           COALESCE(stock_water_l, 0) AS stock_water_l,
           COALESCE(stock_food_kg, 0) AS stock_food_kg,
           ST_Y(current_geom::geometry) AS lat,
           ST_X(current_geom::geometry) AS lng
    FROM resources
    WHERE district_id = :did
      AND 'supply' = ANY(capabilities)
      AND status IN ('idle','returning')
    """
)

# What has already reached each incident, so a second cycle plans the remainder
# instead of re-sending relief that has arrived.
DELIVERED_SQL = text(
    """
    SELECT id,
           COALESCE(water_delivered_l, 0) AS water_l,
           COALESCE(food_delivered_kg, 0) AS food_kg
    FROM incidents
    WHERE district_id = :did AND status IN ('open','assigned')
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
                await session.execute(OPEN_INCIDENTS_SQL, {"did": district_id})
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
        # Head to head on the incidents both plans serve. See compare(): the
        # per-strategy means are not comparable to each other.
        head_to_head = compare(opt_pairs, greedy_pairs, eta)
        opt["mean_common_min"] = head_to_head["mean_common_opt"]
        grd["mean_common_min"] = head_to_head["mean_common_greedy"]
        opt["differing_assignments"] = head_to_head["differing_assignments"]

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

        # ── relief supplies ───────────────────────────────────────────────
        # Rescue is solved above by Hungarian assignment. Relief is a
        # capacitated transportation problem and is solved here by min-cost
        # flow — the same solver the shelters use, because it is the same shape.
        supply = {"water_planned_l": 0, "food_planned_kg": 0,
                  "water_unmet_l": 0, "food_unmet_kg": 0}
        depots = [
            SupplyDepotView(
                id=r["id"], name=r["name"],
                lat=float(r["lat"]), lng=float(r["lng"]),
                stock_water_l=int(r["stock_water_l"]),
                stock_food_kg=int(r["stock_food_kg"]),
                status=r["status"],
            )
            for r in (
                await session.execute(SUPPLY_DEPOTS_SQL, {"did": district_id})
            ).mappings().all()
        ]
        if depots:
            delivered = {
                r["id"]: (int(r["water_l"]), int(r["food_kg"]))
                for r in (
                    await session.execute(DELIVERED_SQL, {"did": district_id})
                ).mappings().all()
            }
            eta_di, _ = await eta_matrix(depots, incidents)
            supply = allocate_supplies(incidents, depots, eta_di, delivered)

            await session.execute(
                text(
                    "UPDATE assignments SET status = 'cancelled' "
                    "WHERE district_id = :did AND status = 'proposed' AND kind = 'supply'"
                ),
                {"did": district_id},
            )
            for d in supply["deliveries"]:
                await session.execute(
                    text(
                        """
                        INSERT INTO assignments (district_id, incident_id, resource_id, kind,
                                                 eta_seconds, water_l, food_kg,
                                                 solver_run_id, status)
                        VALUES (:did, :iid, :rid, 'supply', :eta, :water, :food, :run, 'proposed')
                        """
                    ),
                    {
                        "did": district_id, "iid": d["incident_id"],
                        "rid": d["resource_id"], "eta": d["eta_seconds"],
                        "water": d["quantity"] if d["unit"] == "water_l" else None,
                        "food": d["quantity"] if d["unit"] == "food_kg" else None,
                        "run": str(run_id),
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
                    total_response_opt, total_response_greedy, cycle_ms, degraded_eta,
                    mean_common_opt, mean_common_greedy, common_incidents)
                VALUES (:id, :did, :ni, :nr, :mo, :mg, :wo, :wg, :uo, :ug,
                        :so, :sg, :to, :tg, :ms, :deg, :mco, :mcg, :nc)
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
                "mco": head_to_head["mean_common_opt"],
                "mcg": head_to_head["mean_common_greedy"],
                "nc": head_to_head["common_incidents"],
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
            "supply": supply,
            "cycle_ms": cycle_ms,
            "degraded_eta": degraded,
            **cluster_stats,
        }

        await publish(district_id, "reoptimized", summary)
        if supply.get("water_unmet_l", 0) > 0 or supply.get("food_unmet_kg", 0) > 0:
            # The district cannot feed or water everyone it has open right now.
            # Like the shelter shortfall, this is an SDMA escalation and a real
            # operational number rather than a dashboard decoration.
            await publish(
                district_id, "alarm",
                {
                    "code": "RELIEF_SUPPLY_SHORTFALL",
                    "detail": {
                        "water_l": supply.get("water_unmet_l", 0),
                        "food_kg": supply.get("food_unmet_kg", 0),
                    },
                },
            )

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
