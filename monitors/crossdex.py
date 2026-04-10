"""Cross-DEX price comparison — detect mispriced pools and limit orders.

Uses DexScreener (free, no auth) for cross-pool price comparison,
Jupiter for Solana aggregated pricing, and 1inch orderbook for
EVM limit order scanning.

Detection: any single pool/order >85% below consensus = fat finger.
"""

import logging
import statistics

import httpx

from config import JUPITER_API_BASE, JUPITER_API_KEY, MIN_DISCOUNT_PCT
from engine.detector import build_opportunity, classify_confidence

logger = logging.getLogger(__name__)

DEXSCREENER_BASE = "https://api.dexscreener.com/latest/dex"
ONEINCH_ORDERBOOK = "https://api.1inch.dev/orderbook/v4.0"

# Tokens to monitor across DEXes
MONITOR_TOKENS = {
    "SOL": {
        "solana": "So11111111111111111111111111111111111111112",
        "coingecko": "solana",
    },
    "ETH": {
        "ethereum": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",  # WETH
        "coingecko": "ethereum",
    },
    "BTC": {
        "ethereum": "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",  # WBTC
        "solana": "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh",  # WBTC on Solana
        "coingecko": "bitcoin",
    },
}


async def get_dexscreener_prices(token_address: str) -> list[dict]:
    """Get all pool prices for a token across DEXes via DexScreener.

    Returns list of: {dex, chain, price_usd, volume_24h, pair_address}
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{DEXSCREENER_BASE}/tokens/{token_address}")
            if resp.status_code != 200:
                return []
            data = resp.json()
            pairs = data.get("pairs", [])

            results = []
            for pair in pairs:
                price_str = pair.get("priceUsd")
                if not price_str:
                    continue
                try:
                    price = float(price_str)
                except (ValueError, TypeError):
                    continue

                vol_24h = pair.get("volume", {}).get("h24", 0) or 0
                results.append({
                    "dex": pair.get("dexId", "?"),
                    "chain": pair.get("chainId", "?"),
                    "price_usd": price,
                    "volume_24h": float(vol_24h),
                    "pair_address": pair.get("pairAddress", ""),
                    "base_symbol": pair.get("baseToken", {}).get("symbol", "?"),
                    "quote_symbol": pair.get("quoteToken", {}).get("symbol", "?"),
                    "url": pair.get("url", ""),
                })
            return results
    except Exception as e:
        logger.error(f"DexScreener error: {e}")
        return []


async def get_jupiter_price(mint: str) -> float | None:
    """Get Jupiter aggregated price for a Solana token."""
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
            token = data.get("data", {}).get(mint)
            if token:
                return float(token.get("price", 0))
    except Exception:
        pass
    return None


def compute_consensus_price(prices: list[dict], min_volume: float = 1000) -> float | None:
    """Compute consensus price from pool data.

    Uses volume-weighted median of pools with >$1000 24h volume.
    Filters out dust pools that could skew the result.
    """
    valid = [p for p in prices if p["volume_24h"] >= min_volume and p["price_usd"] > 0]
    if len(valid) < 2:
        return None

    # Volume-weighted average
    total_vol = sum(p["volume_24h"] for p in valid)
    if total_vol <= 0:
        return None

    vwap = sum(p["price_usd"] * p["volume_24h"] for p in valid) / total_vol
    return vwap


def find_outliers(prices: list[dict], consensus: float, threshold_pct: float = 85) -> list[dict]:
    """Find pools with prices significantly below consensus.

    An outlier is any pool where price is >threshold_pct below consensus.
    """
    outliers = []
    for p in prices:
        if p["price_usd"] <= 0 or consensus <= 0:
            continue
        discount = (1 - p["price_usd"] / consensus) * 100
        if discount >= threshold_pct:
            outliers.append({
                **p,
                "consensus_price": consensus,
                "discount_pct": round(discount, 1),
                "confidence": classify_confidence(discount),
            })
    return outliers


async def scan_token(symbol: str, token_info: dict) -> list[dict]:
    """Scan all pools for a specific token across chains."""
    all_prices = []
    opps = []

    # Gather prices from all chains
    for chain, address in token_info.items():
        if chain in ("coingecko",):
            continue
        prices = await get_dexscreener_prices(address)
        all_prices.extend(prices)

    # Also get Jupiter aggregated price as reference
    sol_address = token_info.get("solana")
    if sol_address:
        jup_price = await get_jupiter_price(sol_address)
        if jup_price:
            all_prices.append({
                "dex": "jupiter_aggregated",
                "chain": "solana",
                "price_usd": jup_price,
                "volume_24h": 999999999,  # High weight — aggregator is reliable
                "pair_address": "",
                "base_symbol": symbol,
                "quote_symbol": "USD",
                "url": "",
            })

    if len(all_prices) < 3:
        return []

    consensus = compute_consensus_price(all_prices)
    if not consensus:
        return []

    outliers = find_outliers(all_prices, consensus)

    for outlier in outliers:
        opp = build_opportunity(
            marketplace=f"dex:{outlier['dex']}",
            chain=outlier["chain"],
            asset_id=outlier["pair_address"],
            asset_name=f"{symbol} on {outlier['dex']} ({outlier['chain']})",
            listing_price=outlier["price_usd"],
            fair_value=consensus,
            currency="USD",
            url=outlier.get("url", ""),
            extra={
                "dex": outlier["dex"],
                "volume_24h": outlier["volume_24h"],
                "consensus_price": consensus,
                "total_pools_checked": len(all_prices),
            },
        )
        if opp:
            opps.append(opp)

    return opps


async def scan_1inch_orderbook(chain_id: int = 1, token_address: str = "", api_key: str = "") -> list[dict]:
    """Scan 1inch limit orderbook for mispriced orders.

    Requires API key. Returns empty if no key provided.
    """
    if not api_key:
        return []

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
            params = {"limit": 100}
            if token_address:
                params["makerAsset"] = token_address

            resp = await client.get(
                f"{ONEINCH_ORDERBOOK}/{chain_id}/all",
                headers=headers,
                params=params,
            )
            if resp.status_code != 200:
                return []

            orders = resp.json()
            if not isinstance(orders, list):
                return []

            # TODO: Parse order amounts and compare against market price
            # Each order has makerAsset, takerAsset, makingAmount, takingAmount
            # Effective price = takingAmount / makingAmount
            # Compare against consensus price
            return []
    except Exception as e:
        logger.error(f"1inch orderbook error: {e}")
        return []


async def scan() -> list[dict]:
    """Scan all monitored tokens across all DEXes for mispricing."""
    all_opps = []
    for symbol, token_info in MONITOR_TOKENS.items():
        try:
            opps = await scan_token(symbol, token_info)
            if opps:
                all_opps.extend(opps)
                for o in opps:
                    logger.info(
                        f"CROSS-DEX: {symbol} on {o.get('metadata',{}).get('dex','?')} "
                        f"@ ${o['listing_price']:.2f} vs consensus ${o['fair_value']:.2f} "
                        f"({o['discount_pct']}% off, {o['confidence']})"
                    )
        except Exception as e:
            logger.error(f"Cross-DEX scan {symbol}: {e}")
    return all_opps
