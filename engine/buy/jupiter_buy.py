"""Jupiter swap execution — buy tokens on Solana DEX."""

import logging
import httpx

from config import JUPITER_API_BASE, JUPITER_API_KEY
from wallets.solana import get_pubkey, sign_and_send_versioned_tx, confirm_tx

logger = logging.getLogger(__name__)


async def execute_jupiter_swap(
    input_mint: str,
    output_mint: str,
    amount_lamports: int,
    slippage_bps: int = 100,
) -> str | None:
    """Execute a Jupiter swap: get quote → get swap tx → sign → send.

    Returns tx signature on success.
    """
    pubkey = get_pubkey()
    if not pubkey:
        logger.error("No SOL wallet configured")
        return None

    headers = {}
    if JUPITER_API_KEY:
        headers["x-api-key"] = JUPITER_API_KEY

    # Step 1: Get quote
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{JUPITER_API_BASE}/quote/v6",
                params={
                    "inputMint": input_mint,
                    "outputMint": output_mint,
                    "amount": str(amount_lamports),
                    "slippageBps": str(slippage_bps),
                },
                headers=headers,
            )
            if resp.status_code != 200:
                logger.error(f"Jupiter quote failed: {resp.status_code}")
                return None
            quote = resp.json()
    except Exception as e:
        logger.error(f"Jupiter quote error: {e}")
        return None

    # Step 2: Get swap transaction
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{JUPITER_API_BASE}/swap/v6",
                headers={**headers, "Content-Type": "application/json"},
                json={
                    "quoteResponse": quote,
                    "userPublicKey": pubkey,
                    "wrapAndUnwrapSol": True,
                    "dynamicComputeUnitLimit": True,
                    "prioritizationFeeLamports": "auto",
                },
            )
            if resp.status_code != 200:
                logger.error(f"Jupiter swap tx failed: {resp.status_code} {resp.text[:200]}")
                return None
            swap_data = resp.json()
            swap_tx = swap_data.get("swapTransaction")
            if not swap_tx:
                logger.error("No swapTransaction in response")
                return None
    except Exception as e:
        logger.error(f"Jupiter swap error: {e}")
        return None

    # Step 3: Sign and send
    sig = await sign_and_send_versioned_tx(swap_tx)
    if not sig:
        return None

    # Step 4: Confirm
    confirmed = await confirm_tx(sig, timeout_seconds=30)
    if confirmed:
        logger.info(f"Jupiter swap confirmed: {sig}")
        return sig
    else:
        logger.warning(f"Jupiter swap unconfirmed: {sig}")
        return sig  # Return anyway — may confirm later
