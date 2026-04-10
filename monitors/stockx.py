"""StockX monitor — sneakers/streetwear price monitoring.

StockX does not have a public API. We use their public product pages
and the browse API that powers their search frontend.
"""

import logging
import httpx

from engine.detector import build_opportunity

logger = logging.getLogger(__name__)

STOCKX_BROWSE = "https://stockx.com/api/browse"
STOCKX_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "App-Platform": "Iron",
    "App-Version": "2024.12.18.1",
}


async def search_products(query: str, limit: int = 20) -> list[dict]:
    """Search StockX for products."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                STOCKX_BROWSE,
                headers=STOCKX_HEADERS,
                params={"_search": query, "page": 1, "resultsPerPage": limit},
            )
            if resp.status_code != 200:
                logger.warning(f"StockX search failed: {resp.status_code}")
                return []
            data = resp.json()
            return data.get("Products", [])
    except Exception as e:
        logger.error(f"StockX search error: {e}")
        return []


async def scan_product(product: dict) -> dict | None:
    """Check a StockX product for mispriced asks.

    Compares lowest ask against last sale price.
    """
    market = product.get("market", {})
    lowest_ask = market.get("lowestAsk", 0)
    last_sale = market.get("lastSale", 0)

    if not lowest_ask or not last_sale or lowest_ask <= 0 or last_sale <= 0:
        return None

    title = product.get("title", "Unknown")
    url_key = product.get("urlKey", "")

    return build_opportunity(
        marketplace="stockx",
        chain="offchain",
        asset_id=product.get("styleId", url_key),
        asset_name=title,
        listing_price=lowest_ask,
        fair_value=last_sale,
        currency="USD",
        url=f"https://stockx.com/{url_key}" if url_key else "",
        extra={"last_sale": last_sale, "retail_price": market.get("retailPrice", 0)},
    )


async def scan(queries: list[str] | None = None) -> list[dict]:
    """Scan StockX watchlist for fat-finger asks."""
    if not queries:
        queries = ["jordan 1", "yeezy", "dunk low", "travis scott"]

    all_opps = []
    for q in queries:
        products = await search_products(q, limit=10)
        for product in products:
            opp = await scan_product(product)
            if opp:
                all_opps.append(opp)
    return all_opps
