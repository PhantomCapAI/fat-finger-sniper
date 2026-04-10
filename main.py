"""fat-finger-sniper — On-chain monitor for mispriced NFT listings."""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import POLL_INTERVAL_ETH, POLL_INTERVAL_SOL, MAX_WATCHLIST
from monitors import opensea, magiceden
from alerts import send_alert

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# --- State ---

watchlist_eth: dict[str, dict] = {}  # slug -> {added_at, last_scan, hits}
watchlist_sol: dict[str, dict] = {}  # symbol -> {added_at, last_scan, hits}
recent_alerts: list[dict] = []       # last N alerts
_scan_tasks: list[asyncio.Task] = []
MAX_RECENT = 200


# --- Background scanners ---

async def eth_scanner():
    """Poll OpenSea collections for fat-finger listings."""
    while True:
        for slug in list(watchlist_eth.keys()):
            try:
                flagged = await opensea.scan_collection(slug)
                watchlist_eth[slug]["last_scan"] = datetime.now(timezone.utc).isoformat()
                for f in flagged:
                    watchlist_eth[slug]["hits"] = watchlist_eth[slug].get("hits", 0) + 1
                    recent_alerts.append(f)
                    if len(recent_alerts) > MAX_RECENT:
                        recent_alerts.pop(0)
                    await send_alert(f)
                    logger.info(f"ETH fat finger: {slug} {f.get('token_id')} @ {f.get('listing_price')} (floor {f.get('floor_price')})")
            except Exception as e:
                logger.error(f"ETH scan error {slug}: {e}")
            await asyncio.sleep(1)  # rate limit between collections
        await asyncio.sleep(POLL_INTERVAL_ETH)


async def sol_scanner():
    """Poll Magic Eden collections for fat-finger listings."""
    while True:
        for symbol in list(watchlist_sol.keys()):
            try:
                flagged = await magiceden.scan_collection(symbol)
                watchlist_sol[symbol]["last_scan"] = datetime.now(timezone.utc).isoformat()
                for f in flagged:
                    watchlist_sol[symbol]["hits"] = watchlist_sol[symbol].get("hits", 0) + 1
                    recent_alerts.append(f)
                    if len(recent_alerts) > MAX_RECENT:
                        recent_alerts.pop(0)
                    await send_alert(f)
                    logger.info(f"SOL fat finger: {symbol} {f.get('token_mint', '')[:8]}... @ {f.get('listing_price_sol')} (floor {f.get('floor_price_sol')})")
            except Exception as e:
                logger.error(f"SOL scan error {symbol}: {e}")
            await asyncio.sleep(1)
        await asyncio.sleep(POLL_INTERVAL_SOL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Fat Finger Sniper starting...")
    t1 = asyncio.create_task(eth_scanner())
    t2 = asyncio.create_task(sol_scanner())
    _scan_tasks.extend([t1, t2])
    yield
    for t in _scan_tasks:
        t.cancel()


# --- App ---

app = FastAPI(title="fat-finger-sniper", docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "fat-finger-sniper",
        "watchlist_eth": len(watchlist_eth),
        "watchlist_sol": len(watchlist_sol),
        "total_alerts": len(recent_alerts),
    }


# --- Watchlist management ---

@app.post("/watch/eth/{slug}")
async def watch_eth(slug: str):
    """Add an OpenSea collection to the ETH watchlist."""
    if len(watchlist_eth) >= MAX_WATCHLIST:
        return {"error": f"max {MAX_WATCHLIST} collections"}, 400
    if slug in watchlist_eth:
        return {"status": "already_watching", "slug": slug}
    watchlist_eth[slug] = {"added_at": datetime.now(timezone.utc).isoformat(), "last_scan": None, "hits": 0}
    return {"status": "watching", "slug": slug, "chain": "ethereum"}


@app.post("/watch/sol/{symbol}")
async def watch_sol(symbol: str):
    """Add a Magic Eden collection to the SOL watchlist."""
    if len(watchlist_sol) >= MAX_WATCHLIST:
        return {"error": f"max {MAX_WATCHLIST} collections"}, 400
    if symbol in watchlist_sol:
        return {"status": "already_watching", "symbol": symbol}
    watchlist_sol[symbol] = {"added_at": datetime.now(timezone.utc).isoformat(), "last_scan": None, "hits": 0}
    return {"status": "watching", "symbol": symbol, "chain": "solana"}


@app.delete("/watch/eth/{slug}")
async def unwatch_eth(slug: str):
    """Remove an OpenSea collection from the ETH watchlist."""
    if slug in watchlist_eth:
        del watchlist_eth[slug]
        return {"status": "removed", "slug": slug}
    return {"status": "not_found", "slug": slug}


@app.delete("/watch/sol/{symbol}")
async def unwatch_sol(symbol: str):
    """Remove a Magic Eden collection from the SOL watchlist."""
    if symbol in watchlist_sol:
        del watchlist_sol[symbol]
        return {"status": "removed", "symbol": symbol}
    return {"status": "not_found", "symbol": symbol}


# --- Scanning ---

@app.get("/scan/eth/{slug}")
async def scan_eth(slug: str):
    """One-shot scan of an OpenSea collection for fat-finger listings."""
    floor = await opensea.get_collection_floor(slug)
    flagged = await opensea.scan_collection(slug)
    return {"collection": slug, "chain": "ethereum", "floor": floor, "flagged": flagged, "count": len(flagged)}


@app.get("/scan/sol/{symbol}")
async def scan_sol(symbol: str):
    """One-shot scan of a Magic Eden collection for fat-finger listings."""
    stats = await magiceden.get_collection_stats(symbol)
    flagged = await magiceden.scan_collection(symbol)
    return {"collection": symbol, "chain": "solana", "stats": stats, "flagged": flagged, "count": len(flagged)}


# --- Dashboard ---

@app.get("/dashboard")
async def dashboard():
    """Full dashboard: watchlists, recent alerts, stats."""
    return {
        "watchlist_eth": {slug: info for slug, info in watchlist_eth.items()},
        "watchlist_sol": {sym: info for sym, info in watchlist_sol.items()},
        "recent_alerts": recent_alerts[-50:],
        "total_alerts": len(recent_alerts),
    }


@app.get("/alerts")
async def get_alerts(limit: int = 50):
    """Get recent fat-finger alerts."""
    return {"alerts": recent_alerts[-limit:], "total": len(recent_alerts)}
