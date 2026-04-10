"""Solana wallet — sign and send transactions."""

import logging
import base64
import base58

import httpx
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

from config import SOL_PRIVATE_KEY, SOLANA_RPC_URL

logger = logging.getLogger(__name__)

_keypair: Keypair | None = None


def get_keypair() -> Keypair | None:
    global _keypair
    if _keypair:
        return _keypair
    if not SOL_PRIVATE_KEY:
        return None
    try:
        # Try base58 first (Phantom export format)
        kb = base58.b58decode(SOL_PRIVATE_KEY)
        _keypair = Keypair.from_bytes(kb)
    except Exception:
        try:
            # Try base64
            kb = base64.b64decode(SOL_PRIVATE_KEY)
            _keypair = Keypair.from_bytes(kb)
        except Exception as e:
            logger.error(f"Failed to load SOL keypair: {e}")
            return None
    return _keypair


def get_pubkey() -> str:
    kp = get_keypair()
    return str(kp.pubkey()) if kp else ""


async def sign_and_send_versioned_tx(tx_base64: str) -> str | None:
    """Sign a base64-encoded versioned transaction and send it.

    Used by Jupiter swaps and other Solana programs that return
    pre-built transactions needing only a signature.
    """
    kp = get_keypair()
    if not kp:
        logger.error("No SOL keypair available")
        return None

    try:
        tx_bytes = base64.b64decode(tx_base64)
        tx = VersionedTransaction.from_bytes(tx_bytes)

        # Sign the transaction
        signed_tx = VersionedTransaction(tx.message, [kp])
        signed_bytes = bytes(signed_tx)

        # Send via RPC
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                SOLANA_RPC_URL,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "sendTransaction",
                    "params": [
                        base64.b64encode(signed_bytes).decode("ascii"),
                        {
                            "encoding": "base64",
                            "skipPreflight": False,
                            "preflightCommitment": "confirmed",
                            "maxRetries": 3,
                        },
                    ],
                },
            )
            data = resp.json()
            if "result" in data:
                sig = data["result"]
                logger.info(f"SOL tx sent: {sig[:16]}...")
                return sig
            else:
                logger.error(f"SOL tx failed: {data.get('error', {})}")
                return None
    except Exception as e:
        logger.error(f"SOL sign_and_send error: {e}")
        return None


async def confirm_tx(signature: str, timeout_seconds: int = 30) -> bool:
    """Wait for transaction confirmation."""
    import asyncio
    elapsed = 0
    while elapsed < timeout_seconds:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    SOLANA_RPC_URL,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getSignatureStatuses",
                        "params": [[signature]],
                    },
                )
                data = resp.json()
                statuses = data.get("result", {}).get("value", [])
                if statuses and statuses[0]:
                    status = statuses[0]
                    if status.get("confirmationStatus") in ("confirmed", "finalized"):
                        return True
                    if status.get("err"):
                        return False
        except Exception:
            pass
        await asyncio.sleep(2)
        elapsed += 2
    return False
