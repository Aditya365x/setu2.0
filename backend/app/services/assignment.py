"""§6.5 — optimal assignment. The differentiator.

Greedy nearest-first is what every other team builds. It is locally sensible
and globally poor: early greedy choices strand the last incident with a
90-minute drive. The Hungarian algorithm minimises total weighted response time
in O(n^3), which is well under a second at district scale.

Both strategies are implemented here and both are persisted every cycle, so the
dashboard toggle reads two real stored plans rather than re-simulating one.
"""

import math
from datetime import datetime, timezone
from typing import Sequence

import numpy as np
from scipy.optimize import linear_sum_assignment

from ..config import settings
from .scoring import REQUIRED_CAPS
from .types import IncidentView, ResourceView

# Soft-infinity. Using a large finite number rather than np.inf keeps the
# Hungarian solver well-conditioned; anything at or above BIG/2 after the soft
# adjustments is treated as forbidden.
BIG = 1e6
FORBIDDEN = BIG / 2

# Only these states may enter the solver's candidate pool. Enforced in SQL too
# (§5.4) — every live-demo dispatch bug traces back to violating this.
AVAILABLE_STATES = ("idle", "returning")

# How much response time we are willing to trade to send a unit that can carry
# everyone in one trip rather than one that will need several.
#
# Capacity is deliberately NOT a hard constraint. It used to be, and that was
# wrong in the way that matters most: an incident with 80 people trapped in a
# flood found that every boat (30) and every rescue team (40) failed
# `people > capacity`, so the solver forbade every pairing and dispatched
# NOBODY. A boat that holds 30 can start rescuing 80 people — it makes trips,
# or you send three boats. "No single unit can carry everyone in one go" is
# never a reason to send no one.
#
# So capacity became a preference: bigger units are pulled toward bigger
# incidents, and `units_required` below tells the operator how many are
# actually needed. Capability stays hard, because it is genuinely binary — a
# supply truck cannot perform a water rescue at any capacity.
CAPACITY_SHORTFALL_PENALTY_MIN = 20.0


def required_caps(incident: IncidentView) -> set[str]:
    return REQUIRED_CAPS.get(incident.hazard_type, set())


def units_required(incident: IncidentView, resource: ResourceView) -> int:
    """How many of this resource it takes to clear the incident in one wave.

    Surfaced to the operator so the plan is honest about scale: one boat
    dispatched against 80 people is a start, not a solution, and the dashboard
    should say so rather than implying the incident is handled.
    """
    people = max(0, incident.people_affected_est or 0)
    capacity = max(1, resource.capacity or 1)
    return max(1, math.ceil(people / capacity))


def build_cost_matrix(
    resources: Sequence[ResourceView],
    incidents: Sequence[IncidentView],
    eta: np.ndarray,
    now: datetime | None = None,
) -> np.ndarray:
    """cost[i][j] = cost of sending resource i to incident j, in minutes."""
    now = now or datetime.now(timezone.utc)
    C = eta / 60.0  # minutes — the base cost

    for i, r in enumerate(resources):
        caps = set(r.capabilities)
        for j, inc in enumerate(incidents):
            # ── hard constraints ──────────────────────────────────────────
            if not required_caps(inc).issubset(caps):
                C[i][j] += BIG                      # wrong capability — binary
            if r.status not in AVAILABLE_STATES:
                C[i][j] += BIG                      # not available
            if inc.status in ("resolved", "false_alarm"):
                C[i][j] += BIG

            # ── capacity: a preference, never a veto ──────────────────────
            # Scaled by the FRACTION of people this unit cannot take, so a unit
            # that clears the incident costs nothing extra, one that covers
            # half pays half the penalty, and one that covers a tenth pays
            # nearly all of it. Bounded by CAPACITY_SHORTFALL_PENALTY_MIN so it
            # can reorder candidates without ever exceeding a realistic drive
            # time — being far away must still beat being useless.
            people = max(0, inc.people_affected_est or 0)
            if people > r.capacity:
                unserved = (people - r.capacity) / people
                C[i][j] += CAPACITY_SHORTFALL_PENALTY_MIN * unserved

            # ── soft preferences ──────────────────────────────────────────
            # triage: severe incidents pull resources toward them
            C[i][j] -= inc.severity_score * settings.urgency_weight

            # SLA breach risk dominates raw distance
            if inc.sla_deadline is not None:
                deadline = inc.sla_deadline
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=timezone.utc)
                slack_min = (deadline - now).total_seconds() / 60.0
                if slack_min < eta[i][j] / 60.0:
                    C[i][j] += settings.sla_breach_penalty

            # anti-thrash: never yank a unit off a committed job for a
            # marginal gain. Without this the map thrashes every cycle and
            # the demo looks broken.
            if r.committed_incident_id == inc.id:
                C[i][j] -= settings.commitment_bonus
            elif r.committed_incident_id is not None:
                C[i][j] += settings.reassignment_penalty

            # Keep the scarcest, most capable teams for the hardest jobs.
            # Deliberately small: the cost matrix is denominated in minutes, so
            # a large bias here buys strategic reserve at the price of real
            # response time and makes the optimizer look worse than greedy on
            # the very metric we claim to improve. A tie-break, not a thumb on
            # the scale.
            if r.agency == "NDRF" and inc.severity_score < 40:
                C[i][j] += 8

    return C


