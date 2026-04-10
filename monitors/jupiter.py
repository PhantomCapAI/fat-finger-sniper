"""Jupiter/Raydium DEX misprice monitor — detects pricing anomalies on Solana DEXes."""

import logging
import httpx

from config import JUPITER_API_BASE
from engine.detector import build_opportunity

logger = logging.getLogger(__name__)

# Well-known Solana tokens to monitor for mispricing
WATCH_TOKENS = {
    "SOL": "So11111111111111111111111111111111111111112",
    "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "USDT": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    "JUP": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "BONK": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    "WIF": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
}


async def get_price(mint: str) -> float | None:
    """Get token price in USD via Jupiter Price API v2.

    GET https://api.jup.ag/price/v2?ids={mint}
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{JUPITER_API_BASE}/price/v2",
                params={"ids": mint},
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            token_data = data.get("data", {}).get(mint)
            if token_data:
                return float(token_data.get("price", 0))
    except Exception as e:
        logger.error(f"Jupiter price error: {e}")
    return None


async def get_quote(input_mint: str, output_mint: str, amount: int) -> dict | None:
    """Get swap quote to check for DEX mispricing.

    GET https://api.jup.ag/quote/v6?inputMint=X&outputMint=Y&amount=Z
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{JUPITER_API_BASE}/quote/v6",
                params={
                    "inputMint": input_mint,
                    "outputMint": output_mint,
                    "amount": str(amount),
                    "slippageBps": "50",
                },
            )
            if resp.status_code != 200:
                return None
            return resp.json()
    except Exception as e:
        logger.error(f"Jupiter quote error: {e}")
    return None


async def scan_pair(token_name: str, mint: str) -> dict | None:
    """Check a token pair for pricing anomalies.

    Compares Jupiter aggregated price against individual route prices
    to detect if any single DEX has a significantly different price.
    """
    usdc_mint = WATCH_TOKENS["USDC"]
    price = await get_price(mint)
    if not price or price <= 0:
        return None

    # Get a quote for a small trade to see route pricing
    # 1 USDC = 1_000_000 (6 decimals)
    quote = await get_quote(usdc_mint, mint, 1_000_000)
    if not quote:
        return None

    out_amount = int(quote.get("outAmount", "0"))
    if out_amount <= 0:
        return None

    # Check route plans for pricing discrepancies
    route_plan = quote.get("routePlan", [])
    if len(route_plan) < 2:
        return None  # Need multiple routes to compare

    # The aggregator already picks the best — check if any route is wildly different
    # This is simplified; real implementation would compare across multiple quote sizes
    return None  # DEX arb detection needs more sophisticated logic


async def scan() -> list[dict]:
    """Scan all watched tokens for DEX mispricing."""
    opps = []
    for name, mint in WATCH_TOKENS.items():
        if name in ("USDC", "USDT"):
            continue
        result = await scan_pair(name, mint)
        if result:
            opps.append(result)
    return opps
