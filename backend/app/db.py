"""Database engine, session factory, and schema bootstrap.

All spatial work happens in SQL (§4.4), so this layer stays deliberately thin:
an async engine, a session dependency, and one idempotent DDL apply.
"""

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import settings

engine = create_async_engine(settings.database_url, pool_size=10, max_overflow=20)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# Arbitrary but stable: any process applying the schema takes this lock.
SCHEMA_LOCK_ID = 8_675_309


async def get_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session


async def apply_schema() -> None:
    """Idempotent: safe on every boot. `make reset` truncates, never drops.

    The DDL is one multi-statement script, and asyncpg refuses those through
    the extended (prepared-statement) protocol. Drop to the raw driver
    connection, which speaks the simple query protocol and runs the whole file
    in one round trip — keeping schema.sql readable as a single document rather
    than splitting it into fragments that a `DO $$ ... $$` block would break
    anyway.
    """
    ddl = SCHEMA_PATH.read_text(encoding="utf-8")
    async with engine.begin() as conn:
        raw = await conn.get_raw_connection()
        driver = raw.driver_connection
        # API and worker boot together and both apply the schema. CREATE
        # EXTENSION IF NOT EXISTS is not atomic under concurrency, so the loser
        # of that race dies on a duplicate-key error. One advisory lock removes
        # the race entirely and costs nothing.
        await driver.execute("SELECT pg_advisory_lock($1)", SCHEMA_LOCK_ID)
        try:
            await driver.execute(ddl)
        finally:
            await driver.execute("SELECT pg_advisory_unlock($1)", SCHEMA_LOCK_ID)
