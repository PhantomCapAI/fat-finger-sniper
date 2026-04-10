"""Jupiter DEX misprice monitor — detects pool dislocations on Solana.

Strategy: for each watched token, fetch Jupiter's aggregated reference price
(what the token is "worth" per Jupiter's Price API) and compare against the
effective price you would actually pay via a 10-USDC quote. Under normal
conditions these agree within ~0.5% (fees + tiny slippage). A gap larger
than DEX_MEDIUM_THRESHOLD_PCT indicates a pool imbalance the aggregator
can see but the cheapest executable route can't fully smooth out.

This is NOT a front-running signal. By the time we fetch the quote, any
sub-second MEV has already been extracted by Jito searchers colocated with
validator leaders. What this actually catches is *persistent* dislocations
that live across multiple blocks — thin-liquidity tokens or delayed
cross-DEX rebalancing. Useful as a research / alerting signal. Not useful
for competing head-to-head with Rust MEV bots on hot pairs.

Max confidence is HIGH. CRITICAL is deliberately unreachable from this
monitor so the YOLO override in engine/executor.py can never trigger off
a DEX detection.
"""

import logging
import httpx

from config import JUPITER_API_BASE, JUPITER_API_KEY

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

# Decimals per mint — Jupiter quote outAmount is in base units.
TOKEN_DECIMALS = {
    "So11111111111111111111111111111111111111112": 9,   # SOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": 6,   # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": 6,   # USDT
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN": 6,    # JUP
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": 5,   # BONK
    "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm": 6,   # WIF
}

# DEX-specific thresholds — NFT markets use 70-95%+ tiers, DEX mispricings
# are an order of magnitude smaller because Jupiter aggregates across routes.
DEX_MEDIUM_THRESHOLD_PCT = 5.0
DEX_HIGH_THRESHOLD_PCT = 10.0

# Quote input size. 10 USDC is small enough that slippage is minimal on
# any token with real liquidity — if the effective price deviates from
# reference at this size, it's a genuine pool issue not slippage noise.
QUOTE_INPUT_USDC = 10
QUOTE_INPUT_BASE_UNITS = QUOTE_INPUT_USDC * 1_000_000  # USDC is 6 decimals


async def get_price(mint: str) -> float | None:
    """Get token price in USD via Jupiter Price API v2.

    GET https://api.jup.ag/price/v2?ids={mint}
    """
    headers = {}
    if JUPITER_API_KEY:
        headers["x-api-key"] = JUPITER_API_KEY
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{JUPITER_API_BASE}/price/v2",
                params={"ids": mint},
                headers=headers,
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
    """Get swap quote via Jupiter v6 quote API.

    GET https://api.jup.ag/quote/v6?inputMint=X&outputMint=Y&amount=Z
    """
    headers = {}
    if JUPITER_API_KEY:
        headers["x-api-key"] = JUPITER_API_KEY
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
                headers=headers,
            )
            if resp.status_code != 200:
                return None
            return resp.json()
    except Exception as e:
        logger.error(f"Jupiter quote error: {e}")
    return None


def _classify_dex_confidence(deviation_pct: float) -> str:
    """Confidence ladder for DEX detections.

    Never returns CRITICAL — that tier is reserved for NFT/hard asset
    detections and is the only tier that triggers the YOLO override.
    """
    if deviation_pct >= DEX_HIGH_THRESHOLD_PCT:
        return "HIGH"
    if deviation_pct >= DEX_MEDIUM_THRESHOLD_PCT:
        return "MEDIUM"
    return "LOW"


async def scan_pair(token_name: str, mint: str) -> dict | None:
    """Compare Jupiter's aggregated reference price against an actual buy quote.

    Fetches the USD reference price for the token, then simulates a 10 USDC
    buy via Jupiter Quote API. Computes the effective price paid
    (USD_in / tokens_received) and compares against the reference.

    Returns an opportunity dict if the effective price is meaningfully BELOW
    the reference (i.e. you're getting more tokens than the quoted "fair"
    price would predict). Returns None otherwise.
    """
    usdc_mint = WATCH_TOKENS["USDC"]
    if mint == usdc_mint:
        return None

    ref_price = await get_price(mint)
    if not ref_price or ref_price <= 0:
        return None

    quote = await get_quote(usdc_mint, mint, QUOTE_INPUT_BASE_UNITS)
    if not quote:
        return None

    out_amount_raw = int(quote.get("outAmount", "0"))
    if out_amount_raw <= 0:
        return None

    decimals = TOKEN_DECIMALS.get(mint)
    if decimals is None:
        logger.debug(f"No decimals entry for {mint}, skipping")
        return None

    tokens_received = out_amount_raw / (10 ** decimals)
    if tokens_received <= 0:
        return None

    # Effective USD price paid per token. Signed: positive means you pay MORE
    # than reference (bad deal), negative means you pay LESS (opportunity).
    effective_price = float(QUOTE_INPUT_USDC) / tokens_received
    signed_deviation_pct = (effective_price / ref_price - 1) * 100

    # We only care about opportunities where the route is BELOW reference.
    if signed_deviation_pct >= 0:
        return None

    discount_pct = round(abs(signed_deviation_pct), 2)
    confidence = _classify_dex_confidence(discount_pct)
    if confidence == "LOW":
        return None

    logger.info(
        f"Jupiter DEX signal: {token_name} effective=${effective_price:.6f} "
        f"ref=${ref_price:.6f} discount={discount_pct}% confidence={confidence}"
    )

    return {
        "marketplace": "jupiter",
        "chain": "solana",
        "asset_id": mint,
        "asset_name": token_name,
        "listing_price": effective_price,
        "fair_value": ref_price,
        "discount_pct": discount_pct,
        "confidence": confidence,
        "currency": "USD",
        "url": f"https://jup.ag/swap/USDC-{token_name}",
        "seller": "",
        "metadata": {
            "ref_price_usd": ref_price,
            "effective_price_usd": effective_price,
            "signed_deviation_pct": round(signed_deviation_pct, 4),
            "quote_input_usdc": QUOTE_INPUT_USDC,
            "quote_output_raw": out_amount_raw,
            "tokens_received": tokens_received,
            "route_count": len(quote.get("routePlan", [])),
        },
    }


async def scan() -> list[dict]:
    """Scan all watched tokens for DEX mispricing."""
    opps: list[dict] = []
    for name, mint in WATCH_TOKENS.items():
        if name in ("USDC", "USDT"):
            continue
        try:
            result = await scan_pair(name, mint)
            if result:
                opps.append(result)
        except Exception as e:
            logger.error(f"Jupiter scan_pair {name} failed: {e}")
    return opps
