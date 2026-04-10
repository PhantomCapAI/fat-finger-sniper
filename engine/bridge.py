"""Relay.link cross-chain bridging — instant bridge when funds are on wrong chain."""

import logging
import httpx

from config import RELAY_API_BASE, PHANTOM_TREASURY

logger = logging.getLogger(__name__)

# Chain IDs for Relay.link
CHAIN_IDS = {
    "ethereum": 1,
    "base": 8453,
    "polygon": 137,
    "bsc": 56,
    "solana": 792703809,  # Relay's Solana chain ID
}


async def get_quote(
    origin_chain: str,
    dest_chain: str,
    amount_wei: str,
    currency: str = "0x0000000000000000000000000000000000000000",  # native
) -> dict | None:
    """Get a bridge quote from Relay.link.

    POST /quote/v2
    """
    origin_id = CHAIN_IDS.get(origin_chain)
    dest_id = CHAIN_IDS.get(dest_chain)
    if not origin_id or not dest_id:
        return None

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{RELAY_API_BASE}/quote/v2",
                json={
                    "user": PHANTOM_TREASURY,
                    "originChainId": origin_id,
                    "destinationChainId": dest_id,
                    "originCurrency": currency,
                    "destinationCurrency": currency,
                    "amount": amount_wei,
                    "tradeType": "EXACT_INPUT",
                },
            )
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"Relay quote failed: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        logger.error(f"Relay quote error: {e}")
    return None


async def check_config(origin_chain: str, dest_chain: str) -> dict | None:
    """Check if a bridge route is available and get solver capacity.

    GET /config/v2
    """
    origin_id = CHAIN_IDS.get(origin_chain)
    dest_id = CHAIN_IDS.get(dest_chain)
    if not origin_id or not dest_id:
        return None

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{RELAY_API_BASE}/config/v2",
                params={
                    "originChainId": origin_id,
                    "destinationChainId": dest_id,
                    "user": PHANTOM_TREASURY,
                },
            )
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        logger.error(f"Relay config error: {e}")
    return None


async def needs_bridge(required_chain: str, wallet_balances: dict[str, float]) -> str | None:
    """Determine if we need to bridge and from which chain.

    Returns the origin chain to bridge from, or None if funds are on the right chain.
    """
    if wallet_balances.get(required_chain, 0) > 0:
        return None

    # Find the chain with the most balance
    best_chain = max(wallet_balances, key=wallet_balances.get, default=None)
    if best_chain and wallet_balances[best_chain] > 0:
        return best_chain
    return None
