"""OpenSea NFT buy execution — fulfill listing via Seaport on EVM chains."""

import logging
import httpx

from config import OPENSEA_API_BASE, OPENSEA_API_KEY, EVM_PRIVATE_KEY
from wallets.evm import get_address, get_gas_price, check_gas_limit

logger = logging.getLogger(__name__)


async def get_listing_order(slug: str, token_id: str, chain: str = "ethereum") -> dict | None:
    """Get the best listing for a specific NFT.

    GET /api/v2/orders/chain/{chain}/protocol/0x00000000000000ADc04C56Bf30aC9d3c0aAF14dC/listings
    """
    headers = {"Accept": "application/json"}
    if OPENSEA_API_KEY:
        headers["X-Api-Key"] = OPENSEA_API_KEY

    # Get listings for this specific token
    url = f"{OPENSEA_API_BASE}/orders/{chain}/seaport/listings"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                url,
                headers=headers,
                params={"limit": 1, "order_by": "eth_price", "order_direction": "asc"},
            )
            if resp.status_code != 200:
                logger.error(f"OpenSea listing fetch failed: {resp.status_code}")
                return None
            data = resp.json()
            orders = data.get("orders", [])
            return orders[0] if orders else None
    except Exception as e:
        logger.error(f"OpenSea listing error: {e}")
        return None


async def fulfill_listing(order_hash: str, protocol_address: str, chain: str = "ethereum") -> dict | None:
    """Get fulfillment data for a listing.

    POST /api/v2/listings/fulfillment_data
    Returns transaction data ready to sign and send.
    """
    fulfiller_address = get_address()
    if not fulfiller_address:
        logger.error("No EVM wallet configured")
        return None

    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if OPENSEA_API_KEY:
        headers["X-Api-Key"] = OPENSEA_API_KEY

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{OPENSEA_API_BASE}/listings/fulfillment_data",
                headers=headers,
                json={
                    "listing": {
                        "hash": order_hash,
                        "chain": chain,
                        "protocol_address": protocol_address,
                    },
                    "fulfiller": {
                        "address": fulfiller_address,
                    },
                },
            )
            if resp.status_code != 200:
                logger.error(f"OpenSea fulfillment failed: {resp.status_code} {resp.text[:300]}")
                return None
            return resp.json()
    except Exception as e:
        logger.error(f"OpenSea fulfillment error: {e}")
        return None


async def execute_opensea_buy(
    order_hash: str,
    protocol_address: str,
    chain: str = "ethereum",
) -> str | None:
    """Execute an OpenSea NFT purchase.

    1. Get fulfillment data (tx to sign)
    2. Check gas limits
    3. Sign and send via web3
    """
    if not EVM_PRIVATE_KEY:
        logger.error("No EVM private key")
        return None

    # Step 1: Get fulfillment tx data
    fulfillment = await fulfill_listing(order_hash, protocol_address, chain)
    if not fulfillment:
        return None

    tx_data = fulfillment.get("fulfillment_data", {}).get("transaction", {})
    if not tx_data:
        logger.error(f"No transaction in fulfillment. Keys: {list(fulfillment.keys())}")
        return None

    to_address = tx_data.get("to", "")
    value = int(tx_data.get("value", "0"))
    calldata = tx_data.get("data", "")

    if not to_address or not calldata:
        logger.error("Incomplete transaction data from OpenSea")
        return None

    # Step 2: Gas check
    gas_price = await get_gas_price(chain)
    if not check_gas_limit(gas_price, chain):
        logger.warning(f"Gas too high on {chain}: {gas_price}")
        return None

    # Step 3: Sign and send via web3
    try:
        from web3 import Web3, Account

        rpc_urls = {
            "ethereum": "https://eth.llamarpc.com",
            "base": "https://mainnet.base.org",
            "polygon": "https://polygon-rpc.com",
        }
        w3 = Web3(Web3.HTTPProvider(rpc_urls.get(chain, rpc_urls["ethereum"])))
        account = Account.from_key(EVM_PRIVATE_KEY)

        nonce = w3.eth.get_transaction_count(account.address)
        tx = {
            "to": Web3.to_checksum_address(to_address),
            "value": value,
            "data": calldata,
            "nonce": nonce,
            "gas": 300000,
            "gasPrice": min(gas_price, int(gas_price * 1.1)),  # slight bump
            "chainId": {"ethereum": 1, "base": 8453, "polygon": 137}.get(chain, 1),
        }

        signed = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        hex_hash = tx_hash.hex()
        logger.info(f"OpenSea buy sent on {chain}: {hex_hash[:16]}...")
        return hex_hash

    except Exception as e:
        logger.error(f"OpenSea buy execution error: {e}")
        return None
