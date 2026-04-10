"""Honeypot detection — skip scams, unverified collections, new sellers."""

import logging
import httpx

from config import (
    MIN_COLLECTION_VOLUME_ETH, MIN_COLLECTION_VOLUME_SOL,
    MIN_SELLER_HISTORY, OPENSEA_API_BASE, OPENSEA_API_KEY,
    MAGICEDEN_API_BASE,
)
from db import is_blacklisted

logger = logging.getLogger(__name__)


async def check_eth_collection(slug: str, volume: float | None = None) -> dict:
    """Verify an ETH NFT collection is legitimate.

    Checks:
    - Not on blacklist
    - Has minimum trading volume
    - Collection exists and is established
    """
    if await is_blacklisted("collection", slug):
        return {"safe": False, "reason": "blacklisted"}

    if volume is not None and volume < MIN_COLLECTION_VOLUME_ETH:
        return {"safe": False, "reason": f"low_volume ({volume:.2f} ETH < {MIN_COLLECTION_VOLUME_ETH})"}

    return {"safe": True, "reason": "passed"}


async def check_sol_collection(symbol: str, volume_sol: float | None = None) -> dict:
    """Verify a Solana NFT collection is legitimate."""
    if await is_blacklisted("collection", symbol):
        return {"safe": False, "reason": "blacklisted"}

    if volume_sol is not None and volume_sol < MIN_COLLECTION_VOLUME_SOL:
        return {"safe": False, "reason": f"low_volume ({volume_sol:.2f} SOL < {MIN_COLLECTION_VOLUME_SOL})"}

    return {"safe": True, "reason": "passed"}


async def check_seller_eth(seller_address: str) -> dict:
    """Check if an ETH seller has history (not a fresh wallet)."""
    if not seller_address:
        return {"safe": False, "reason": "no_seller_address"}

    if await is_blacklisted("seller", seller_address.lower()):
        return {"safe": False, "reason": "blacklisted_seller"}

    # Check via OpenSea events for seller history
    if OPENSEA_API_KEY:
        try:
            headers = {"X-Api-Key": OPENSEA_API_KEY, "Accept": "application/json"}
            url = f"{OPENSEA_API_BASE}/events/accounts/{seller_address}"
            params = {"event_type": ["sale"], "limit": MIN_SELLER_HISTORY}
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=headers, params=params)
                if resp.status_code == 200:
                    events = resp.json().get("asset_events", [])
                    if len(events) < MIN_SELLER_HISTORY:
                        return {"safe": False, "reason": f"new_seller ({len(events)} sales < {MIN_SELLER_HISTORY})"}
        except Exception as e:
            logger.warning(f"Seller check failed: {e}")

    return {"safe": True, "reason": "passed"}


async def check_seller_sol(seller_address: str) -> dict:
    """Check if a Solana seller has history."""
    if not seller_address:
        return {"safe": False, "reason": "no_seller_address"}

    if await is_blacklisted("seller", seller_address):
        return {"safe": False, "reason": "blacklisted_seller"}

    # Check via Magic Eden for seller activity
    try:
        url = f"{MAGICEDEN_API_BASE}/wallets/{seller_address}/activities"
        params = {"offset": 0, "limit": MIN_SELLER_HISTORY}
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params)
            if resp.status_code == 200:
                activities = resp.json()
                if len(activities) < MIN_SELLER_HISTORY:
                    return {"safe": False, "reason": f"new_seller ({len(activities)} activities)"}
    except Exception as e:
        logger.warning(f"SOL seller check failed: {e}")

    return {"safe": True, "reason": "passed"}


async def is_safe_opportunity(opp: dict) -> dict:
    """Run full honeypot check on an opportunity."""
    chain = opp.get("chain", "")
    marketplace = opp.get("marketplace", "")
    collection = opp.get("metadata", {}).get("collection", "")
    seller = opp.get("seller", "")
    volume = opp.get("metadata", {}).get("volume")

    if chain == "solana":
        col_check = await check_sol_collection(collection, volume)
        if not col_check["safe"]:
            return col_check
        seller_check = await check_seller_sol(seller)
        return seller_check
    else:
        col_check = await check_eth_collection(collection, volume)
        if not col_check["safe"]:
            return col_check
        seller_check = await check_seller_eth(seller)
        return seller_check
