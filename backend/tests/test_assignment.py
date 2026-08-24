"""The tests that protect the central claim (§C).

`test_hungarian_beats_greedy` is written first and deliberately: if this ever
fails, the pitch is wrong, not just the code.
"""

import numpy as np
import pytest

from app.services.assignment import build_cost_matrix, evaluate, solve
from app.services.types import IncidentView, ResourceView


def _boat(rid: int, name: str) -> ResourceView:
    return ResourceView(
        id=rid, name=name, type="boat", agency="ODRAF",
        lat=19.31, lng=84.79, capabilities={"water_rescue"}, capacity=30,
    )


def _incident(iid: int, severity: float) -> IncidentView:
    return IncidentView(
        id=iid, hazard_type="flood", lat=19.31, lng=84.79,
        severity_score=severity, people_affected_est=10,
    )


def test_hungarian_beats_greedy():
    """The worked example from §6.5, verbatim.

    Boat A is 5 min from Incident 1 and 8 min from Incident 2.
    Boat B is 6 min from Incident 1 but 45 min from Incident 2.

    Greedy serves the more severe incident first, takes Boat A for it (5 min),
    and strands Boat B with a 45-minute drive: 50 minutes total.
    Hungarian gives up one minute on the first incident to save 37 overall: 14.
    """
    resources = [_boat(1, "Boat A"), _boat(2, "Boat B")]
    incidents = [_incident(1, 90.0), _incident(2, 40.0)]  # 1 is more severe

    eta = np.array([
        [5 * 60.0, 8 * 60.0],    # Boat A
        [6 * 60.0, 45 * 60.0],   # Boat B
    ])

    greedy = solve(resources, incidents, eta.copy(), strategy="greedy")
    optimized = solve(resources, incidents, eta.copy(), strategy="optimized")

    greedy_total = sum(eta[i][j] for i, j in greedy) / 60.0
    optimized_total = sum(eta[i][j] for i, j in optimized) / 60.0

    assert greedy_total == pytest.approx(50.0)
    assert optimized_total == pytest.approx(14.0)
    assert optimized_total < greedy_total

    # And the metric strip must show the improvement, not just the plan.
    g = evaluate(greedy, incidents, eta)
    o = evaluate(optimized, incidents, eta)
    assert o["mean_response_min"] < g["mean_response_min"]
    assert o["worst_case_min"] < g["worst_case_min"]


def test_capability_constraint_is_hard():
    """No assignment ever pairs a resource with an incident it lacks the
    capability for — however close it happens to be parked."""
    truck = ResourceView(
        id=9, name="Supply Truck 1", type="supply_truck", agency="Fire",
        lat=19.31, lng=84.79, capabilities={"supply"}, capacity=100,
    )
    collapse = IncidentView(
        id=5, hazard_type="building_collapse", lat=19.311, lng=84.791,
        severity_score=95.0, people_affected_est=3,
    )
    eta = np.array([[60.0]])  # one minute away — and still ineligible
    assert solve([truck], [collapse], eta) == []


def test_capacity_constraint_is_hard():
    small = ResourceView(
        id=3, name="Boat Small", type="boat", agency="ODRAF",
        lat=19.31, lng=84.79, capabilities={"water_rescue"}, capacity=4,
    )
    big_incident = IncidentView(
        id=7, hazard_type="flood", lat=19.32, lng=84.80,
        severity_score=80.0, people_affected_est=40,
    )
    eta = np.array([[300.0]])
    assert solve([small], [big_incident], eta) == []


def test_busy_units_never_enter_the_pool():
    """§5.4 invariant: a resource enroute or onsite can never be dispatched
    again. Enforced in SQL and re-asserted here."""
    busy = _boat(4, "Boat Busy")
    busy.status = "enroute"
    eta = np.array([[300.0]])
    assert solve([busy], [_incident(1, 90.0)], eta) == []


def test_commitment_locking_survives_reoptimization():
    """A committed, en-route pairing is a fixed input to every later run.
    Without this the map thrashes and the demo looks broken."""
    committed = _boat(1, "Boat A")
    committed.committed_incident_id = 1
    free = _boat(2, "Boat B")

    incidents = [_incident(1, 90.0)]
    # Boat B is nearer, but Boat A is already on the job.
    eta = np.array([[20 * 60.0], [3 * 60.0]])

    C = build_cost_matrix([committed, free], incidents, eta.copy())
    assert C[0][0] < C[1][0], "the committed unit must remain the cheapest option"

    assigns = solve([committed, free], incidents, eta.copy())
    assert assigns == [(0, 0)]


def test_severity_pulls_scarce_units_toward_the_worst_incident():
    """With one unit and two reachable incidents, the severe one wins."""
    boat = _boat(1, "Boat A")
    minor = _incident(1, 10.0)
    severe = _incident(2, 95.0)
    eta = np.array([[10 * 60.0, 12 * 60.0]])  # severe is slightly further

    assigns = solve([boat], [minor, severe], eta)
    assert assigns == [(0, 1)]
