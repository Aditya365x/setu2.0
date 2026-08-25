"""Per-district situational picture: what is happening here, right now.

Three layers, and they answer three different questions:

1. **Weather now** — temperature, wind, rain at the district centroid. External,
   live, and the only part that needs the internet.
2. **Official alerts** — the CAP polygons from NDMA SACHET that actually cover
   this district. Authoritative, and already in our database.
3. **What is actually happening** — live incidents in this district grouped by
   hazard. This is the layer nobody else has: not a forecast of what might
   happen, but a count of who has asked for help in the last hour and for what.

The third layer is the point. IMD forecasts better than we ever could, and
Google's flood models are better than anything we would build. Neither of them
knows that eleven people in Amalapuram are on a roof right now, because nobody
is collecting that. SETU is.

## Offline behaviour

`OFFLINE_MODE=true` — the demo default — makes zero outbound requests. Layers 2
and 3 still work because they come from our own database, and the weather block
returns `available: false` rather than an empty object pretending to be data. A
panel that silently shows nothing is worse than one that says it does not know.

When online, results are cached in Redis for CACHE_SECONDS and the last good
response is kept indefinitely as a fallback, so a flaky connection degrades to a
staleness label rather than a blank card. Same pattern as the CAP poller.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..bus import redis
from ..config import settings

# Endpoint is configurable so a deployment can point at a different
# provider (or an internal mirror) without touching this module.
OPEN_METEO = settings.conditions_endpoint

# Weather does not change meaningfully in fifteen minutes, and a dashboard that
# is open all day should not hammer a free API.
CACHE_SECONDS = 900

# Beyond this the cached reading is labelled stale rather than presented as
# current. An hour-old wind speed during a landfall is actively misleading.
STALE_AFTER_MINUTES = 45

# ── hazard outlook ────────────────────────────────────────────────────────
#
# Not a forecast and not a model. This translates published IMD rainfall and
# wind classes, plus terrain, into a per-hazard statement an operator can argue
# with: "landslide LIKELY because 212 mm fell here in 72 hours and the ground is
# at 927 m". Every level names its reasons, the same discipline as the severity
# breakdown, because a number nobody can interrogate is a number nobody should
# act on.
#
# IMD forecasts weather far better than we ever could, and Google's flood models
# are better than anything we would build. What neither of them does is tell a
# Collector "your district is now in the window where slopes fail". That is a
# translation job, and translation is honest work as long as it is labelled.

# IMD 24-hour rainfall classes.
RAIN_HEAVY_MM = 64.5
RAIN_VERY_HEAVY_MM = 115.5
RAIN_EXTREME_MM = 204.4

# Antecedent rainfall is what actually fails a slope: ground already saturated
# by three days of rain gives way under an amount that would be harmless dry.
# ~200 mm/72h on susceptible terrain is the commonly cited trigger band.
ANTECEDENT_WET_MM = 100.0
ANTECEDENT_SATURATED_MM = 200.0

# IMD wind scale: gale from 62 km/h, storm from 88 km/h.
WIND_GALE_KMH = 62.0
WIND_STORM_KMH = 88.0

# Terrain proxies, from the elevation the weather provider returns for the
# district centroid. Crude — a district centroid is one point and Ganjam spans
# both coast and Eastern Ghats — so it is stated as a proxy, never as a survey.
HILLY_M = 250.0
LOW_LYING_M = 30.0

LEVELS = ["unlikely", "possible", "likely", "imminent"]


def _lift(current: str, to: str) -> str:
    return to if LEVELS.index(to) > LEVELS.index(current) else current


def hazard_outlook(
    weather: dict[str, Any],
    cap_severity: str | None,
    is_coastal: bool,
) -> list[dict[str, Any]]:
    """Per-hazard likelihood with reasons. Empty when we have no weather."""
    if not weather.get("available"):
        return []

    rain_next = float(weather.get("rain_24h_mm") or 0)
    rain_past_72 = float(weather.get("rain_past_72h_mm") or 0)
    gust = float(weather.get("max_gust_24h_kmh") or weather.get("max_wind_24h_kmh") or 0)
    elevation = float(weather.get("elevation_m") or 0)

    out: list[dict[str, Any]] = []

    # ── flood ────────────────────────────────────────────────────────────
    level, why = "unlikely", []
    if rain_next >= RAIN_EXTREME_MM:
        level, _ = "likely", why.append(f"{rain_next:.0f} mm forecast in 24h (extremely heavy)")
    elif rain_next >= RAIN_VERY_HEAVY_MM:
        level = "likely"; why.append(f"{rain_next:.0f} mm forecast in 24h (very heavy)")
    elif rain_next >= RAIN_HEAVY_MM:
        level = "possible"; why.append(f"{rain_next:.0f} mm forecast in 24h (heavy)")
    if rain_past_72 >= ANTECEDENT_SATURATED_MM:
        level = _lift(level, "likely")
        why.append(f"{rain_past_72:.0f} mm already fell in 72h — ground saturated")
    elif rain_past_72 >= ANTECEDENT_WET_MM:
        level = _lift(level, "possible")
        why.append(f"{rain_past_72:.0f} mm in the last 72h")
    if elevation and elevation <= LOW_LYING_M and level != "unlikely":
        why.append(f"low-lying terrain ({elevation:.0f} m) drains slowly")
        level = _lift(level, "likely")
    if cap_severity in ("Extreme", "Severe") and level != "unlikely":
        level = _lift(level, "imminent")
        why.append(f"IMD {cap_severity} alert in force")
    out.append({"hazard": "flood", "level": level, "why": why})

    # ── landslide ────────────────────────────────────────────────────────
    # Only meaningful where there is a slope to fail.
    if elevation >= HILLY_M:
        level, why = "unlikely", [f"hilly terrain ({elevation:.0f} m)"]
        if rain_past_72 >= ANTECEDENT_SATURATED_MM:
            level = "likely"
            why.append(f"{rain_past_72:.0f} mm in 72h — slopes saturated")
        elif rain_past_72 >= ANTECEDENT_WET_MM:
            level = "possible"
            why.append(f"{rain_past_72:.0f} mm in 72h")
        if rain_next >= RAIN_VERY_HEAVY_MM and level != "unlikely":
            level = _lift(level, "imminent")
            why.append(f"{rain_next:.0f} mm more forecast in 24h")
        out.append({"hazard": "landslide", "level": level, "why": why})

    # ── cyclone / wind damage ────────────────────────────────────────────
    level, why = "unlikely", []
    if gust >= WIND_STORM_KMH:
        level = "likely"; why.append(f"gusts {gust:.0f} km/h (IMD storm force)")
    elif gust >= WIND_GALE_KMH:
        level = "possible"; why.append(f"gusts {gust:.0f} km/h (IMD gale force)")
    if cap_severity == "Extreme":
        level = "imminent"; why.append("IMD Extreme alert in force")
    elif cap_severity == "Severe" and level != "unlikely":
        level = _lift(level, "likely"); why.append("IMD Severe alert in force")
    out.append({"hazard": "cyclone_damage", "level": level, "why": why})

    # ── storm surge — coastal districts only ─────────────────────────────
    if is_coastal:
        level, why = "unlikely", []
        if gust >= WIND_STORM_KMH and cap_severity in ("Extreme", "Severe"):
            level = "likely"
            why.append(f"storm-force gusts {gust:.0f} km/h with an active {cap_severity} alert")
        elif gust >= WIND_GALE_KMH:
            level = "possible"
            why.append(f"gale-force gusts {gust:.0f} km/h on an exposed coast")
        if level != "unlikely":
            out.append({"hazard": "storm_surge", "level": level, "why": why})

    return out


def overall_band(outlook: list[dict[str, Any]]) -> str:
    """The single word at the top of the card: the worst hazard on the board."""
    if not outlook:
        return "unknown"
    worst = max((LEVELS.index(h["level"]) for h in outlook), default=0)
    return ["normal", "watch", "warning", "danger"][worst]


async def _fetch_weather(lat: float, lng: float, district_id: int) -> dict[str, Any]:
    """Current conditions plus a 24-hour outlook, cached and fail-soft."""
    key = f"wx:{district_id}"

    if not settings.live_conditions:
        # Explicitly switched off. Say so rather than returning an empty object
        # that looks like a reading of nothing.
        return {"available": False, "reason": "live_conditions_disabled", "source": "none"}

    cached = await redis.get(key)
    if cached:
        data = json.loads(cached)
        age_min = (time.time() - data.get("fetched_at", 0)) / 60.0
        if age_min < CACHE_SECONDS / 60.0:
            data["stale_minutes"] = round(age_min)
            return data

    try:
        params = {
            "latitude": lat, "longitude": lng,
            "current": "temperature_2m,wind_speed_10m,precipitation,weather_code",
            # past_days is the important one: antecedent rainfall is what
            # actually fails a slope or fills a river, and a forecast alone
            # cannot tell you the ground is already saturated.
            "daily": "precipitation_sum,wind_speed_10m_max,wind_gusts_10m_max",
            "past_days": 3,
            "forecast_days": 2,
            "timezone": "Asia/Kolkata",
        }
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(OPEN_METEO, params=params)
            resp.raise_for_status()
            j = resp.json()

        cur = j.get("current", {}) or {}
        daily = j.get("daily", {}) or {}
        data = {
            "available": True,
            "source": "open-meteo",
            "temperature_c": cur.get("temperature_2m"),
            "wind_kmh": cur.get("wind_speed_10m"),
            "precipitation_mm": cur.get("precipitation"),
            "observed_at": cur.get("time"),
            "elevation_m": j.get("elevation"),
            # With past_days=3 the daily arrays run [-3, -2, -1, today, +1].
            "rain_past_72h_mm": round(sum(daily.get("precipitation_sum", [])[:3] or [0]), 1),
            "rain_24h_mm": (daily.get("precipitation_sum") or [0, 0, 0, 0])[3]
            if len(daily.get("precipitation_sum") or []) > 3
            else (daily.get("precipitation_sum") or [0])[0],
            "max_wind_24h_kmh": (daily.get("wind_speed_10m_max") or [0, 0, 0, 0])[3]
            if len(daily.get("wind_speed_10m_max") or []) > 3
            else (daily.get("wind_speed_10m_max") or [0])[0],
            "max_gust_24h_kmh": (daily.get("wind_gusts_10m_max") or [0, 0, 0, 0])[3]
            if len(daily.get("wind_gusts_10m_max") or []) > 3
            else (daily.get("wind_gusts_10m_max") or [0])[0],
            "fetched_at": time.time(),
            "stale_minutes": 0,
        }
        # Keep the last good reading with no TTL: a stale number that is
        # labelled stale beats a blank card during an outage.
        await redis.set(key, json.dumps(data))
        return data

    except Exception:
        if cached:
            data = json.loads(cached)
            data["stale_minutes"] = round((time.time() - data.get("fetched_at", 0)) / 60.0)
            data["degraded"] = True
            return data
        return {"available": False, "reason": "unreachable", "source": "none"}


ACTIVE_HAZARDS_SQL = text(
    """
    SELECT hazard_type::text AS hazard, COUNT(*) AS incidents,
           COALESCE(SUM(people_affected_est), 0) AS people,
           ROUND(MAX(severity_score)::numeric, 1) AS worst_severity
    FROM incidents
    WHERE district_id = :did AND status IN ('open','assigned')
    GROUP BY hazard_type
    ORDER BY COUNT(*) DESC
    """
)

DISTRICT_ALERTS_SQL = text(
    """
    SELECT event, severity, urgency, certainty, headline, instruction,
           source_agency, effective_from, expires_at
    FROM alerts
    WHERE district_id = :did AND expires_at > now()
    ORDER BY CASE severity
                 WHEN 'Extreme'  THEN 4 WHEN 'Severe' THEN 3
                 WHEN 'Moderate' THEN 2 WHEN 'Minor'  THEN 1 ELSE 0 END DESC
    """
)


async def district_conditions(session: AsyncSession, district_id: int) -> dict[str, Any]:
    """Everything happening in one district, right now."""
    district = (
        await session.execute(
            text(
                "SELECT id, name, state, ST_Y(centroid::geometry) AS lat, "
                "ST_X(centroid::geometry) AS lng FROM districts WHERE id = :did"
            ),
            {"did": district_id},
        )
    ).mappings().one_or_none()
    if not district:
        return {"error": "unknown district"}

    weather = await _fetch_weather(district["lat"], district["lng"], district_id)

    alerts = [
        dict(a)
        for a in (
            await session.execute(DISTRICT_ALERTS_SQL, {"did": district_id})
        ).mappings().all()
    ]
    hazards = [
        dict(h)
        for h in (
            await session.execute(ACTIVE_HAZARDS_SQL, {"did": district_id})
        ).mappings().all()
    ]

    # Every district in this deployment fronts the Bay of Bengal — that was the
    # selection criterion for the corridor — so surge is in scope for all of
    # them. Stated as a constant rather than derived from a proxy query,
    # because a fake computation is worse than an honest assumption: if this
    # ever seeds an inland district, the assumption is visible here and wrong
    # in one obvious place instead of silently wrong in a heuristic.
    is_coastal = True
    outlook = hazard_outlook(
        weather, alerts[0]["severity"] if alerts else None, is_coastal
    )
    band = overall_band(outlook)

    return {
        "district_id": district["id"],
        "district_name": district["name"],
        "state": district["state"],
        "weather": weather,
        "alerts": alerts,
        # What is actually happening on the ground here — the layer no forecast
        # provider has, because it comes from people asking for help.
        "active_hazards": hazards,
        "people_affected": sum(int(h["people"] or 0) for h in hazards),
        "risk_band": band,
        # Per-hazard likelihood with reasons — "is a flood or landslide likely
        # HERE", not a temperature reading.
        "outlook": outlook,
        "stale_minutes": weather.get("stale_minutes", 0),
        "degraded": bool(weather.get("degraded")) or not weather.get("available"),
    }


# Concurrency cap on the outbound weather fetch. Sixteen districts at once is
# rude to a free API and pointless besides — the 15-minute cache means most
# calls never leave the process.
_FETCH_SLOTS = 6


async def all_district_conditions(session: AsyncSession) -> list[dict[str, Any]]:
    """Every district's outlook, worst first.

    Drives the live feed: a Collector watching one district still needs to know
    that the district upstream is about to fail, because that is where the
    mutual-aid request comes from and where their own units may be sent.

    Fetched concurrently and bounded, then sorted so the board reads as a
    priority list rather than an alphabetical directory — the point of a feed is
    that the top of it is the thing to look at.
    """
    import asyncio

    rows = (
        await session.execute(text("SELECT id FROM districts ORDER BY id"))
    ).all()
    ids = [r[0] for r in rows]

    sem = asyncio.Semaphore(_FETCH_SLOTS)

    async def one(did: int) -> dict[str, Any]:
        async with sem:
            try:
                return await district_conditions(session, did)
            except Exception:
                # One district failing must never blank the whole feed.
                return {"district_id": did, "error": True, "risk_band": "unknown",
                        "outlook": [], "active_hazards": []}

    results = [await one(d) for d in ids]

    order = {"danger": 0, "warning": 1, "watch": 2, "normal": 3, "unknown": 4}
    results.sort(
        key=lambda r: (
            order.get(r.get("risk_band"), 5),
            -sum(int(h.get("incidents") or 0) for h in r.get("active_hazards", [])),
            r.get("district_name") or "",
        )
    )
    return results
