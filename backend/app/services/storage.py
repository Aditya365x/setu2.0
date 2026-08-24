"""Photo storage behind the same pluggable-adapter idiom as §9.1.

Local disk is the default: it removes signed-URL work from P0 and keeps the
stack running with the network unplugged. MinIO slots in unchanged when a
deployment needs a real object store.
"""

import io
import uuid
from pathlib import Path
from typing import Protocol

from PIL import Image

from ..config import settings

# Cap uploads hard. A 12 MP photo from a modern phone will stall an ingest on a
# congested network — which is exactly the network this runs on.
MAX_EDGE_PX = 1280
JPEG_QUALITY = 70

EXIF_DATETIME_ORIGINAL = 0x9003
EXIF_DATETIME = 0x0132


def compress(raw: bytes) -> tuple[bytes, dict]:
    """Downscale server-side too. The PWA already does this, but operator
    uploads and gateway-relayed images arrive unprocessed."""
    try:
        img = Image.open(io.BytesIO(raw))
        exif = img.getexif()
        # Feeds the §6.3 photo-freshness term.
        exif_ts = exif.get(EXIF_DATETIME_ORIGINAL) or exif.get(EXIF_DATETIME) if exif else None

        img = img.convert("RGB")
        img.thumbnail((MAX_EDGE_PX, MAX_EDGE_PX))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        return buf.getvalue(), {"exif_ts": exif_ts}
    except Exception:
        # Never lose a report because its photo was malformed.
        return raw, {"exif_ts": None}


class Storage(Protocol):
    async def put(self, raw: bytes, key: str) -> str: ...


class LocalDiskStorage:
    def __init__(self) -> None:
        self.root = Path(settings.media_root)
        self.root.mkdir(parents=True, exist_ok=True)

    async def put(self, raw: bytes, key: str) -> str:
        (self.root / key).write_bytes(raw)
        return f"{settings.media_base_url}/{key}"


class MinioStorage:
    """Deferred. The interface exists so callers never change."""

    async def put(self, raw: bytes, key: str) -> str:  # pragma: no cover
        raise NotImplementedError("Keep STORAGE_PROVIDER=local until MinIO is wired")


def get_storage() -> Storage:
    return MinioStorage() if settings.storage_provider == "minio" else LocalDiskStorage()


async def store_photo(raw: bytes) -> tuple[str, dict]:
    data, meta = compress(raw)
    key = f"{uuid.uuid4().hex}.jpg"
    url = await get_storage().put(data, key)
    return url, meta
