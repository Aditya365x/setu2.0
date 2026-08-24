"""Reference codes and reporter identity.

§11.3: phone numbers are stored only as HMAC-SHA256(phone, server_salt). Raw
numbers are held transiently in the outbound queue and never persisted. Reports
are anonymous by default.
"""

import hashlib
import hmac
import secrets

from .config import settings

# Unambiguous alphabet — no O/0, no I/1. These codes get read aloud over a
# radio and typed by someone standing in water.
ALPHABET = "ACDEFGHJKLMNPQRTUVWXY2346789"


def reference_code(length: int = 4) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def reporter_hash(phone: str | None) -> str | None:
    if not phone:
        return None
    return hmac.new(
        settings.reporter_hash_salt.encode(), phone.strip().encode(), hashlib.sha256
    ).hexdigest()
