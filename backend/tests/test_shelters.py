"""§6.6 — capacity-aware evacuation routing."""

import numpy as np

from app.services.shelters import allocate_shelters
from app.services.types import IncidentView, ShelterView


def _incident(iid: int, people: int, needs_medical: bool = False) -> IncidentView:
    return IncidentView(
        id=iid, hazard_type="flood", lat=19.31, lng=84.79,
        severity_score=80.0, people_affected_est=people, needs_medical=needs_medical,
    )


def test_overflow_routes_to_the_next_cheapest_shelter():
    """The nearest shelter fills; the remainder must route onward rather than
    being turned away at the gate — with no special-case code."""
    incident = _incident(1, people=30)
    near = ShelterView(id=1, name="Near", lat=19.31, lng=84.79, capacity_total=20, occupancy=0)
    far = ShelterView(id=2, name="Far", lat=19.40, lng=84.90, capacity_total=100, occupancy=0)

    eta = np.array([[5 * 60.0, 25 * 60.0]])  # near is genuinely nearer
    plan, unplaced = allocate_shelters([incident], [near, far], eta)

    by_shelter = {p["shelter_id"]: p["people"] for p in plan}
    assert by_shelter == {1: 20, 2: 10}
    assert unplaced == 0


def test_district_wide_shortfall_is_surfaced_not_swallowed():
    """unplaced > 0 is the escalation signal to the SDMA — the number no
    existing system produces."""
    incident = _incident(1, people=500)
    only = ShelterView(id=1, name="Only", lat=19.31, lng=84.79, capacity_total=100, occupancy=40)

    eta = np.array([[5 * 60.0]])
    plan, unplaced = allocate_shelters([incident], [only], eta)

    assert sum(p["people"] for p in plan) == 60  # free beds, not total capacity
    assert unplaced == 440


def test_placement_never_exceeds_free_beds():
    incidents = [_incident(1, 50), _incident(2, 50)]
    shelters = [
        ShelterView(id=1, name="A", lat=19.31, lng=84.79, capacity_total=30, occupancy=10),
        ShelterView(id=2, name="B", lat=19.33, lng=84.81, capacity_total=40, occupancy=0),
    ]
    eta = np.array([[300.0, 900.0], [800.0, 320.0]])
    plan, unplaced = allocate_shelters(incidents, shelters, eta)

    placed = {}
    for p in plan:
        placed[p["shelter_id"]] = placed.get(p["shelter_id"], 0) + p["people"]
    assert placed.get(1, 0) <= 20
    assert placed.get(2, 0) <= 40
    assert sum(placed.values()) + unplaced == 100


def test_medical_need_prefers_an_equipped_shelter():
    """Expressed as an edge weight, not a rule engine — so it composes with
    everything else the flow is balancing."""
    incident = _incident(1, people=10, needs_medical=True)
    plain = ShelterView(id=1, name="Plain", lat=19.31, lng=84.79, capacity_total=50)
    medical = ShelterView(
        id=2, name="Medical", lat=19.35, lng=84.85, capacity_total=50, has_medical=True
    )

    # The medical shelter is 10 minutes further, well inside the 30-minute
    # detour we are willing to accept for casualties.
    eta = np.array([[5 * 60.0, 15 * 60.0]])
    plan, unplaced = allocate_shelters([incident], [plain, medical], eta)

    assert unplaced == 0
    assert [p["shelter_id"] for p in plan] == [2]


def test_inaccessible_shelter_leaves_the_network_immediately():
    incident = _incident(1, people=10)
    closed = ShelterView(
        id=1, name="Cut off", lat=19.31, lng=84.79,
        capacity_total=100, status="inaccessible",
    )
    eta = np.array([[300.0]])
    plan, unplaced = allocate_shelters([incident], [closed], eta)
    assert plan == []
    assert unplaced == 10
