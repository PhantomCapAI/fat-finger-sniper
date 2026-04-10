"""Magic Eden listing monitor — polls for Solana NFT listings below floor."""

import logging
import httpx

from config import MAGICEDEN_API_BASE, FAT_FINGER_THRESHOLD_PCT, MIN_FLOOR_SOL

logger = logging.getLogger(__name__)

LAMPORTS_PER_SOL = 1_000_000_000


async def get_collection_stats(symbol: str) -> dict | None:
    """Get collection stats from Magic Eden.

    GET /v2/collections/{symbol}/stats
    Returns: floorPrice (lamports), listedCount, volumeAll, avgPrice24hr
    """
    url = f"{MAGICEDEN_API_BASE}/collections/{symbol}/stats"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url)
        if resp.status_code != 200:
            logger.warning(f"MagicEden stats {symbol}: {resp.status_code}")
            return None
        data = resp.json()
        floor_lamports = data.get("floorPrice", 0)
        return {
            "floor_price_lamports": floor_lamports,
            "floor_price_sol": floor_lamports / LAMPORTS_PER_SOL if floor_lamports else 0,
            "listed_count": data.get("listedCount", 0),
            "volume_all_sol": (data.get("volumeAll", 0) or 0) / LAMPORTS_PER_SOL,
            "avg_price_24h_sol": (data.get("avgPrice24hr", 0) or 0) / LAMPORTS_PER_SOL,
        }


async def get_listings(symbol: str, limit: int = 20, offset: int = 0) -> list[dict]:
    """Get active listings for a collection, sorted by price ascending.

    GET /v2/collections/{symbol}/listings?offset=0&limit=20
    Returns array of: {price, tokenMint, tokenAddress, seller, ...}
    """
    url = f"{MAGICEDEN_API_BASE}/collections/{symbol}/listings"
    params = {"offset": offset, "limit": limit}
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, params=params)
        if resp.status_code != 200:
            logger.warning(f"MagicEden listings {symbol}: {resp.status_code}")
            return []
        return resp.json()


async def scan_collection(symbol: str) -> list[dict]:
    """Scan a Solana collection for fat-finger listings.

    Gets listings sorted by price (lowest first) and flags any
    that are below the threshold percentage of floor price.
    """
    stats = await get_collection_stats(symbol)
    if not stats or stats["floor_price_sol"] < MIN_FLOOR_SOL:
        return []

    floor_sol = stats["floor_price_sol"]
    threshold_sol = floor_sol * (FAT_FINGER_THRESHOLD_PCT / 100)

    listings = await get_listings(symbol, limit=20)
    flagged = []

    for listing in listings:
        price_sol = listing.get("price", 0)
        if price_sol <= 0:
            continue

        if price_sol < threshold_sol:
            token_mint = listing.get("tokenMint", "")
            flagged.append({
                "marketplace": "magiceden",
                "chain": "solana",
                "collection": symbol,
                "token_mint": token_mint,
                "token_address": listing.get("tokenAddress", ""),
                "listing_price_sol": price_sol,
                "floor_price_sol": floor_sol,
                "discount_pct": round((1 - price_sol / floor_sol) * 100, 1),
                "currency": "SOL",
                "seller": listing.get("seller", ""),
                "magiceden_url": f"https://magiceden.io/item-details/{token_mint}",
            })

    flagged.sort(key=lambda x: x["listing_price_sol"])
    return flagged
