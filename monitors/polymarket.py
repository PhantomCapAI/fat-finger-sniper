"""Polymarket monitor — detects mispriced prediction market shares."""

import logging
import httpx

from config import POLYMARKET_CLOB_BASE, POLYMARKET_GAMMA_BASE
from engine.detector import build_opportunity

logger = logging.getLogger(__name__)


async def get_markets(limit: int = 50, active: bool = True) -> list[dict]:
    """Get active markets from Polymarket Gamma API.

    GET https://gamma-api.polymarket.com/markets?limit=N&active=true&closed=false
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{POLYMARKET_GAMMA_BASE}/markets",
                params={
                    "limit": limit,
                    "active": str(active).lower(),
                    "closed": "false",
                },
            )
            if resp.status_code != 200:
                return []
            return resp.json()
    except Exception as e:
        logger.error(f"Polymarket markets error: {e}")
        return []


async def get_orderbook(token_id: str) -> dict | None:
    """Get order book for a specific outcome token.

    GET https://clob.polymarket.com/book?token_id=X
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{POLYMARKET_CLOB_BASE}/book",
                params={"token_id": token_id},
            )
            if resp.status_code != 200:
                return None
            return resp.json()
    except Exception as e:
        logger.error(f"Polymarket orderbook error: {e}")
    return None


async def get_midpoint(token_id: str) -> float | None:
    """Get midpoint price for a token.

    GET https://clob.polymarket.com/midpoint?token_id=X
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{POLYMARKET_CLOB_BASE}/midpoint",
                params={"token_id": token_id},
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            return float(data.get("mid", 0))
    except Exception as e:
        logger.error(f"Polymarket midpoint error: {e}")
    return None


async def scan_market(market: dict) -> list[dict]:
    """Scan a market for mispriced shares.

    A binary market has YES and NO tokens. If YES is at 0.95,
    NO should be near 0.05. If NO is listed at 0.001, that's a fat finger.

    Also checks: ask price significantly below midpoint = fat finger.
    """
    opps = []
    tokens = market.get("tokens", [])
    if not tokens:
        # Try clobTokenIds format
        clob_ids = market.get("clobTokenIds", "")
        if isinstance(clob_ids, str) and clob_ids:
            tokens = [{"token_id": tid.strip()} for tid in clob_ids.split(",") if tid.strip()]

    question = market.get("question", market.get("title", "Unknown"))
    slug = market.get("slug", market.get("conditionId", ""))

    for token_info in tokens:
        token_id = token_info.get("token_id", "")
        if not token_id:
            continue

        book = await get_orderbook(token_id)
        if not book:
            continue

        asks = book.get("asks", [])
        last_trade = float(book.get("last_trade_price", "0.5"))

        if not asks:
            continue

        # Best ask (lowest sell price)
        best_ask_price = float(asks[0].get("price", "1"))
        best_ask_size = float(asks[0].get("size", "0"))

        if best_ask_price <= 0 or last_trade <= 0:
            continue

        # Fair value = midpoint or last trade
        fair = last_trade

        opp = build_opportunity(
            marketplace="polymarket",
            chain="polygon",
            asset_id=token_id,
            asset_name=question[:60],
            listing_price=best_ask_price,
            fair_value=fair,
            currency="USDC",
            url=f"https://polymarket.com/event/{slug}" if slug else "https://polymarket.com",
            extra={
                "ask_size": best_ask_size,
                "last_trade": last_trade,
                "market_slug": slug,
            },
        )
        if opp:
            opps.append(opp)

    return opps


async def scan(market_limit: int = 30) -> list[dict]:
    """Scan active Polymarket markets for mispriced shares."""
    markets = await get_markets(limit=market_limit)
    all_opps = []
    for market in markets:
        opps = await scan_market(market)
        all_opps.extend(opps)
    return all_opps
