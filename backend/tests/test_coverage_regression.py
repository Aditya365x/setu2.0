"""Regressions for two bugs the first end-to-end run exposed.

Both were the kind that produce a plausible-looking dashboard and a wrong pitch,
which is worse than a crash.
"""

import numpy as np

from app.services.assignment import evaluate, solve, units_required
from app.services.types import IncidentView, ResourceView


def _boat(rid: int, lat: float = 19.31, capacity: int = 30) -> ResourceView:
    return ResourceView(
        id=rid, name=f"Boat {rid}", type="boat", agency="ODRAF",
        lat=lat, lng=84.79, capabilities={"water_rescue"}, capacity=capacity,
    )


def _flood(iid: int, severity: float, people: int = 10) -> IncidentView:
    return IncidentView(
        id=iid, hazard_type="flood", lat=19.31, lng=84.79,
        severity_score=severity, people_affected_est=people,
    )


def test_evaluate_reports_coverage_not_just_mean():
    """Mean response over each strategy's own served set is not a like-for-like
    comparison: greedy strands the hard incidents and averages over what is
    left. Coverage has to travel alongside it."""
    resources = [_boat(1), _boat(2)]
    incidents = [_flood(1, 90.0), _flood(2, 40.0)]
    eta = np.array([[5 * 60.0, 8 * 60.0], [6 * 60.0, 45 * 60.0]])

    result = evaluate(solve(resources, incidents, eta.copy()), incidents, eta)
    assert result["assigned"] == 2
    assert "total_response_min" in result
    assert result["total_response_min"] >= result["mean_response_min"]


def test_hungarian_covers_at_least_as_many_incidents_as_greedy():
    """The real operational advantage: greedy spends a scarce unit on an early
    incident and then has nothing left for one only that unit could have
    reached. Hungarian never serves fewer."""
    # Three incidents, three boats, but the boats are spread out so greedy's
    # severity-first grab strands the last incident.
    resources = [_boat(1, lat=19.31), _boat(2, lat=19.40), _boat(3, lat=19.90)]
    incidents = [_flood(1, 95.0), _flood(2, 80.0), _flood(3, 60.0)]

    eta = np.array([
        [4 * 60.0, 6 * 60.0, 70 * 60.0],
        [5 * 60.0, 7 * 60.0, 65 * 60.0],
        [90 * 60.0, 95 * 60.0, 8 * 60.0],
    ])

    greedy = evaluate(solve(resources, incidents, eta.copy(), "greedy"), incidents, eta)
    optimized = evaluate(solve(resources, incidents, eta.copy(), "optimized"), incidents, eta)

    assert optimized["assigned"] >= greedy["assigned"]
    assert optimized["total_response_min"] <= greedy["total_response_min"]


def test_double_counted_people_are_visible_as_an_absurd_unit_count():
    """Guards the people-estimate bug from the other side.

    `people_affected_est` is MAX across the clustered reports, not SUM — ten
    witnesses describing the same collapsed house is 3 people, not 30.

    This test used to detect double-counting by asserting that an inflated
    incident became UNASSIGNABLE. That detector is gone on purpose: refusing to
    dispatch was itself the bug (see test_capacity_never_blocks_dispatch), and
    an inflated incident now gets a boat like any other.

    So the regression is pinned on the quantity that still moves — the number of
    units the plan says it needs. One boat for 24 people is a plausible
    operation; eight boats for the same collapsed house is not, and that is what
    a SUM regression would look like on screen.
    """
    boat = _boat(1, capacity=30)
    eta = np.array([[5 * 60.0]])

    within = _flood(1, 80.0, people=24)
    assert solve([boat], [within], eta.copy()) == [(0, 0)]
    assert units_required(within, boat) == 1

    # The same event, double-counted across ten duplicate reports.
    inflated = _flood(1, 80.0, people=240)
    assert solve([boat], [inflated], eta.copy()) == [(0, 0)], (
        "an inflated estimate must still be dispatched to — never silently dropped"
    )
    assert units_required(inflated, boat) == 8, (
        "a tenfold people estimate must surface as a tenfold unit requirement"
    )


def test_agency_preference_is_a_tiebreak_not_a_distortion():
    """The NDRF reserve preference must never cost more real response time than
    it saves. If it grows large it makes the optimizer lose to greedy on the
    very metric the pitch claims."""
    ndrf = ResourceView(
        id=1, name="NDRF Team 1", type="rescue_team", agency="NDRF",
        lat=19.31, lng=84.79, capabilities={"water_rescue", "cutting"}, capacity=40,
    )
    odraf = _boat(2)
    minor = _flood(1, 20.0)

    # NDRF is 10 minutes closer. The preference should not be strong enough to
    # send the far unit instead.
    eta = np.array([[5 * 60.0], [15 * 60.0]])
    assert solve([ndrf, odraf], [minor], eta) == [(0, 0)]
