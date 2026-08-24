"""§6.6 — shelter allocation as min-cost flow.

Assignment (§6.5) handles one-to-one matching. Evacuation is a different
problem: many people from one incident may have to split across several
shelters because the nearest one fills. That is a capacitated transportation
problem, and min-cost flow solves it exactly.

Two properties make this worth the fifty lines. It degrades gracefully — the
nearest shelter filling routes overflow to the second-nearest with no
special-case code. And it surfaces a real operational number: if `unplaced > 0`
the district is short of shelter capacity *right now*, which is an escalation
to the SDMA that no existing system supports.
"""

from typing import Sequence

import networkx as nx
import numpy as np

from .types import IncidentView, ShelterView

# Minutes of detour we are willing to accept to reach a shelter that can
# actually treat casualties. Expressed as an edge weight rather than a rule
# engine, so it composes with everything else the flow is balancing.
NO_MEDICAL_PENALTY = 30


def allocate_shelters(
    incidents: Sequence[IncidentView],
    shelters: Sequence[ShelterView],
    eta_is: np.ndarray,
) -> tuple[list[dict], int]:
    """Return (placements, unplaced_people).

    eta_is is [incidents x shelters] in seconds.
    """
    demand = [max(0, inc.people_affected_est or 0) for inc in incidents]
    total = sum(demand)
    if total == 0:
        return [], 0

    open_shelters = [
        (s_idx, sh) for s_idx, sh in enumerate(shelters) if sh.status == "open" and sh.free > 0
    ]
    if not open_shelters:
        return [], total

    G = nx.DiGraph()

    for k, inc in enumerate(incidents):
        if demand[k] <= 0:
            continue
        G.add_edge("SRC", f"i{k}", capacity=demand[k], weight=0)
        for s_idx, sh in open_shelters:
            w = int(eta_is[k][s_idx] / 60)
            if inc.needs_medical and not sh.has_medical:
                w += NO_MEDICAL_PENALTY
            G.add_edge(f"i{k}", f"s{s_idx}", capacity=sh.free, weight=w)

    for s_idx, sh in open_shelters:
        G.add_edge(f"s{s_idx}", "SNK", capacity=sh.free, weight=0)

    if "SRC" not in G or "SNK" not in G:
        return [], total

    # max_flow_min_cost pushes as many people as the network can take and picks
    # the cheapest way to do it. Capacity shortfall shows up as flow < total
    # rather than as an exception — which is exactly the signal we want.
    flow = nx.max_flow_min_cost(G, "SRC", "SNK")

    placements: list[dict] = []
    placed = 0
    for k, inc in enumerate(incidents):
        for s_idx, sh in open_shelters:
            n = flow.get(f"i{k}", {}).get(f"s{s_idx}", 0)
            if n > 0:
                placements.append(
                    {
                        "incident_id": inc.id,
                        "shelter_id": sh.id,
                        "shelter_name": sh.name,
                        "people": int(n),
                        "eta_seconds": int(eta_is[k][s_idx]),
                    }
                )
                placed += n

    return placements, int(total - placed)
