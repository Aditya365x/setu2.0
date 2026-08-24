"""Concrete gateways. Selected by SMS_PROVIDER; the rest of the system is
gateway-agnostic."""

import httpx

from ...config import settings
from .base import InboundMessage, SmsGateway, hmac_matches


class MockGateway:
    """The offline demo path. Outbound messages land in an in-memory log the
    dashboard can render, so the SMS beat works with the network unplugged."""

    def __init__(self) -> None:
        self.outbox: list[dict] = []

    async def send(self, to: str, body: str) -> str:
        self.outbox.append({"to": to, "body": body})
        return f"mock-{len(self.outbox)}"

    def parse_inbound(self, raw: dict) -> InboundMessage:
        return InboundMessage(
            sender=str(raw.get("From") or raw.get("from") or "unknown"),
            body=str(raw.get("Body") or raw.get("body") or ""),
        )

    def verify_signature(self, raw: dict, headers: dict) -> bool:
        return True


class TwilioGateway:
    """Demo and pilot. A trial number is enough to land a real SMS on a judge's
    phone, which is worth more than any slide about SMS."""

    def __init__(self) -> None:
        self.sid = settings.twilio_account_sid
        self.token = settings.twilio_auth_token
        self.from_ = settings.twilio_from

    async def send(self, to: str, body: str) -> str:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Messages.json"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                auth=(self.sid, self.token),
                data={"To": to, "From": self.from_, "Body": body},
            )
            resp.raise_for_status()
            return resp.json().get("sid", "")

    def parse_inbound(self, raw: dict) -> InboundMessage:
        return InboundMessage(sender=str(raw.get("From", "")), body=str(raw.get("Body", "")))

    def verify_signature(self, raw: dict, headers: dict) -> bool:
        return hmac_matches(
            settings.gateway_webhook_secret,
            repr(sorted(raw.items())).encode(),
            headers.get("x-setu-signature"),
        )


class ExotelGateway(TwilioGateway):
    """The Indian production path. Same shape; different base URL and auth,
    filled in at pilot time."""

    async def send(self, to: str, body: str) -> str:  # pragma: no cover
        raise NotImplementedError("Wire Exotel credentials at pilot time")


_GATEWAYS = {"mock": MockGateway, "twilio": TwilioGateway, "exotel": ExotelGateway}
_instance: SmsGateway | None = None


def get_gateway() -> SmsGateway:
    global _instance
    if _instance is None:
        _instance = _GATEWAYS[settings.sms_provider]()
    return _instance


def advisory_for(hazard: str, shelter_name: str, bearing: str, eta_min: int, ref: str) -> str:
    """§9.4 — an instruction, not 'stay safe'. Shelter name, bearing, distance,
    ETA, and a reference the citizen can quote back."""
    return (
        f"SETU: {hazard.upper()} alert your area. "
        f"Move to {shelter_name}, {bearing}. Capacity available. "
        f"Rescue team ETA {eta_min} min. Ref {ref}. Do not cross flowing water."
    )
