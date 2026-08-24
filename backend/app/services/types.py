"""Plain value objects the solver operates on.

The intelligence layer never touches the ORM: it takes these, returns indices.
That is what makes §6 unit-testable without a database, which matters because
`test_hungarian_beats_greedy` is the test that protects the central claim.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class ResourceView:
    id: int
    name: str
    type: str
    agency: str
    lat: float
    lng: float
    capabilities: set[str] = field(default_factory=set)
    capacity: int = 1
    status: str = "idle"
    committed_incident_id: Optional[int] = None


@dataclass
class IncidentView:
    id: int
    hazard_type: str
    lat: float
    lng: float
    severity_score: float = 0.0
    people_affected_est: int = 0
    status: str = "open"
    needs_medical: bool = False
    sla_deadline: Optional[datetime] = None


@dataclass
class ShelterView:
    id: int
    name: str
    lat: float
    lng: float
    capacity_total: int
    occupancy: int = 0
    has_medical: bool = False
    status: str = "open"

    @property
    def free(self) -> int:
        return max(0, self.capacity_total - self.occupancy)