def solve(
    resources: Sequence[ResourceView],
    incidents: Sequence[IncidentView],
    eta: np.ndarray,
    strategy: str = "optimized",
) -> list[tuple[int, int]]:
    """Return [(resource_idx, incident_idx)] pairs."""
    if not len(resources) or not len(incidents):
        return []

    C = build_cost_matrix(resources, incidents, eta)

    if strategy == "greedy":
        # The baseline, for the live comparison: serve the most severe
        # incident first with whatever free unit is nearest to it.
        assigns: list[tuple[int, int]] = []
        used: set[int] = set()
        order = sorted(range(len(incidents)), key=lambda j: -incidents[j].severity_score)
        for j in order:
            cand = [i for i in range(len(resources)) if i not in used and C[i][j] < FORBIDDEN]
            if not cand:
                continue
            i = min(cand, key=lambda i: eta[i][j])  # nearest free unit
            assigns.append((i, j))
            used.add(i)
        return assigns

    rows, cols = linear_sum_assignment(C)
    # Pairs the solver was forced into only because the matrix is rectangular
    # are dropped here, which makes "unassigned" explicit and auditable.
    return [(int(i), int(j)) for i, j in zip(rows, cols) if C[i][j] < FORBIDDEN]


def compare(
    opt: Sequence[tuple[int, int]],
    greedy: Sequence[tuple[int, int]],
    eta: np.ndarray,
) -> dict:
    """Like-for-like mean response, over the incidents BOTH strategies serve.

    Comparing each strategy's mean over its own served set is not a comparison,
    it is a selection effect. Greedy strands the incidents nobody can easily
    reach and then averages over the easy remainder; the optimizer takes those
    hard incidents on, and is punished on the mean for doing so. In one measured
    run optimized served 27 incidents to greedy's 24 and still showed a 5.7%
    WORSE mean — which reads on a dashboard as the optimizer losing, when it had
    just rescued three more groups of people.

    The honest number is the mean over the intersection: the same incidents,
    both plans, head to head. Coverage is reported next to it, because the extra
    incidents the optimizer serves are the whole point and must not disappear
    into an average.
    """
    opt_by_incident = {j: i for i, j in opt}
    greedy_by_incident = {j: i for i, j in greedy}
    common = sorted(set(opt_by_incident) & set(greedy_by_incident))

    if not common:
        return {"common_incidents": 0, "mean_common_opt": 0.0, "mean_common_greedy": 0.0}

    o = [eta[opt_by_incident[j]][j] / 60.0 for j in common]
    g = [eta[greedy_by_incident[j]][j] / 60.0 for j in common]
    return {
        "common_incidents": len(common),
        "mean_common_opt": round(float(np.mean(o)), 2),
        "mean_common_greedy": round(float(np.mean(g)), 2),
        # Where the two plans actually disagree. If this is 0 the strategies
        # produced the same answer and any delta is noise.
        "differing_assignments": sum(
            1 for j in common if opt_by_incident[j] != greedy_by_incident[j]
        ),
    }


def evaluate(
    assigns: Sequence[tuple[int, int]],
    incidents: Sequence[IncidentView],
    eta: np.ndarray,
    critical_threshold: float = 70.0,
) -> dict:
    """The numbers behind the metric strip. Computed from the plan actually
    produced — not claimed."""
    etas_min = [eta[i][j] / 60.0 for i, j in assigns]
    served = {j for _, j in assigns}
    unassigned_critical = sum(
        1
        for j, inc in enumerate(incidents)
        if j not in served and inc.severity_score >= critical_threshold
    )
    return {
        # Coverage first. Greedy strands the incidents nobody else can reach and
        # then averages over what is left, so mean response alone understates
        # the difference — sometimes reverses it.
        "assigned": len(assigns),
        "total_response_min": round(float(np.sum(etas_min)), 2) if etas_min else 0.0,
        "mean_response_min": round(float(np.mean(etas_min)), 2) if etas_min else 0.0,
        "worst_case_min": round(float(np.max(etas_min)), 2) if etas_min else 0.0,
        "unassigned_critical": unassigned_critical,
    }
