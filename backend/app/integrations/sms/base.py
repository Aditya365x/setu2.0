"""§9.1 — gateway abstraction.

Be honest about this in the pitch: an Indian inbound shortcode or long-code is
not obtainable in hackathon time. What is real here is the webhook, the parser
and the outbound path — behind a pluggable interface, so swapping Twilio for
Exotel in a pilot is a config change. Stating that plainly reads as competence;
pretending otherwise loses the feasibility parameter to anyone who knows
telecom.
"""

import hashlib
import hmac
from dataclasses import dataclass
from typing import Protocol


@dataclass
class InboundMessage:
    sender: str
    body: str


class SmsGateway(Protocol):
    async def send(self, to: str, body: str) -> str: ...
    def parse_inbound(self, raw: dict) -> InboundMessage: ...
    def verify_signature(self, raw: dict, headers: dict) -> bool: ...


def hmac_matches(secret: str, payload: bytes, provided: str | None) -> bool:
    if not provided:
        return False
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)


def bearing_text(from_lat: float, from_lng: float, to_lat: float, to_lng: float) -> str:
    """'1.2 km NE' — an instruction someone can act on in the dark, not a
    coordinate pair."""
    import math

    dlat = to_lat - from_lat
    dlng = to_lng - from_lng
    brg = (math.degrees(math.atan2(dlng, dlat)) + 360) % 360
    points = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    compass = points[int((brg + 22.5) // 45) % 8]

    from ...services.routing import haversine_m

    km = haversine_m(from_lat, from_lng, to_lat, to_lng) / 1000
    return f"{km:.1f} km {compass}"
