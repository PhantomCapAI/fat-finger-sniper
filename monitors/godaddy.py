"""GoDaddy Auctions domain monitor — detects underpriced domain listings."""

import logging
import httpx

from engine.detector import build_opportunity

logger = logging.getLogger(__name__)

# GoDaddy Auctions public search endpoint
GODADDY_SEARCH = "https://auctions.godaddy.com/trpSearchResults.aspx"
GODADDY_API = "https://auctions.godaddy.com/trpItemListingData.aspx"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}


async def search_domains(query: str = "", tlds: list[str] | None = None, limit: int = 20) -> list[dict]:
    """Search GoDaddy Auctions for domains.

    Uses the public auction feed. Filters for Buy Now listings only.
    """
    if not tlds:
        tlds = ["com", "io", "ai", "xyz"]

    params = {
        "t": 17,  # Buy Now listings
        "action": "search",
        "searchType": 1,
        "q": query,
        "rows": limit,
        "page": 1,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(GODADDY_API, headers=HEADERS, params=params)
            if resp.status_code != 200:
                return []
            data = resp.json()
            return data.get("rows", data) if isinstance(data, dict) else data
    except Exception as e:
        logger.error(f"GoDaddy search error: {e}")
        return []


async def estimate_domain_value(domain: str) -> float:
    """Rough domain value estimation based on characteristics.

    Simple heuristic: short .com domains are worth more.
    Real implementation would use EstiBot/GoDaddy appraisal APIs.
    """
    name, tld = domain.rsplit(".", 1) if "." in domain else (domain, "com")
    base = 10.0

    # Length premium
    if len(name) <= 3:
        base *= 100
    elif len(name) <= 4:
        base *= 50
    elif len(name) <= 5:
        base *= 20
    elif len(name) <= 6:
        base *= 10

    # TLD premium
    tld_mult = {"com": 10, "io": 5, "ai": 8, "net": 3, "org": 2, "xyz": 1}
    base *= tld_mult.get(tld, 1)

    return base


async def scan_domain(listing: dict) -> dict | None:
    """Check a domain listing for fat-finger pricing."""
    domain = listing.get("dn", listing.get("domain", ""))
    price = listing.get("price", listing.get("buyNowPrice", 0))

    if not domain or not price or float(price) <= 0:
        return None

    price = float(price)
    fair = await estimate_domain_value(domain)

    return build_opportunity(
        marketplace="godaddy",
        chain="offchain",
        asset_id=domain,
        asset_name=domain,
        listing_price=price,
        fair_value=fair,
        currency="USD",
        url=f"https://auctions.godaddy.com/trpItemListing.aspx?domain={domain}",
    )


async def scan(queries: list[str] | None = None) -> list[dict]:
    """Scan GoDaddy Auctions for mispriced domains."""
    if not queries:
        queries = ["ai", "crypto", "nft", "defi", "web3"]

    all_opps = []
    for q in queries:
        listings = await search_domains(q, limit=10)
        for listing in listings:
            opp = await scan_domain(listing)
            if opp:
                all_opps.append(opp)
    return all_opps
