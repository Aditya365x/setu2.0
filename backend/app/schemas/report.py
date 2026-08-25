"""Request/response contracts. §7.2 holds the representative payloads."""

from typing import Optional

from pydantic import BaseModel


class ReportAccepted(BaseModel):
    report_id: str
    reference_code: str
    status: str
    queued_offline: bool = False
    # Which district the report was actually filed under, resolved from its
    # position rather than from server config. Returned so the reporter's app
    # and the operator both know where it landed.
    district_id: Optional[int] = None
    # False means the point sits outside every seeded district boundary and was
    # filed to the nearest one. Surfaced rather than hidden: a report from
    # outside the covered corridor is accepted, but somebody should know.
    in_district: Optional[bool] = None


class SeverityBreakdown(BaseModel):
    reported: float
    corroboration: float
    hazard: float
    population: float
    official: float
    escalations: list[str] = []


class IncidentSummary(BaseModel):
    id: int
    hazard_type: str
    status: str
    lat: float
    lng: float
    severity_score: float
    report_count: int
    people_affected_est: int
    sla_deadline: Optional[str] = None
    needs_medical: bool = False


class Metrics(BaseModel):
    """The dashboard strip. Every number here is computed by the running
    system, not asserted on a slide."""

    open_incidents: int
    critical_unassigned: int
    units_free: int
    units_committed: int
    mean_response_min: dict[str, float]
    worst_case_min: dict[str, float]
    # The like-for-like number: mean over incidents BOTH strategies served.
    # The per-strategy means above are not comparable to each other.
    mean_common_min: dict[str, float] = {"optimized": 0.0, "greedy": 0.0}
    common_incidents: int = 0
    # Coverage. Reported next to mean response because greedy leaves the hard
    # incidents unassigned, so mean alone is not a like-for-like comparison.
    incidents_served: dict[str, int] = {"optimized": 0, "greedy": 0}
    people_evacuated: int
    shelter_occupancy_pct: float
    shelter_shortfall: int
    quarantined: int
    degraded_eta: bool = False
    last_cycle_ms: Optional[int] = None

    # §32 — the situational summary. Counts by severity band answer "how bad is
    # it right now" in a way a single open-incident total cannot: 24 incidents
    # of which 6 are critical is a different shift than 24 of which none are.
    incidents_critical: int = 0
    incidents_high: int = 0
    incidents_medium: int = 0
    incidents_low: int = 0
    shelters_open: int = 0
    shelter_capacity_available: int = 0
    pending_allocations: int = 0
