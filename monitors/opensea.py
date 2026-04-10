"""OpenSea monitor — ETH NFT listings via events API."""

import logging
import httpx

from config import OPENSEA_API_KEY, OPENSEA_API_BASE
from engine.detector import compute_fair_value, build_opportunity

logger = logging.getLogger(__name__)


def _headers() -> dict:
    h = {"Accept": "application/json"}
    if OPENSEA_API_KEY:
        h["X-Api-Key"] = OPENSEA_API_KEY
    return h


async def get_collection_stats(slug: str) -> dict | None:
    """GET /api/v2/collections/{slug}/stats"""
    url = f"{OPENSEA_API_BASE}/collections/{slug}/stats"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers=_headers())
        if resp.status_code != 200:
            return None
        data = resp.json()
        total = data.get("total", {})
        return {
            "floor_price": total.get("floor_price", 0),
            "floor_price_symbol": total.get("floor_price_symbol", "ETH"),
            "volume": total.get("volume", 0),
            "sales": total.get("sales", 0),
        }


async def get_listing_events(slug: str, limit: int = 50) -> list[dict]:
    """GET /api/v2/events/collection/{slug}?event_type=listing"""
    url = f"{OPENSEA_API_BASE}/events/collection/{slug}"
    params = {"event_type": ["listing"], "limit": limit}
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers=_headers(), params=params)
        if resp.status_code != 200:
            return []
        return resp.json().get("asset_events", [])


async def scan(slug: str) -> list[dict]:
    """Scan a collection for fat-finger listings."""
    stats = await get_collection_stats(slug)
    if not stats or stats["floor_price"] <= 0:
        return []

    fair = compute_fair_value(stats["floor_price"])
    events = await get_listing_events(slug)
    opps = []

    for event in events:
        payment = event.get("payment", {})
        quantity = int(payment.get("quantity", "0"))
        decimals = int(payment.get("decimals", 18))
        if quantity == 0:
            continue

        price = quantity / (10 ** decimals)
        nft = event.get("nft", {})
        token_id = nft.get("identifier", "?")
        contract = nft.get("contract", "")

        opp = build_opportunity(
            marketplace="opensea",
            chain="ethereum",
            asset_id=f"{contract}:{token_id}",
            asset_name=nft.get("name", f"#{token_id}"),
            listing_price=price,
            fair_value=fair,
            currency=payment.get("symbol", "ETH"),
            url=f"https://opensea.io/assets/ethereum/{contract}/{token_id}",
            seller=event.get("maker", ""),
            extra={"collection": slug, "volume": stats["volume"]},
        )
        if opp:
            opps.append(opp)

    return opps
