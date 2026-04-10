"""Tensor monitor — Solana NFT listings via GraphQL API."""

import logging
import httpx

from config import TENSOR_API_BASE, TENSOR_API_KEY
from engine.detector import compute_fair_value, build_opportunity

logger = logging.getLogger(__name__)

LAMPORTS_PER_SOL = 1_000_000_000


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if TENSOR_API_KEY:
        h["X-TENSOR-API-KEY"] = TENSOR_API_KEY
    return h


async def get_collection_stats(slug: str) -> dict | None:
    """Query collectionStatsV2 via GraphQL."""
    query = """
    query CollStats($slug: String!) {
      instrumentTV2(slug: $slug) {
        statsV2 {
          currency
          buyNowPrice
          sellNowPrice
          numListed
          numMints
          floor1h
          floor24h
          floor7d
          volume1h
          volume24h
          volume7d
          volumeAll
          salesCount24h
        }
        slug
        name
      }
    }
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                TENSOR_API_BASE,
                headers=_headers(),
                json={"query": query, "variables": {"slug": slug}},
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            instrument = data.get("data", {}).get("instrumentTV2")
            if not instrument:
                return None
            stats = instrument.get("statsV2", {})
            buy_now = int(stats.get("buyNowPrice") or 0)
            return {
                "name": instrument.get("name", slug),
                "floor_price_sol": buy_now / LAMPORTS_PER_SOL,
                "volume_all_sol": int(stats.get("volumeAll") or 0) / LAMPORTS_PER_SOL,
                "listed_count": int(stats.get("numListed") or 0),
                "sales_24h": int(stats.get("salesCount24h") or 0),
            }
    except Exception as e:
        logger.error(f"Tensor stats error: {e}")
        return None


async def get_active_listings(slug: str, limit: int = 20) -> list[dict]:
    """Query active listings sorted by price."""
    query = """
    query Listings($slug: String!, $limit: Int) {
      activeListingsV2(slug: $slug, sortBy: PriceAsc, limit: $limit) {
        txs {
          tx {
            grossAmount
            grossAmountUnit
          }
          mint {
            onchainId
          }
          seller
        }
      }
    }
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                TENSOR_API_BASE,
                headers=_headers(),
                json={"query": query, "variables": {"slug": slug, "limit": limit}},
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            return data.get("data", {}).get("activeListingsV2", {}).get("txs", [])
    except Exception as e:
        logger.error(f"Tensor listings error: {e}")
        return []


async def scan(slug: str) -> list[dict]:
    """Scan Tensor for fat-finger listings."""
    stats = await get_collection_stats(slug)
    if not stats or stats["floor_price_sol"] <= 0:
        return []

    fair = compute_fair_value(stats["floor_price_sol"])
    listings = await get_active_listings(slug)
    opps = []

    for item in listings:
        tx = item.get("tx", {})
        amount = int(tx.get("grossAmount") or 0)
        price_sol = amount / LAMPORTS_PER_SOL
        if price_sol <= 0:
            continue

        mint_id = item.get("mint", {}).get("onchainId", "")
        seller = item.get("seller", "")

        opp = build_opportunity(
            marketplace="tensor",
            chain="solana",
            asset_id=mint_id,
            asset_name=mint_id[:12] + "...",
            listing_price=price_sol,
            fair_value=fair,
            currency="SOL",
            url=f"https://www.tensor.trade/item/{mint_id}",
            seller=seller,
            extra={"collection": slug, "volume": stats["volume_all_sol"]},
        )
        if opp:
            opps.append(opp)

    return opps
