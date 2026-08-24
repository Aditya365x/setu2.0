"""§6.2 severity scoring and §6.3 trust scoring.

Both are deterministic and both are explainable. A black box that outputs 87 is
useless to a Collector who has to justify a decision in a post-event review, so
every score returns its component breakdown and that breakdown is persisted and
rendered on screen.

No model is trained here. These are weighted formulas with defensible terms —
which is the correct choice for a life-safety system and the honest answer to
"where is the AI?" (§16).
"""

import math
from dataclasses import dataclass
from typing import Optional

from ..config import settings

# ── §6.2 ──────────────────────────────────────────────────────────────────
WEIGHTS = {
    "reported": 0.35,       # what people on the ground actually said
    "corroboration": 0.20,  # independent confirmation
    "hazard": 0.15,         # intrinsic lethality of the hazard
    "population": 0.15,     # who is exposed
    "official": 0.15,       # inside an active CAP alert polygon
}

# 0..1, from NDMA fatality profiles.
HAZARD_WEIGHT = {
    "building_collapse": 1.00,
    "landslide": 0.95,
    "medical": 0.90,
    "fire": 0.85,
    "stranded": 0.80,
    "flood": 0.70,
    "power_line": 0.60,
    "cyclone_damage": 0.55,
    "other": 0.40,
}

CAP_SEVERITY = {"Extreme": 1.0, "Severe": 0.75, "Moderate": 0.45, "Minor": 0.2}

# §26 — vulnerable people present. Points per category, and a hard ceiling so
# the term tie-breaks rather than dominates. Four categories at 3 points each
# would be 12; the cap holds it to 8, which is just under the gap between
# severity bands and therefore cannot leapfrog a whole band on its own.
VULNERABILITY_WEIGHT = 3.0
VULNERABILITY_CAP = 8.0

# Which capabilities an incident demands. Drives the hard constraints in §6.5.
REQUIRED_CAPS = {
    "flood": {"water_rescue"},
    "stranded": {"water_rescue"},
    "medical": {"medical"},
    "building_collapse": {"cutting"},
    "landslide": {"cutting"},
    "fire": {"fire"},
    "cyclone_damage": set(),
    "power_line": set(),
    "other": set(),
}


@dataclass
class IncidentScoringInput:
    hazard_type: str
    mean_severity: float          # 1..5, mean of clustered reports
    mean_trust: float             # 0..1
    report_count: int
    pop_density_1km: float = 0.0
    district_p95_density: float = 1.0
    active_alert_severity: Optional[str] = None
    people_reported: int = 0
    age_minutes: float = 0.0
    status: str = "open"
    # §6/§26 — who is affected, not just how many.
    has_children: bool = False
    has_elderly: bool = False
    has_injured: bool = False
    has_disabled: bool = False


