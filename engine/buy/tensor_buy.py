"""Tensor NFT buy execution — Solana."""

import logging
import httpx

from config import TENSOR_API_BASE, TENSOR_API_KEY
from wallets.solana import get_pubkey, sign_and_send_versioned_tx, confirm_tx

logger = logging.getLogger(__name__)


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if TENSOR_API_KEY:
        h["X-TENSOR-API-KEY"] = TENSOR_API_KEY
    return h


async def execute_tensor_buy(mint: str, price_lamports: int, seller: str) -> str | None:
    """Buy an NFT on Tensor via their GraphQL buy transaction endpoint.

    Tensor's tcompBuy mutation returns a pre-built transaction.
    """
    pubkey = get_pubkey()
    if not pubkey:
        logger.error("No SOL wallet configured")
        return None

    query = """
    mutation TcompBuy($mint: String!, $buyer: String!, $maxPrice: Decimal!, $seller: String!) {
      tcompBuy(mint: $mint, buyer: $buyer, maxPrice: $maxPrice, seller: $seller) {
        txs {
          tx
          lastValidBlockHeight
        }
      }
    }
    """

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                TENSOR_API_BASE,
                headers=_headers(),
                json={
                    "query": query,
                    "variables": {
                        "mint": mint,
                        "buyer": pubkey,
                        "maxPrice": str(price_lamports),
                        "seller": seller,
                    },
                },
            )
            if resp.status_code != 200:
                logger.error(f"Tensor buy failed: {resp.status_code}")
                return None
            data = resp.json()

            txs = data.get("data", {}).get("tcompBuy", {}).get("txs", [])
            if not txs:
                errors = data.get("errors", [])
                if errors:
                    logger.error(f"Tensor buy errors: {errors[0].get('message', '?')}")
                return None

            # Sign and send each transaction in sequence
            last_sig = None
            for tx_data in txs:
                tx_b64 = tx_data.get("tx")
                if not tx_b64:
                    continue
                sig = await sign_and_send_versioned_tx(tx_b64)
                if sig:
                    last_sig = sig
                else:
                    logger.error("Tensor buy: failed to send tx in sequence")
                    return None

            if last_sig:
                confirmed = await confirm_tx(last_sig, timeout_seconds=30)
                logger.info(f"Tensor buy {'confirmed' if confirmed else 'sent'}: {last_sig}")
                return last_sig

    except Exception as e:
        logger.error(f"Tensor buy error: {e}")
    return None
