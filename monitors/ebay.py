"""eBay monitor — Buy It Now with Immediate Payment only.

Uses eBay Browse API (public, no auth for search).
Only targets fixed-price listings with immediate payment required.
"""

import logging
import httpx

from engine.detector import build_opportunity

logger = logging.getLogger(__name__)

EBAY_BROWSE = "https://svcs.ebay.com/services/search/FindingService/v1"
EBAY_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}


async def search_listings(
    query: str, category_id: str = "", min_price: float = 0, limit: int = 20
) -> list[dict]:
    """Search eBay for Buy It Now listings.

    Uses eBay's Finding API for BIN-only, immediate payment items.
    """
    params = {
        "OPERATION-NAME": "findItemsByKeywords",
        "SERVICE-VERSION": "1.0.0",
        "RESPONSE-DATA-FORMAT": "JSON",
        "keywords": query,
        "paginationInput.entriesPerPage": str(limit),
        "itemFilter(0).name": "ListingType",
        "itemFilter(0).value": "FixedPrice",
        "sortOrder": "PricePlusShippingLowest",
    }

    if category_id:
        params["categoryId"] = category_id

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(EBAY_BROWSE, headers=EBAY_HEADERS, params=params)
            if resp.status_code != 200:
                return []
            data = resp.json()
            results = (
                data.get("findItemsByKeywordsResponse", [{}])[0]
                .get("searchResult", [{}])[0]
                .get("item", [])
            )
            return results
    except Exception as e:
        logger.error(f"eBay search error: {e}")
        return []


async def scan_listing(item: dict, fair_value: float) -> dict | None:
    """Check an eBay listing against known fair value."""
    price_info = item.get("sellingStatus", [{}])[0]
    current_price = price_info.get("currentPrice", [{}])[0]
    price = float(current_price.get("__value__", 0))
    currency = current_price.get("@currencyId", "USD")

    if price <= 0 or fair_value <= 0:
        return None

    title = item.get("title", [""])[0] if isinstance(item.get("title"), list) else item.get("title", "")
    item_id = item.get("itemId", [""])[0] if isinstance(item.get("itemId"), list) else item.get("itemId", "")

    return build_opportunity(
        marketplace="ebay",
        chain="offchain",
        asset_id=str(item_id),
        asset_name=title[:80],
        listing_price=price,
        fair_value=fair_value,
        currency=currency,
        url=f"https://www.ebay.com/itm/{item_id}" if item_id else "",
    )


async def scan(watchlist: dict[str, float] | None = None) -> list[dict]:
    """Scan eBay for mispriced BIN listings.

    watchlist: dict of search query -> expected fair value in USD
    """
    if not watchlist:
        return []

    all_opps = []
    for query, fair_value in watchlist.items():
        listings = await search_listings(query, limit=10)
        for item in listings:
            opp = await scan_listing(item, fair_value)
            if opp:
                all_opps.append(opp)
    return all_opps
