"""Relief supply allocation — the second half of "rescue teams and relief supplies".

Rescue is one-to-one and solved by Hungarian assignment. Relief is capacitated
and many-to-many, so it is solved by min-cost flow. These tests pin the
properties that make that claim true rather than decorative.
"""

import numpy as np

from app.services.supplies import (
    FOOD_KG_PER_PERSON_DAY,
    WATER_L_PER_PERSON_DAY,
    SupplyDepotView,
    allocate_supplies,
    food_need,
    water_need,
)
from app.services.types import IncidentView


def _incident(iid: int, people: int, severity: float = 60.0) -> IncidentView:
    return IncidentView(
        id=iid, hazard_type="flood", lat=19.31, lng=84.79,
        severity_score=severity, people_affected_est=people,
    )


def _depot(rid: int, water: int, food: int, status: str = "idle") -> SupplyDepotView:
    return SupplyDepotView(
        id=rid, name=f"Truck {rid}", lat=19.31, lng=84.79,
        stock_water_l=water, stock_food_kg=food, status=status,
    )


def test_need_follows_sphere_standards():
    """The per-person figures are published standards, not invented constants.

    A Collector asked to justify a relief order needs to be able to name the
    source, and 15 L/person/day is the Sphere Handbook minimum — not the 7.5 L
    survival floor, which would be planning for people to be sick in a week.
    """
    inc = _incident(1, people=100)
    assert water_need(inc) == 100 * WATER_L_PER_PERSON_DAY == 1500
    assert food_need(inc) == int(100 * FOOD_KG_PER_PERSON_DAY) == 50


def test_delivered_relief_is_not_sent_twice():
    """The bug this exists to prevent: re-planning the whole need every cycle.

    Without netting off what has arrived, the optimizer would keep dispatching
    the same water forever, trucks would drive in circles, and coverage would
    read 100% while nothing actually reached anyone.
    """
    inc = _incident(1, people=100)
    assert water_need(inc, delivered_l=0) == 1500
    assert water_need(inc, delivered_l=600) == 900
    assert water_need(inc, delivered_l=1500) == 0
    # Over-delivery must not produce a negative need that credits other incidents.
    assert water_need(inc, delivered_l=9000) == 0


def test_one_truck_splits_across_several_incidents():
    """The reason this is a flow and not an assignment.

    A single truck serves several incidents in one run. An assignment solver
    would pick one and strand the rest.
    """
    incidents = [_incident(1, 20), _incident(2, 20), _incident(3, 20)]
    depot = _depot(1, water=900, food=100)
    eta = np.array([[300.0, 300.0, 300.0]])

    plan = allocate_supplies(incidents, [depot], eta)
    water = [d for d in plan["deliveries"] if d["unit"] == "water_l"]

    assert len(water) == 3, "one truck must be able to serve all three"
    assert sum(d["quantity"] for d in water) == 900
    assert plan["water_unmet_l"] == 0


def test_several_trucks_combine_on_one_incident():
    """And the converse: one incident too big for any single truck is served by
    several, rather than being declared unservable the way the capacity
    constraint used to do for rescue."""
    incident = _incident(1, people=200)          # 3000 L needed
    depots = [_depot(1, 1200, 50), _depot(2, 1200, 50), _depot(3, 1200, 50)]
    eta = np.array([[300.0], [300.0], [300.0]])

    plan = allocate_supplies([incident], depots, eta)
    water = [d for d in plan["deliveries"] if d["unit"] == "water_l"]

    assert len(water) >= 3, "the load must be split across trucks"
    assert sum(d["quantity"] for d in water) == 3000
    assert plan["water_unmet_l"] == 0


def test_delivery_never_exceeds_stock_on_the_truck():
    incident = _incident(1, people=1000)          # 15,000 L needed
    depot = _depot(1, water=2000, food=100)
    eta = np.array([[300.0]])

    plan = allocate_supplies([incident], [depot], eta)
    water = sum(d["quantity"] for d in plan["deliveries"] if d["unit"] == "water_l")

    assert water == 2000, "cannot deliver more water than the truck carries"
    assert plan["water_unmet_l"] == 13000


def test_district_wide_shortfall_is_surfaced_not_swallowed():
    """`unmet > 0` is the SDMA escalation — the district cannot water its own
    people right now. It must be a number, not an exception."""
    incidents = [_incident(1, 500), _incident(2, 500)]
    depots = [_depot(1, 1000, 20)]
    eta = np.array([[300.0, 600.0]])

    plan = allocate_supplies(incidents, depots, eta)
    assert plan["water_unmet_l"] == (500 + 500) * WATER_L_PER_PERSON_DAY - 1000
    assert plan["food_unmet_kg"] > 0


def test_a_committed_truck_is_not_relief_capacity():
    """Same §5.4 invariant as rescue: a unit already working is not stock we can
    plan against."""
    incident = _incident(1, people=50)
    busy = _depot(1, water=5000, food=500, status="enroute")
    eta = np.array([[300.0]])

    plan = allocate_supplies([incident], [busy], eta)
    assert plan["deliveries"] == []
    assert plan["water_unmet_l"] == 50 * WATER_L_PER_PERSON_DAY


def test_severity_pulls_relief_toward_the_worse_incident():
    """With enough stock for only one, the severe incident is served first."""
    mild = _incident(1, people=40, severity=15.0)
    severe = _incident(2, people=40, severity=95.0)
    depot = _depot(1, water=600, food=100)       # exactly one incident's worth

    # The severe one is FURTHER away, so distance alone would pick the mild one.
    eta = np.array([[300.0, 900.0]])

    plan = allocate_supplies([mild, severe], [depot], eta)
    by_incident = {}
    for d in plan["deliveries"]:
        if d["unit"] == "water_l":
            by_incident[d["incident_id"]] = by_incident.get(d["incident_id"], 0) + d["quantity"]

    assert by_incident.get(2, 0) > by_incident.get(1, 0), (
        "severity must outweigh a ten-minute detour"
    )


def test_no_depots_is_an_honest_zero_not_a_crash():
    incidents = [_incident(1, 30)]
    plan = allocate_supplies(incidents, [], np.zeros((0, 1)))
    assert plan["deliveries"] == []
    assert plan["water_planned_l"] == 0
