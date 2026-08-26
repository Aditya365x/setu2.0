"""Runtime configuration. Every tunable from Appendix B lives here."""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── infrastructure ────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://setu:setu@localhost:5433/setu"
    redis_url: str = "redis://localhost:6379/0"

    @field_validator("database_url")
    @classmethod
    def _force_async_driver(cls, v: str) -> str:
        """Rewrite a sync Postgres URL to the asyncpg driver.

        Every managed host — Render, Railway, Heroku, Fly — injects
        `postgresql://user:pass@host/db`, because that is what psql and every
        sync client expect. SQLAlchemy's async engine refuses it with
        "the asyncio extension requires an async driver to be used", the API
        dies on startup, and the platform reports a failed health check against
        a port nothing ever bound. The cause is one missing `+asyncpg`.

        Normalising here rather than in the deploy docs means the platform's
        own auto-injected variable just works. `postgres://` is included
        because Heroku-style URLs still appear in the wild, and an explicit
        driver (`+asyncpg`, `+psycopg`) is left untouched.
        """
        for prefix in ("postgresql://", "postgres://"):
            if v.startswith(prefix):
                return "postgresql+asyncpg://" + v[len(prefix):]
        return v

    # ── pluggable adapters (§9.1 idiom: selected by env var) ──────────────
    routing_provider: str = "haversine"   # haversine | osrm
    storage_provider: str = "local"       # local | minio
    sms_provider: str = "mock"            # mock | twilio | exotel

    osrm_url: str = "http://localhost:5000"
    media_root: str = "/data/media"
    media_base_url: str = "/media"

    # ── external feeds ────────────────────────────────────────────────────
    cap_endpoint: str = "https://sachet.ndma.gov.in/cap_public_website/rss/rss_india.xml"
    cap_poll_seconds: int = 60
    # OFFLINE_MODE=true -> CAP is read from fixtures/, zero egress. The demo
    # default, because the alert path HAS a recorded fixture to fall back on.
    offline_mode: bool = True

    # Live conditions are a separate switch, deliberately.
    #
    # Tying them to OFFLINE_MODE was wrong: CAP has a fixture to serve when the
    # network is gone, and the conditions panel does not. Sharing one flag meant
    # the panel showed nothing at all in the demo default, which is not a
    # degraded reading — it is no feature. It now always tries live, falls back
    # to the last good cached reading, and only then says it does not know.
    # Nothing on screen ever blocks on it.
    live_conditions: bool = True
    conditions_endpoint: str = "https://api.open-meteo.com/v1/forecast"

    # ── tenancy ───────────────────────────────────────────────────────────
    district_id: int = 1

    # ── §6.1 clustering ───────────────────────────────────────────────────
    cluster_eps_m: int = 300
    cluster_window_min: int = 30

    # ── §6.3 trust ────────────────────────────────────────────────────────
    trust_quarantine_threshold: float = 0.35

    # ── §6.4 routing fallback ─────────────────────────────────────────────
    # 8.33 m/s ~= 30 km/h effective road speed under disaster conditions.
    fallback_speed_mps: float = 8.33

    # ── §6.5 cost matrix ──────────────────────────────────────────────────
    urgency_weight: float = 0.30
    sla_breach_penalty: float = 500.0
    reassignment_penalty: float = 400.0
    commitment_bonus: float = 200.0

    # ── §6.8 the loop ─────────────────────────────────────────────────────
    optimize_debounce_seconds: float = 2.0
    optimize_tick_seconds: int = 30
    spatial_prefilter_km: int = 50

    # ── §11.3 privacy ─────────────────────────────────────────────────────
    reporter_hash_salt: str = "dev-salt-change-me"
    jwt_secret: str = "dev-secret-change-me"

    # ── sms gateway ───────────────────────────────────────────────────────
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from: str = ""
    gateway_webhook_secret: str = "dev-webhook-secret"


settings = Settings()
