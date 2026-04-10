"""Magic Eden NFT buy execution — Solana."""

import logging
import httpx

from config import MAGICEDEN_API_BASE
from wallets.solana import get_pubkey, sign_and_send_versioned_tx, confirm_tx

logger = logging.getLogger(__name__)


async def execute_magiceden_buy(token_mint: str, price_lamports: int) -> str | None:
    """Buy an NFT on Magic Eden.

    Uses ME's /v2/instructions/buy endpoint to get a pre-built transaction,
    then signs and sends it.
    """
    pubkey = get_pubkey()
    if not pubkey:
        logger.error("No SOL wallet configured")
        return None

    # Get buy instruction from Magic Eden
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{MAGICEDEN_API_BASE}/instructions/buy_now",
                params={
                    "buyer": pubkey,
                    "tokenMint": token_mint,
                    "price": price_lamports / 1_000_000_000,  # ME expects SOL not lamports
                },
            )
            if resp.status_code != 200:
                logger.error(f"ME buy instruction failed: {resp.status_code} {resp.text[:200]}")
                return None
            data = resp.json()
    except Exception as e:
        logger.error(f"ME buy instruction error: {e}")
        return None

    # The response contains a serialized transaction
    tx_signed = data.get("txSigned") or data.get("tx", {}).get("data")
    if not tx_signed:
        # Some endpoints return transaction in different formats
        logger.error(f"ME buy: no transaction in response. Keys: {list(data.keys())}")
        return None

    # If it's a base64 transaction, sign and send
    if isinstance(tx_signed, str):
        sig = await sign_and_send_versioned_tx(tx_signed)
        if sig:
            confirmed = await confirm_tx(sig, timeout_seconds=30)
            logger.info(f"ME buy {'confirmed' if confirmed else 'sent'}: {sig}")
            return sig

    return None
