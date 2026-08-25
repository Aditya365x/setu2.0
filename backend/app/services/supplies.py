"""Relief supply allocation as min-cost flow.

The problem statement asks for allocation of "rescue teams **and relief
supplies**". Those are two different problems and they need two different
solvers, which is why this file exists next to `assignment.py` rather than
inside it:

* **Rescue** is one-to-one. One boat goes to one incident, and the question is
  which pairing minimises total response time. That is the Hungarian assignment
  in §6.5.

* **Relief** is many-to-many and capacitated. One truck serves several
  incidents, one incident may need several trucks, and stock depletes as it is
  delivered. That is a transportation problem, and it is structurally identical
  to the shelter evacuation in §6.6 — so it reuses the same min-cost flow rather
  than pretending it is an assignment.

Two commodities, run as two separate flows, deliberately not collapsed into an
abstract "supply unit". A truck can run out of drinking water while still
carrying food, and an operator needs to see which — a single blended number
would hide exactly the shortage that matters.

Needs are computed against what has ALREADY been delivered, so a second cycle
does not re-send relief that arrived in the first. Without that the plan would
happily deliver the same water forever and report full coverage while trucks
drove in circles.
"""

from dataclasses import dataclass
from typing import Sequence

import networkx as nx
import numpy as np

from .types import IncidentView

# Sphere Handbook minimum standards, per person per day.
#
#   Water: 15 L covers drinking, cooking and basic hygiene. The survival
#          minimum is 7.5 L; planning to the survival figure means planning for
#          people to be dirty and sick in a week, so 15 is the number relief
#          agencies actually use.
#   Food:  0.5 kg of dry ration is the standard family-kit arithmetic
#          (~2,100 kcal/person/day).
#
# These are published standards, not invented constants, and that matters when a
# Collector asks where the number came from.
WATER_L_PER_PERSON_DAY = 15
FOOD_KG_PER_PERSON_DAY = 0.5

# One delivery covers a day. Relief is resupplied daily in a real operation, and
# planning a week ahead on day one strands stock that a different ward needs now.
RELIEF_DAYS = 1

# Minutes of detour accepted to serve a severe incident before a mild one. The
# flow minimises travel time, so without this a truck would always take the
# nearest incident regardless of how badly it was hurt.
SEVERITY_PULL_MIN = 25.0
CRITICAL_THRESHOLD = 70.0


@dataclass
class SupplyDepotView:
    """A supply-carrying resource. Usually a truck; anything with stock works."""
    id: int
    name: str
    lat: float
    lng: float
    stock_water_l: int = 0
    stock_food_kg: int = 0
    status: str = "idle"

    @property
    def available(self) -> bool:
        # A truck already committed elsewhere is not stock we can plan against.
        return self.status in ("idle", "returning")


def water_need(inc: IncidentView, delivered_l: int = 0) -> int:
    """Outstanding drinking water for this incident, in litres."""
    total = int(max(0, inc.people_affected_est or 0) * WATER_L_PER_PERSON_DAY * RELIEF_DAYS)
    return max(0, total - max(0, delivered_l))


def food_need(inc: IncidentView, delivered_kg: int = 0) -> int:
    """Outstanding dry ration for this incident, in kilograms."""
    total = int(max(0, inc.people_affected_est or 0) * FOOD_KG_PER_PERSON_DAY * RELIEF_DAYS)
    return max(0, total - max(0, delivered_kg))


def _solve_commodity(
    incidents: Sequence[IncidentView],
    depots: Sequence[SupplyDepotView],
    eta_di: np.ndarray,
    demand: list[int],
    stock: list[int],
    unit: str,
) -> tuple[list[dict], int]:
    """One commodity, one flow. Returns (deliveries, unmet)."""
    total_demand = sum(demand)
    if total_demand == 0:
        return [], 0

    usable = [(k, d) for k, d in enumerate(depots) if d.available and stock[k] > 0]
    if not usable:
        return [], total_demand

    G = nx.DiGraph()
    for k, depot in usable:
        G.add_edge("SRC", f"d{k}", capacity=stock[k], weight=0)
        for j, inc in enumerate(incidents):
            if demand[j] <= 0:
                continue
            minutes = int(eta_di[k][j] / 60)
            # Severe incidents pull relief toward them. Subtracted from travel
            # time so the flow still prefers near over far, all else equal, but
            # will drive past a scratch to reach a catastrophe.
            pull = int(SEVERITY_PULL_MIN * (inc.severity_score or 0) / 100.0)
            G.add_edge(f"d{k}", f"i{j}", capacity=stock[k], weight=max(0, minutes - pull))

    for j, inc in enumerate(incidents):
        if demand[j] > 0:
            G.add_edge(f"i{j}", "SNK", capacity=demand[j], weight=0)

    if "SRC" not in G or "SNK" not in G:
        return [], total_demand

    # max_flow_min_cost moves as much as the network allows and picks the
    # cheapest way. A district-wide shortage appears as flow < demand rather
    # than as an exception, which is the signal an operator needs.
    flow = nx.max_flow_min_cost(G, "SRC", "SNK")

    deliveries: list[dict] = []
    moved = 0
    for k, depot in usable:
        for j, inc in enumerate(incidents):
            qty = flow.get(f"d{k}", {}).get(f"i{j}", 0)
            if qty > 0:
                deliveries.append(
                    {
                        "incident_id": inc.id,
                        "resource_id": depot.id,
                        "resource_name": depot.name,
                        "unit": unit,
                        "quantity": int(qty),
                        "eta_seconds": int(eta_di[k][j]),
                    }
                )
                moved += qty

    return deliveries, int(total_demand - moved)


def allocate_supplies(
    incidents: Sequence[IncidentView],
    depots: Sequence[SupplyDepotView],
    eta_di: np.ndarray,
    delivered: dict[int, tuple[int, int]] | None = None,
) -> dict:
    """Plan one relief cycle.

    `eta_di` is [depots x incidents] in seconds.
    `delivered` maps incident id -> (water_l_already, food_kg_already).

    Returns both commodities' plans plus the shortfalls, which are the numbers
    that go to the SDMA when a district cannot feed itself.
    """
    if not len(incidents) or not len(depots):
        return {
            "deliveries": [], "water_unmet_l": 0, "food_unmet_kg": 0,
            "water_planned_l": 0, "food_planned_kg": 0,
        }

    delivered = delivered or {}
    water_demand = [water_need(i, delivered.get(i.id, (0, 0))[0]) for i in incidents]
    food_demand = [food_need(i, delivered.get(i.id, (0, 0))[1]) for i in incidents]

    water_plan, water_unmet = _solve_commodity(
        incidents, depots, eta_di, water_demand,
        [d.stock_water_l for d in depots], "water_l",
    )
    food_plan, food_unmet = _solve_commodity(
        incidents, depots, eta_di, food_demand,
        [d.stock_food_kg for d in depots], "food_kg",
    )

    return {
        "deliveries": water_plan + food_plan,
        "water_planned_l": sum(d["quantity"] for d in water_plan),
        "food_planned_kg": sum(d["quantity"] for d in food_plan),
        "water_unmet_l": water_unmet,
        "food_unmet_kg": food_unmet,
        "critical_unmet": any(
            inc.severity_score >= CRITICAL_THRESHOLD and water_demand[j] > 0
            for j, inc in enumerate(incidents)
        ) and water_unmet > 0,
    }
