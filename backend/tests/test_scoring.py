"""§6.2 severity and §6.3 trust — deterministic, explainable, defensible."""

from app.services.scoring import (
    IncidentScoringInput,
    ReportTrustInput,
    is_quarantined,
    severity_score,
    trust_score,
)


def _inc(**kw) -> IncidentScoringInput:
    base = dict(hazard_type="flood", mean_severity=3.0, mean_trust=0.7, report_count=1)
    base.update(kw)
    return IncidentScoringInput(**base)


def test_score_always_ships_its_breakdown():
    """A black box that outputs 87 is useless to a Collector who has to
    justify the decision."""
    score, parts = severity_score(_inc())
    assert 0 <= score <= 100
    for term in ("reported", "corroboration", "hazard", "population", "official"):
        assert term in parts


def test_corroboration_is_capped_against_a_forwarded_flood():
    """The single most important defence against panic-forwarding dominating
    triage: 200 reports must not score 200x one report."""
    one = severity_score(_inc(report_count=1))[1]["corroboration"]
    ten = severity_score(_inc(report_count=10))[1]["corroboration"]
    two_hundred = severity_score(_inc(report_count=200))[1]["corroboration"]

    assert one < ten <= two_hundred
    assert two_hundred <= 1.0
    assert two_hundred - ten < ten - one  # sharply diminishing returns


def test_population_term_corrects_the_connectivity_bias():
    """Two identical incidents; the one with more people exposed outranks the
    one with more reports. This is the equity argument, in code."""
    quiet_dense = severity_score(
        _inc(report_count=2, pop_density_1km=9000, district_p95_density=10000)
    )[0]
    loud_sparse = severity_score(
        _inc(report_count=8, pop_density_1km=200, district_p95_density=10000)
    )[0]
    assert quiet_dense > loud_sparse


def test_life_threat_incidents_get_a_hard_floor():
    score, parts = severity_score(
        _inc(hazard_type="building_collapse", mean_severity=2.0, mean_trust=0.4, people_reported=3)
    )
    assert score >= 85.0
    assert "life_threat_floor" in parts["escalations"]


def test_ageing_prevents_starvation():
    fresh = severity_score(_inc(age_minutes=5))[0]
    stale = severity_score(_inc(age_minutes=105))[0]
    assert stale > fresh


def test_official_cap_overlap_boosts_and_is_flagged():
    without = severity_score(_inc())[0]
    score, parts = severity_score(_inc(active_alert_severity="Extreme"))
    assert score > without
    assert "inside_extreme_cap_polygon" in parts["escalations"]


def test_field_unit_reports_are_authoritative():
    score, _ = trust_score(ReportTrustInput(source="field_unit"))
    assert score == 1.0


def test_pincode_only_report_is_trusted_less_but_never_dropped():
    sms = trust_score(ReportTrustInput(source="sms", gps_accuracy_m=3000))[0]
    app = trust_score(ReportTrustInput(source="app", gps_accuracy_m=12, has_photo=True))[0]
    assert sms < app
    assert sms > 0.0, "a low-precision report still enters the queue"


def test_rate_signature_quarantines_a_spammer():
    spam = trust_score(
        ReportTrustInput(source="app", gps_accuracy_m=3000, reporter_reports_last_10min=40)
    )[0]
    assert is_quarantined(spam)


def test_corroboration_lifts_an_unremarkable_report():
    alone = trust_score(ReportTrustInput(source="app", gps_accuracy_m=40))[0]
    backed = trust_score(
        ReportTrustInput(source="app", gps_accuracy_m=40, distinct_corroborators=4)
    )[0]
    assert backed > alone
