"""§10.2 — CAP ingestion from NDMA SACHET, with ETag caching.

SACHET's integration guide specifies ETag-based caching: the endpoint returns
200 with XML when content has changed and 304 when it has not, and consuming
agencies are expected to store both the XML and the ETag. Implementing that
correctly is a small detail that signals we read the specification rather than
guessing at it.

Offline is a first-class path, not a fallback. With OFFLINE_MODE=true the
poller serves recorded fixtures and the whole system runs with the venue Wi-Fi
physically unplugged — which is how it will be demonstrated.
"""

import json
from datetime import datetime
from pathlib import Path

import httpx
from lxml import etree
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..bus import publish, redis, trigger_optimize
from ..config import settings

CAP_NS = {"cap": "urn:oasis:names:tc:emergency:cap:1.2"}
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"

UPSERT_ALERT_SQL = text(
    """
    INSERT INTO alerts (district_id, cap_identifier, source_agency, event, severity,
                        urgency, certainty, headline, instruction, area_polygon,
                        effective_from, expires_at, raw_xml)
    VALUES (:did, :ident, :agency, :event, :severity, :urgency, :certainty,
            :headline, :instruction,
            ST_Multi(ST_GeomFromGeoJSON(:polygon))::geography,
            :effective, :expires, :raw)
    ON CONFLICT (cap_identifier) DO UPDATE
       SET severity = EXCLUDED.severity,
           urgency  = EXCLUDED.urgency,
           expires_at = EXCLUDED.expires_at
    RETURNING id
    """
)


def _ts(value: str | None) -> datetime | None:
    """CAP carries ISO-8601 with an offset. asyncpg wants a real datetime, not
    the string — passing it through raises a DataError at insert time."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        return None


def _text(node, path: str) -> str | None:
    found = node.find(path, CAP_NS)
    return found.text.strip() if found is not None and found.text else None


def _polygon_to_geojson(polygon_text: str) -> dict | None:
    """CAP polygons are 'lat,lng lat,lng ...' — note the axis order is the
    reverse of GeoJSON, which is a classic source of silently-wrong maps."""
    coords = []
    for pair in polygon_text.split():
        try:
            lat_s, lng_s = pair.split(",")
            coords.append([float(lng_s), float(lat_s)])
        except ValueError:
            continue
    if len(coords) < 4:
        return None
    if coords[0] != coords[-1]:
        coords.append(coords[0])
    return {"type": "Polygon", "coordinates": [coords]}


def parse_cap(xml_bytes: bytes) -> list[dict]:
    root = etree.fromstring(xml_bytes)
    identifier = _text(root, "cap:identifier") or ""
    sender = _text(root, "cap:sender") or ""

    alerts = []
    for info in root.findall("cap:info", CAP_NS):
        area = info.find("cap:area", CAP_NS)
        polygon = None
        if area is not None:
            poly_node = area.find("cap:polygon", CAP_NS)
            if poly_node is not None and poly_node.text:
                polygon = _polygon_to_geojson(poly_node.text)

        alerts.append(
            {
                "cap_identifier": identifier,
                "source_agency": sender,
                "event": _text(info, "cap:event"),
                "severity": _text(info, "cap:severity"),
                "urgency": _text(info, "cap:urgency"),
                "certainty": _text(info, "cap:certainty"),
                "headline": _text(info, "cap:headline"),
                "instruction": _text(info, "cap:instruction"),
                "effective": _ts(_text(info, "cap:effective")),
                "expires": _ts(_text(info, "cap:expires")),
                "polygon": polygon,
            }
        )
    return alerts


async def _fetch_xml(district_id: int) -> bytes | None:
    """Returns XML bytes, or None when nothing has changed."""
    if settings.offline_mode:
        fixture = FIXTURES / "cap_cyclone_ganjam.xml"
        return fixture.read_bytes() if fixture.exists() else None

    etag = await redis.get(f"cap:etag:{district_id}")
    headers = {"If-None-Match": etag} if etag else {}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(settings.cap_endpoint, headers=headers)
    except Exception:
        await redis.set(f"cap:degraded:{district_id}", "1", ex=300)
        cached = await redis.get(f"cap:xml:{district_id}")
        return cached.encode() if cached else None

    if resp.status_code == 304:
        return None                      # unchanged — the cheap, common case
    if resp.status_code != 200:
        await redis.set(f"cap:degraded:{district_id}", "1", ex=300)
        cached = await redis.get(f"cap:xml:{district_id}")
        return cached.encode() if cached else None

    if resp.headers.get("ETag"):
        await redis.set(f"cap:etag:{district_id}", resp.headers["ETag"])
    # Cache the last known good response so a feed outage shows a staleness
    # banner rather than a blank map.
    await redis.set(f"cap:xml:{district_id}", resp.text)
    await redis.delete(f"cap:degraded:{district_id}")
    return resp.content


async def poll_cap(session: AsyncSession, district_id: int) -> dict:
    xml = await _fetch_xml(district_id)
    if not xml:
        return {"changed": False}

    ingested = 0
    for alert in parse_cap(xml):
        if not alert["cap_identifier"] or not alert["polygon"]:
            continue

        # Not our district — CAP is a national feed and most of it is somebody
        # else's problem.
        intersects = (
            await session.execute(
                text(
                    """
                    SELECT ST_Intersects(
                        boundary,
                        ST_GeomFromGeoJSON(:poly)::geography
                    ) FROM districts WHERE id = :did
                    """
                ),
                {"poly": json.dumps(alert["polygon"]), "did": district_id},
            )
        ).scalar_one_or_none()
        if not intersects:
            continue

        await session.execute(
            UPSERT_ALERT_SQL,
            {
                "did": district_id,
                "ident": alert["cap_identifier"],
                "agency": alert["source_agency"],
                "event": alert["event"],
                "severity": alert["severity"],
                "urgency": alert["urgency"],
                "certainty": alert["certainty"],
                "headline": alert["headline"],
                "instruction": alert["instruction"],
                "polygon": json.dumps(alert["polygon"]),
                "effective": alert["effective"],
                "expires": alert["expires"],
                "raw": xml.decode("utf-8", errors="replace"),
            },
        )
        ingested += 1
        await publish(district_id, "alert.new", {k: v for k, v in alert.items() if k != "polygon"})

    if ingested:
        await session.commit()
        # A new alert changes the official term of every severity score in the
        # polygon, so the whole picture is re-scored.
        await trigger_optimize(district_id, "alert")

    return {"changed": True, "ingested": ingested}
