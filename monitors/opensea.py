"""OpenSea listing monitor — polls for new listings below floor price."""

import logging
import httpx

from config import OPENSEA_API_KEY, OPENSEA_API_BASE, FAT_FINGER_THRESHOLD_PCT, MIN_FLOOR_ETH

logger = logging.getLogger(__name__)


def _headers() -> dict:
    h = {"Accept": "application/json"}
    if OPENSEA_API_KEY:
        h["X-Api-Key"] = OPENSEA_API_KEY
    return h


async def get_collection_floor(slug: str) -> dict | None:
    """Get collection floor price from OpenSea.

    GET /api/v2/collections/{slug}/stats
    Returns: {"floor_price": float, "floor_price_symbol": str, ...}
    """
    url = f"{OPENSEA_API_BASE}/collections/{slug}/stats"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers=_headers())
        if resp.status_code != 200:
            logger.warning(f"OpenSea stats {slug}: {resp.status_code}")
            return None
        data = resp.json()
        total = data.get("total", {})
        return {
            "floor_price": total.get("floor_price", 0),
            "floor_price_symbol": total.get("floor_price_symbol", "ETH"),
            "num_owners": total.get("num_owners", 0),
            "total_volume": total.get("volume", 0),
        }


async def get_recent_listings(slug: str, limit: int = 50) -> list[dict]:
    """Get recent listing events for a collection.

    GET /api/v2/events/collection/{slug}?event_type=listing&limit=N
    """
    url = f"{OPENSEA_API_BASE}/events/collection/{slug}"
    params = {"event_type": ["listing"], "limit": limit}
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers=_headers(), params=params)
        if resp.status_code != 200:
            logger.warning(f"OpenSea events {slug}: {resp.status_code}")
            return []
        data = resp.json()
        return data.get("asset_events", [])


async def scan_collection(slug: str) -> list[dict]:
    """Scan a collection for fat-finger listings.

    Compares recent listing prices against the collection floor.
    Returns list of flagged listings below threshold.
    """
    floor_data = await get_collection_floor(slug)
    if not floor_data or floor_data["floor_price"] < MIN_FLOOR_ETH:
        return []

    floor = floor_data["floor_price"]
    threshold = floor * (FAT_FINGER_THRESHOLD_PCT / 100)

    listings = await get_recent_listings(slug)
    flagged = []

    for event in listings:
        payment = event.get("payment", {})
        quantity = int(payment.get("quantity", "0"))
        decimals = int(payment.get("decimals", 18))

        if quantity == 0 or decimals == 0:
            continue

        price_native = quantity / (10**decimals)

        if price_native < threshold and price_native > 0:
            nft = event.get("nft", {})
            flagged.append({
                "marketplace": "opensea",
                "chain": event.get("chain", "ethereum"),
                "collection": slug,
                "token_id": nft.get("identifier", "?"),
                "token_name": nft.get("name", nft.get("identifier", "?")),
                "listing_price": price_native,
                "floor_price": floor,
                "discount_pct": round((1 - price_native / floor) * 100, 1),
                "currency": payment.get("symbol", "ETH"),
                "maker": event.get("maker", ""),
                "order_hash": event.get("order_hash", ""),
                "event_timestamp": event.get("event_timestamp", ""),
                "opensea_url": f"https://opensea.io/assets/ethereum/{nft.get('contract', '')}/{nft.get('identifier', '')}",
            })

    flagged.sort(key=lambda x: x["listing_price"])
    return flagged
