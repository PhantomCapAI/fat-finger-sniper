"""Relay.link bridge execution — move funds cross-chain before buying."""

import logging
import time

import httpx

from config import RELAY_API_BASE
from wallets.solana import get_pubkey, sign_and_send_versioned_tx
from wallets.evm import get_address

logger = logging.getLogger(__name__)

CHAIN_IDS = {
    "ethereum": 1,
    "base": 8453,
    "polygon": 137,
    "bsc": 56,
    "solana": 792703809,
}


async def bridge_sol_to_evm(
    dest_chain: str,
    amount_lamports: int,
) -> dict:
    """Bridge SOL to an EVM chain via Relay.link.

    Returns: {"success": bool, "time_ms": int, "tx_hash": str|None}
    """
    sol_pubkey = get_pubkey()
    evm_address = get_address()

    if not sol_pubkey or not evm_address:
        return {"success": False, "time_ms": 0, "error": "wallets_not_configured"}

    dest_chain_id = CHAIN_IDS.get(dest_chain)
    if not dest_chain_id:
        return {"success": False, "time_ms": 0, "error": f"unknown_chain: {dest_chain}"}

    start = time.time()

    # Step 1: Get executable swap
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{RELAY_API_BASE}/execute/swap",
                json={
                    "user": sol_pubkey,
                    "originChainId": CHAIN_IDS["solana"],
                    "destinationChainId": dest_chain_id,
                    "originCurrency": "11111111111111111111111111111111",  # SOL native
                    "destinationCurrency": "0x0000000000000000000000000000000000000000",  # native
                    "amount": str(amount_lamports),
                    "recipient": evm_address,
                    "tradeType": "EXACT_INPUT",
                },
            )
            if resp.status_code != 200:
                return {"success": False, "time_ms": 0, "error": f"quote_failed: {resp.status_code}"}
            data = resp.json()
    except Exception as e:
        return {"success": False, "time_ms": 0, "error": str(e)}

    # Step 2: Sign and send the Solana transaction from steps
    steps = data.get("steps", [])
    if not steps:
        return {"success": False, "time_ms": 0, "error": "no_steps"}

    for step in steps:
        for item in step.get("items", []):
            tx_data = item.get("data", {})
            # Relay returns the transaction to sign
            if isinstance(tx_data, dict) and "data" in tx_data:
                tx_b64 = tx_data["data"]
                sig = await sign_and_send_versioned_tx(tx_b64)
                if sig:
                    elapsed_ms = int((time.time() - start) * 1000)
                    logger.info(f"Bridge SOL→{dest_chain}: {sig} in {elapsed_ms}ms")
                    return {"success": True, "time_ms": elapsed_ms, "tx_hash": sig}

    elapsed_ms = int((time.time() - start) * 1000)
    return {"success": False, "time_ms": elapsed_ms, "error": "no_signable_tx_found"}
