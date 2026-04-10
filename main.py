"""fat-finger-sniper — Multi-marketplace misprice detection and execution engine."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import (
    PAPER_MODE, POLL_INTERVAL_NFT, POLL_INTERVAL_DEX,
    POLL_INTERVAL_POLY, POLL_INTERVAL_TRAD,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, MAX_DAILY_USD,
)
from db import init_db, close_db, get_stats, get_recent_opportunities, add_blacklist
from engine.executor import process_opportunity
from engine.killswitch import handle_callback
from engine.pipeline import send_to_pipeline, send_fun_telegram
from monitors import opensea, magiceden, tensor, jupiter, polymarket, crossdex
from monitors import stockx, tcgplayer, godaddy, ebay

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# --- Watchlists (in-memory, managed via API) ---
watchlist_nft_eth: list[str] = []   # OpenSea slugs
watchlist_nft_sol: list[str] = []   # Magic Eden symbols / Tensor slugs
watchlist_stockx: list[str] = []    # StockX search queries
watchlist_tcg: list[tuple[str, str]] = []  # (game, query) pairs
watchlist_godaddy: list[str] = []   # Domain search queries
watchlist_ebay: dict[str, float] = {}  # query -> fair_value_usd

_tasks: list[asyncio.Task] = []


# --- Background Scanners ---

async def _process_all(opps: list[dict]):
    """Process a batch of opportunities through the execution pipeline."""
    for opp in opps:
        try:
            result = await process_opportunity(opp)
            action = result.get("action", "skipped")
            if action in ("executed", "paper_logged"):
                await send_fun_telegram(opp, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
                await send_to_pipeline(opp, action)
            logger.info(f"{opp['marketplace']}/{opp['asset_id']}: {action} ({result.get('reason', '')})")
        except Exception as e:
            logger.error(f"Process error: {e}")


async def nft_eth_scanner():
    """Poll OpenSea for ETH NFT fat fingers."""
    while True:
        for slug in list(watchlist_nft_eth):
            try:
                opps = await opensea.scan(slug)
                await _process_all(opps)
            except Exception as e:
                logger.error(f"OpenSea scan {slug}: {e}")
            await asyncio.sleep(1)
        await asyncio.sleep(POLL_INTERVAL_NFT)


async def nft_sol_scanner():
    """Poll Magic Eden + Tensor for Solana NFT fat fingers."""
    while True:
        for symbol in list(watchlist_nft_sol):
            try:
                me_opps = await magiceden.scan(symbol)
                t_opps = await tensor.scan(symbol)
                await _process_all(me_opps + t_opps)
            except Exception as e:
                logger.error(f"SOL NFT scan {symbol}: {e}")
            await asyncio.sleep(1)
        await asyncio.sleep(POLL_INTERVAL_NFT)


async def dex_scanner():
    """Poll Jupiter + cross-DEX comparator for misprices."""
    while True:
        try:
            opps = await jupiter.scan()
            await _process_all(opps)
        except Exception as e:
            logger.error(f"Jupiter scan: {e}")

        try:
            cross_opps = await crossdex.scan()
            await _process_all(cross_opps)
        except Exception as e:
            logger.error(f"Cross-DEX scan: {e}")

        await asyncio.sleep(POLL_INTERVAL_DEX)


async def polymarket_scanner():
    """Poll Polymarket for mispriced shares."""
    while True:
        try:
            opps = await polymarket.scan()
            await _process_all(opps)
        except Exception as e:
            logger.error(f"Polymarket scan: {e}")
        await asyncio.sleep(POLL_INTERVAL_POLY)


async def traditional_scanner():
    """Poll StockX, TCGPlayer, GoDaddy, eBay."""
    while True:
        try:
            if watchlist_stockx:
                opps = await stockx.scan(watchlist_stockx)
                await _process_all(opps)
        except Exception as e:
            logger.error(f"StockX scan: {e}")

        try:
            if watchlist_tcg:
                games = [g for g, q in watchlist_tcg]
                queries = [q for g, q in watchlist_tcg]
                opps = await tcgplayer.scan(games, queries)
                await _process_all(opps)
        except Exception as e:
            logger.error(f"TCGPlayer scan: {e}")

        try:
            if watchlist_godaddy:
                opps = await godaddy.scan(watchlist_godaddy)
                await _process_all(opps)
        except Exception as e:
            logger.error(f"GoDaddy scan: {e}")

        try:
            if watchlist_ebay:
                opps = await ebay.scan(watchlist_ebay)
                await _process_all(opps)
        except Exception as e:
            logger.error(f"eBay scan: {e}")

        await asyncio.sleep(POLL_INTERVAL_TRAD)


# --- App Lifecycle ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info(f"Fat Finger Sniper starting (paper_mode={PAPER_MODE})")
    _tasks.extend([
        asyncio.create_task(nft_eth_scanner()),
        asyncio.create_task(nft_sol_scanner()),
        asyncio.create_task(dex_scanner()),
        asyncio.create_task(polymarket_scanner()),
        asyncio.create_task(traditional_scanner()),
    ])
    yield
    for t in _tasks:
        t.cancel()
    await close_db()


app = FastAPI(title="fat-finger-sniper", docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# --- Health & Dashboard ---

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "paper_mode": PAPER_MODE,
        "watchlists": {
            "nft_eth": len(watchlist_nft_eth),
            "nft_sol": len(watchlist_nft_sol),
            "stockx": len(watchlist_stockx),
            "tcg": len(watchlist_tcg),
            "godaddy": len(watchlist_godaddy),
            "ebay": len(watchlist_ebay),
        },
    }


@app.get("/dashboard")
async def dashboard():
    stats = await get_stats()
    recent = await get_recent_opportunities(50)
    # Serialize datetime objects
    for r in recent:
        for k, v in r.items():
            if isinstance(v, datetime):
                r[k] = v.isoformat()
    return {
        "paper_mode": PAPER_MODE,
        "max_daily_usd": MAX_DAILY_USD,
        "stats": stats,
        "recent_opportunities": recent,
        "watchlists": {
            "nft_eth": watchlist_nft_eth,
            "nft_sol": watchlist_nft_sol,
            "stockx": watchlist_stockx,
            "tcg": watchlist_tcg,
            "godaddy": watchlist_godaddy,
            "ebay": {k: v for k, v in watchlist_ebay.items()},
        },
    }


@app.get("/stats")
async def stats():
    return await get_stats()


# --- Watchlist Management ---

@app.post("/watch/nft/eth/{slug}")
async def watch_nft_eth(slug: str):
    if slug not in watchlist_nft_eth:
        watchlist_nft_eth.append(slug)
    return {"status": "watching", "slug": slug, "chain": "ethereum"}


@app.post("/watch/nft/sol/{symbol}")
async def watch_nft_sol(symbol: str):
    if symbol not in watchlist_nft_sol:
        watchlist_nft_sol.append(symbol)
    return {"status": "watching", "symbol": symbol, "chain": "solana"}


@app.post("/watch/stockx")
async def watch_stockx(request: Request):
    body = await request.json()
    query = body.get("query", "")
    if query and query not in watchlist_stockx:
        watchlist_stockx.append(query)
    return {"status": "watching", "query": query}


@app.post("/watch/tcg")
async def watch_tcg(request: Request):
    body = await request.json()
    game = body.get("game", "pokemon")
    query = body.get("query", "")
    if query:
        watchlist_tcg.append((game, query))
    return {"status": "watching", "game": game, "query": query}


@app.post("/watch/godaddy")
async def watch_godaddy(request: Request):
    body = await request.json()
    query = body.get("query", "")
    if query and query not in watchlist_godaddy:
        watchlist_godaddy.append(query)
    return {"status": "watching", "query": query}


@app.post("/watch/ebay")
async def watch_ebay(request: Request):
    body = await request.json()
    query = body.get("query", "")
    fair_value = body.get("fair_value", 0)
    if query and fair_value > 0:
        watchlist_ebay[query] = fair_value
    return {"status": "watching", "query": query, "fair_value": fair_value}


@app.delete("/watch/nft/eth/{slug}")
async def unwatch_nft_eth(slug: str):
    if slug in watchlist_nft_eth:
        watchlist_nft_eth.remove(slug)
    return {"status": "removed", "slug": slug}


@app.delete("/watch/nft/sol/{symbol}")
async def unwatch_nft_sol(symbol: str):
    if symbol in watchlist_nft_sol:
        watchlist_nft_sol.remove(symbol)
    return {"status": "removed", "symbol": symbol}


# --- One-shot Scans ---

@app.get("/scan/opensea/{slug}")
async def scan_opensea(slug: str):
    opps = await opensea.scan(slug)
    return {"marketplace": "opensea", "collection": slug, "opportunities": opps, "count": len(opps)}


@app.get("/scan/magiceden/{symbol}")
async def scan_magiceden(symbol: str):
    opps = await magiceden.scan(symbol)
    return {"marketplace": "magiceden", "collection": symbol, "opportunities": opps, "count": len(opps)}


@app.get("/scan/tensor/{slug}")
async def scan_tensor(slug: str):
    opps = await tensor.scan(slug)
    return {"marketplace": "tensor", "collection": slug, "opportunities": opps, "count": len(opps)}


@app.get("/scan/polymarket")
async def scan_polymarket():
    opps = await polymarket.scan()
    return {"marketplace": "polymarket", "opportunities": opps, "count": len(opps)}


@app.get("/scan/crossdex")
async def scan_crossdex():
    opps = await crossdex.scan()
    return {"marketplace": "crossdex", "tokens_monitored": list(crossdex.MONITOR_TOKENS.keys()), "opportunities": opps, "count": len(opps)}


@app.get("/scan/crossdex/{symbol}")
async def scan_crossdex_token(symbol: str):
    token_info = crossdex.MONITOR_TOKENS.get(symbol.upper())
    if not token_info:
        return {"error": f"Unknown token: {symbol}", "available": list(crossdex.MONITOR_TOKENS.keys())}
    opps = await crossdex.scan_token(symbol.upper(), token_info)
    return {"token": symbol.upper(), "opportunities": opps, "count": len(opps)}


# --- Blacklist ---

@app.post("/blacklist")
async def add_to_blacklist(request: Request):
    body = await request.json()
    entry_type = body.get("type", "collection")  # collection, seller, contract
    value = body.get("value", "")
    reason = body.get("reason", "")
    await add_blacklist(entry_type, value, reason)
    return {"status": "blacklisted", "type": entry_type, "value": value}


# --- Telegram Callback Handler ---

@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """Handle Telegram inline keyboard callbacks (CANCEL / BUY NOW)."""
    body = await request.json()
    callback = body.get("callback_query")
    if not callback:
        return {"ok": True}

    data = callback.get("data", "")
    if ":" not in data:
        return {"ok": True}

    action, opp_id_str = data.split(":", 1)
    try:
        opp_id = int(opp_id_str)
    except ValueError:
        return {"ok": True}

    handle_callback(opp_id, action)

    # Answer the callback to remove loading spinner
    callback_id = callback.get("id")
    if callback_id and TELEGRAM_BOT_TOKEN:
        import httpx
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(url, json={
                "callback_query_id": callback_id,
                "text": f"{'Cancelled' if action == 'cancel' else 'Executing...'}"
            })

    return {"ok": True}
