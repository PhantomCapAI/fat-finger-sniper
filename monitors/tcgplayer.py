"""TCGPlayer monitor — Pokemon, Magic, Yu-Gi-Oh trading card misprices.

Uses TCGPlayer's public product/pricing API.
Only monitors Buy It Now listings.
"""

import logging
import httpx

from engine.detector import build_opportunity

logger = logging.getLogger(__name__)

TCGP_BASE = "https://mp-search-api.tcgplayer.com/v1/search/request"
TCGP_PRICE_BASE = "https://mpapi.tcgplayer.com/v2/product"

TCGP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

GAME_IDS = {
    "pokemon": 3,
    "magic": 1,
    "yugioh": 2,
}


async def search_cards(game: str, query: str, limit: int = 20) -> list[dict]:
    """Search TCGPlayer for cards in a specific game."""
    game_id = GAME_IDS.get(game.lower())
    if not game_id:
        return []

    payload = {
        "algorithm": "",
        "from": 0,
        "size": limit,
        "filters": {
            "term": {"productLineName": [game.title()]},
            "range": {},
            "match": {},
        },
        "listingSearch": {
            "filters": {
                "term": {"sellerStatus": "Live", "channelId": 0},
                "range": {"quantity": {"gte": 1}},
                "exclude": {"channelExclusion": 0},
            },
        },
        "context": {"cart": {}, "shippingCountry": "US"},
        "sort": {"field": "market-price", "order": "asc"},
    }

    if query:
        payload["filters"]["match"] = {"productName": query}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{TCGP_BASE}?q={query}&isList=false",
                headers=TCGP_HEADERS,
                json=payload,
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            results = data.get("results", [{}])
            if results:
                return results[0].get("results", [])
    except Exception as e:
        logger.error(f"TCGPlayer search error: {e}")
    return []


async def get_product_pricing(product_id: int) -> dict | None:
    """Get pricing data for a specific product."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{TCGP_PRICE_BASE}/{product_id}/pricepoints",
                headers=TCGP_HEADERS,
            )
            if resp.status_code != 200:
                return None
            return resp.json()
    except Exception as e:
        logger.error(f"TCGPlayer pricing error: {e}")
    return None


async def scan_card(card: dict) -> dict | None:
    """Check a card listing for fat-finger pricing."""
    product_name = card.get("productName", "")
    product_id = card.get("productId", 0)
    market_price = card.get("marketPrice", 0)
    lowest_price = card.get("lowestPrice", 0)

    if not market_price or not lowest_price or market_price <= 0 or lowest_price <= 0:
        return None

    return build_opportunity(
        marketplace="tcgplayer",
        chain="offchain",
        asset_id=str(product_id),
        asset_name=product_name,
        listing_price=lowest_price,
        fair_value=market_price,
        currency="USD",
        url=f"https://www.tcgplayer.com/product/{product_id}" if product_id else "",
        extra={"game": card.get("productLineName", ""), "set": card.get("setName", "")},
    )


async def scan(games: list[str] | None = None, queries: list[str] | None = None) -> list[dict]:
    """Scan TCGPlayer for mispriced cards."""
    if not games:
        games = ["pokemon", "magic", "yugioh"]
    if not queries:
        queries = ["charizard", "black lotus", "blue-eyes"]

    all_opps = []
    for game, query in zip(games, queries):
        cards = await search_cards(game, query, limit=10)
        for card in cards:
            opp = await scan_card(card)
            if opp:
                all_opps.append(opp)
    return all_opps
