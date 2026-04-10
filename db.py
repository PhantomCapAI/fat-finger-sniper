"""Database layer — Neon Postgres via asyncpg."""
import logging
from datetime import datetime, timezone, timedelta

import asyncpg

from config import DATABASE_URL

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None

# asyncpg's Connection.execute runs a single statement reliably; splitting
# the schema into individual statements avoids silent partial execution.
SCHEMA_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS opportunities (
        id SERIAL PRIMARY KEY,
        ts TIMESTAMPTZ DEFAULT NOW(),
        marketplace TEXT NOT NULL,
        chain TEXT NOT NULL,
        asset_id TEXT NOT NULL,
        asset_name TEXT,
        listing_price NUMERIC NOT NULL,
        fair_value NUMERIC NOT NULL,
        discount_pct NUMERIC NOT NULL,
        confidence TEXT NOT NULL,
        action TEXT DEFAULT 'pending',
        tx_hash TEXT,
        cost_usd NUMERIC,
        paper_mode BOOLEAN DEFAULT TRUE,
        alert_sent BOOLEAN DEFAULT FALSE,
        cancelled BOOLEAN DEFAULT FALSE,
        executed BOOLEAN DEFAULT FALSE,
        metadata JSONB DEFAULT '{}'
    )""",
    """CREATE TABLE IF NOT EXISTS daily_spend (
        date DATE PRIMARY KEY,
        total_usd NUMERIC DEFAULT 0,
        snipe_count INT DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS blacklist (
        id SERIAL PRIMARY KEY,
        entry_type TEXT NOT NULL,
        value TEXT NOT NULL UNIQUE,
        reason TEXT,
        added_at TIMESTAMPTZ DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS purchased_assets (
        id SERIAL PRIMARY KEY,
        asset_id TEXT NOT NULL UNIQUE,
        marketplace TEXT NOT NULL,
        chain TEXT NOT NULL,
        purchased_at TIMESTAMPTZ DEFAULT NOW()
    )""",
    "CREATE INDEX IF NOT EXISTS idx_opps_ts ON opportunities(ts)",
    "CREATE INDEX IF NOT EXISTS idx_opps_action ON opportunities(action)",
    "CREATE INDEX IF NOT EXISTS idx_purchased ON purchased_assets(asset_id)",
    "CREATE INDEX IF NOT EXISTS idx_blacklist ON blacklist(entry_type, value)",
]


async def init_db():
    global _pool
    if not DATABASE_URL:
        logger.warning("DATABASE_URL not set — running without persistence")
        return
    _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    async with _pool.acquire() as conn:
        for stmt in SCHEMA_STATEMENTS:
            await conn.execute(stmt)
    logger.info("Database initialized")


async def close_db():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def log_opportunity(opp: dict) -> int | None:
    if not _pool:
        return None
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO opportunities
               (marketplace, chain, asset_id, asset_name, listing_price, fair_value,
                discount_pct, confidence, paper_mode, metadata)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING id""",
            opp["marketplace"], opp["chain"], opp["asset_id"],
            opp.get("asset_name"), opp["listing_price"], opp["fair_value"],
            opp["discount_pct"], opp["confidence"], opp.get("paper_mode", True),
            opp.get("metadata", "{}"),
        )
        return row["id"]


async def mark_executed(opp_id: int, tx_hash: str, cost_usd: float):
    if not _pool:
        return
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE opportunities SET executed=TRUE, action='executed', tx_hash=$1, cost_usd=$2 WHERE id=$3",
            tx_hash, cost_usd, opp_id,
        )


async def mark_cancelled(opp_id: int):
    if not _pool:
        return
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE opportunities SET cancelled=TRUE, action='cancelled' WHERE id=$1", opp_id,
        )


async def is_duplicate(asset_id: str) -> bool:
    if not _pool:
        return False
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM purchased_assets WHERE asset_id=$1", asset_id
        )
        return row is not None


async def record_purchase(asset_id: str, marketplace: str, chain: str):
    if not _pool:
        return
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO purchased_assets (asset_id, marketplace, chain) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING",
            asset_id, marketplace, chain,
        )


async def get_daily_spend() -> float:
    if not _pool:
        return 0.0
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("SELECT total_usd FROM daily_spend WHERE date=$1", today)
        return float(row["total_usd"]) if row else 0.0


async def add_daily_spend(amount: float):
    if not _pool:
        return
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with _pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO daily_spend (date, total_usd, snipe_count) VALUES ($1, $2, 1)
               ON CONFLICT (date) DO UPDATE SET total_usd = daily_spend.total_usd + $2,
               snipe_count = daily_spend.snipe_count + 1""",
            today, amount,
        )


async def is_blacklisted(entry_type: str, value: str) -> bool:
    if not _pool:
        return False
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM blacklist WHERE entry_type=$1 AND value=$2", entry_type, value
        )
        return row is not None


async def add_blacklist(entry_type: str, value: str, reason: str = ""):
    if not _pool:
        return
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO blacklist (entry_type, value, reason) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING",
            entry_type, value, reason,
        )


async def get_recent_opportunities(limit: int = 50) -> list[dict]:
    if not _pool:
        return []
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM opportunities ORDER BY ts DESC LIMIT $1", limit
        )
        return [dict(r) for r in rows]


async def get_stats() -> dict:
    if not _pool:
        return {"db": "not_connected"}
    async with _pool.acquire() as conn:
        total = await conn.fetchval("SELECT COUNT(*) FROM opportunities")
        executed = await conn.fetchval("SELECT COUNT(*) FROM opportunities WHERE executed=TRUE")
        cancelled = await conn.fetchval("SELECT COUNT(*) FROM opportunities WHERE cancelled=TRUE")
        daily = await get_daily_spend()
        return {
            "total_opportunities": total,
            "executed": executed,
            "cancelled": cancelled,
            "daily_spend_usd": daily,
        }
