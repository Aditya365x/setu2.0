"""§6.4 — travel-time cost matrix.

Straight-line distance is wrong precisely when it matters: during a flood the
road network is what constrains movement, and a 2 km straight line can be a
25 km detour around a submerged causeway.

Two providers behind one interface, selected by env var. OSRM is the real
answer; haversine is the documented degradation path (§11.2) and therefore also
a legitimate default while the OSM extract is being prepared. Either way the
solver above this layer is unchanged.
"""

import math
from typing import Protocol, Sequence

import httpx
import numpy as np

from ..config import settings
from .types import IncidentView, ResourceView

EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


class Router(Protocol):
    degraded: bool

    async def matrix(
        self, origins: Sequence, destinations: Sequence
    ) -> np.ndarray: ...


def _points(items: Sequence) -> list[tuple[float, float]]:
    out = []
    for it in items:
        out.append((it.lat, it.lng))
    return out


class HaversineRouter:
    """Great-circle distance / 8.33 m/s (~30 km/h effective disaster speed).

    Deliberately pessimistic and deliberately simple. It never fails, never
    needs the network, and produces a matrix with the same shape and units as
    OSRM — so swapping providers changes nothing downstream.
    """

    degraded = True

    async def matrix(self, origins: Sequence, destinations: Sequence) -> np.ndarray:
        o, d = _points(origins), _points(destinations)
        M = np.zeros((len(o), len(d)), dtype=float)
        for i, (olat, olng) in enumerate(o):
            for j, (dlat, dlng) in enumerate(d):
                M[i][j] = haversine_m(olat, olng, dlat, dlng) / settings.fallback_speed_mps
        return M


class OsrmRouter:
    """One /table call returns the full matrix. Falls back rather than failing:
    OSRM being down must never stop dispatch (§11.2)."""

    degraded = False

    def __init__(self) -> None:
        self._fallback = HaversineRouter()

    async def matrix(self, origins: Sequence, destinations: Sequence) -> np.ndarray:
        o, d = _points(origins), _points(destinations)
        coords = ";".join(f"{lng},{lat}" for lat, lng in list(o) + list(d))
        n_o = len(o)
        src = ";".join(str(i) for i in range(n_o))
        dst = ";".join(str(i) for i in range(n_o, n_o + len(d)))
        url = f"{settings.osrm_url}/table/v1/driving/{coords}?sources={src}&destinations={dst}"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                M = np.array(resp.json()["durations"], dtype=float)
        except Exception:
            self.degraded = True
            return await self._fallback.matrix(origins, destinations)

        # Unroutable pairs come back as null; substitute the fallback rather
        # than dropping the pair, which would silently hide an incident.
        if np.isnan(M).any():
            self.degraded = True
            fb = await self._fallback.matrix(origins, destinations)
            M = np.where(np.isnan(M), fb, M)
        else:
            self.degraded = False
        return M


def get_router() -> Router:
    return OsrmRouter() if settings.routing_provider == "osrm" else HaversineRouter()


async def eta_matrix(
    resources: Sequence[ResourceView], incidents: Sequence[IncidentView]
) -> tuple[np.ndarray, bool]:
    """Return ([resources x incidents] seconds, degraded?)."""
    router = get_router()
    M = await router.matrix(resources, incidents)
    return M, router.degraded