def severity_score(inc: IncidentScoringInput) -> tuple[float, dict]:
    # 1. mean reported severity, trust-weighted — unverifiable input counts
    #    less, never zero.
    reported = (inc.mean_severity / 5.0) * inc.mean_trust

    # 2. corroboration, log-scaled and capped so a brigade of forwarded
    #    messages cannot dominate a genuine single report. This is the single
    #    most important defence against a panicked report flood skewing triage.
    corroboration = min(math.log1p(inc.report_count) / math.log(11), 1.0)

    # 3. intrinsic hazard lethality
    hazard = HAZARD_WEIGHT.get(inc.hazard_type, 0.40)

    # 4. the equity term. Report volume tracks phone density, not need — this
    #    is what stops relief concentrating in accessible, connected wards.
    denom = inc.district_p95_density or 1.0
    population = min(inc.pop_density_1km / denom, 1.0)

    # 5. official corroboration: does IMD/CWC agree this area is in trouble?
    official = CAP_SEVERITY.get(inc.active_alert_severity or "", 0.0)

    parts = {
        "reported": round(reported, 3),
        "corroboration": round(corroboration, 3),
        "hazard": round(hazard, 3),
        "population": round(population, 3),
        "official": round(official, 3),
    }
    score = 100.0 * sum(WEIGHTS[k] * v for k, v in parts.items())

    # ── hard escalations: override the weighted score ─────────────────────
    escalations = []
    if inc.hazard_type in ("medical", "building_collapse") and inc.people_reported:
        if score < 85.0:
            escalations.append("life_threat_floor")
        score = max(score, 85.0)
    if inc.active_alert_severity == "Extreme":
        escalations.append("inside_extreme_cap_polygon")

    # §26 — vulnerable people present. Deliberately an escalation and not a
    # sixth weighted term: the five weights sum to exactly 1.0, which is what
    # keeps the score on a defensible 0-100 scale, and adding a term would
    # silently rescale every incident already on the board.
    #
    # Bounded at +VULNERABILITY_CAP so it can break a tie and lift a marginal
    # incident over a threshold, but can never outrank hazard lethality. A
    # child trapped in a flood should outrank an adult trapped in the same
    # flood; it should not outrank a building collapse.
    vulnerable = [
        name
        for name, present in (
            ("children", inc.has_children),
            ("elderly", inc.has_elderly),
            ("injured", inc.has_injured),
            ("disabled", inc.has_disabled),
        )
        if present
    ]
    if vulnerable:
        bonus = min(VULNERABILITY_WEIGHT * len(vulnerable), VULNERABILITY_CAP)
        score = min(score + bonus, 100.0)
        escalations.append(f"vulnerable:{'+'.join(vulnerable)}")
    parts["vulnerable"] = vulnerable

    # Ageing prevents starvation: a low-severity incident cannot sit unserved
    # forever while newer high-severity ones keep pre-empting it.
    if inc.age_minutes > 45 and inc.status == "open":
        score = min(score + 0.25 * (inc.age_minutes - 45), 100.0)
        escalations.append("aged_over_45min")

    parts["escalations"] = escalations
    return round(score, 2), parts


# ── §6.3 ──────────────────────────────────────────────────────────────────
@dataclass
class ReportTrustInput:
    source: str
    gps_accuracy_m: Optional[int] = None
    has_photo: bool = False
    photo_exif_within_30min: bool = False
    distinct_corroborators: int = 0
    reporter_false_alarms: int = 0
    reporter_confirmed_reports: int = 0
    reporter_reports_last_10min: int = 0


def trust_score(r: ReportTrustInput) -> tuple[float, dict]:
    """Reports are never auto-rejected. Below the threshold they are
    quarantined into a visible operator queue and excluded from automatic
    dispatch — never silently dropped. Someone in the water may be typing
    badly."""
    s = 0.5  # neutral prior
    parts: dict[str, float] = {}

    def add(key: str, delta: float) -> None:
        nonlocal s
        s += delta
        parts[key] = round(delta, 3)

    # A field unit is authoritative by definition; short-circuit.
    if r.source == "field_unit":
        return 1.0, {"field_unit": 1.0}

    # provenance
    if r.gps_accuracy_m is not None:
        if r.gps_accuracy_m <= 50:
            add("gps_precise", 0.15)
        elif r.gps_accuracy_m > 1000:
            add("gps_pincode_only", -0.10)
    if r.has_photo:
        add("photo", 0.15)
    if r.photo_exif_within_30min:
        add("photo_fresh", 0.10)

    # independent corroboration within 300 m / 20 min, distinct reporters
    if r.distinct_corroborators:
        add("corroborated", min(0.05 * r.distinct_corroborators, 0.20))

    # reporter history
    if r.reporter_false_alarms >= 2:
        add("history_false_alarms", -0.25)
    if r.reporter_confirmed_reports >= 3:
        add("history_reliable", 0.10)

    # rate-limit signature: same hash, many reports, tiny window
    if r.reporter_reports_last_10min > 5:
        add("rate_signature", -0.30)

    return round(max(0.0, min(1.0, s)), 3), parts


def is_quarantined(trust: float) -> bool:
    return trust < settings.trust_quarantine_threshold
