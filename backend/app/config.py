"""Runtime configuration. Every tunable from Appendix B lives here."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── infrastructure ────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://setu:setu@localhost:5433/setu"
    redis_url: str = "redis://localhost:6379/0"

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
    weather_fallback_url: str = "https://api.open-meteo.com/v1/forecast"
    # OFFLINE_MODE=true -> serve only fixtures/, zero egress. The demo default.
    offline_mode: bool = True

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
