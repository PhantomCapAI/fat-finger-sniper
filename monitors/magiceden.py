"""Magic Eden monitor — Solana NFT listings."""

import logging
import httpx

from config import MAGICEDEN_API_BASE
from engine.detector import compute_fair_value, build_opportunity

logger = logging.getLogger(__name__)

LAMPORTS_PER_SOL = 1_000_000_000


async def get_collection_stats(symbol: str) -> dict | None:
    """GET /v2/collections/{symbol}/stats"""
    url = f"{MAGICEDEN_API_BASE}/collections/{symbol}/stats"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url)
        if resp.status_code != 200:
            return None
        data = resp.json()
        floor_lam = data.get("floorPrice", 0) or 0
        vol = data.get("volumeAll", 0) or 0
        return {
            "floor_price_sol": floor_lam / LAMPORTS_PER_SOL,
            "volume_sol": vol / LAMPORTS_PER_SOL,
            "listed_count": data.get("listedCount", 0),
        }


async def get_listings(symbol: str, limit: int = 20) -> list[dict]:
    """GET /v2/collections/{symbol}/listings (sorted by price asc)"""
    url = f"{MAGICEDEN_API_BASE}/collections/{symbol}/listings"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, params={"limit": limit})
        if resp.status_code != 200:
            return []
        return resp.json()


async def scan(symbol: str) -> list[dict]:
    """Scan a Solana collection for fat-finger listings."""
    stats = await get_collection_stats(symbol)
    if not stats or stats["floor_price_sol"] <= 0:
        return []

    fair = compute_fair_value(stats["floor_price_sol"])
    listings = await get_listings(symbol)
    opps = []

    for listing in listings:
        price = listing.get("price", 0)
        if price <= 0:
            continue
        mint = listing.get("tokenMint", "")

        opp = build_opportunity(
            marketplace="magiceden",
            chain="solana",
            asset_id=mint,
            asset_name=mint[:12] + "...",
            listing_price=price,
            fair_value=fair,
            currency="SOL",
            url=f"https://magiceden.io/item-details/{mint}",
            seller=listing.get("seller", ""),
            extra={"collection": symbol, "volume": stats["volume_sol"]},
        )
        if opp:
            opps.append(opp)

    return opps
