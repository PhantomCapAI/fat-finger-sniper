"""EVM wallet — sign and send transactions across ETH/Base/Polygon/BSC."""

import logging

import httpx

from config import (
    EVM_PRIVATE_KEY, GAS_MULTIPLIER_MAX,
    ETH_RPC_URL, BASE_RPC_URL, POLYGON_RPC_URL, BSC_RPC_URL,
)

logger = logging.getLogger(__name__)

RPC_URLS = {
    "ethereum": ETH_RPC_URL,
    "base": BASE_RPC_URL,
    "polygon": POLYGON_RPC_URL,
    "bsc": BSC_RPC_URL,
}


def get_rpc(chain: str) -> str:
    return RPC_URLS.get(chain, ETH_RPC_URL)


def get_address() -> str:
    """Derive public address from private key."""
    if not EVM_PRIVATE_KEY:
        return ""
    try:
        from web3 import Account
        return Account.from_key(EVM_PRIVATE_KEY).address
    except Exception:
        return ""


async def get_balance(chain: str = "ethereum") -> int:
    """Get native balance in wei."""
    addr = get_address()
    if not addr:
        return 0
    rpc = get_rpc(chain)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                rpc,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_getBalance",
                    "params": [addr, "latest"],
                },
            )
            data = resp.json()
            return int(data.get("result", "0x0"), 16)
    except Exception as e:
        logger.error(f"EVM balance error: {e}")
        return 0


async def get_gas_price(chain: str = "ethereum") -> int:
    """Get current gas price in wei."""
    rpc = get_rpc(chain)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                rpc,
                json={"jsonrpc": "2.0", "id": 1, "method": "eth_gasPrice", "params": []},
            )
            return int(resp.json().get("result", "0x0"), 16)
    except Exception:
        return 0


async def send_raw_tx(chain: str, signed_tx_hex: str) -> str | None:
    """Send a pre-signed raw transaction."""
    rpc = get_rpc(chain)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                rpc,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_sendRawTransaction",
                    "params": [signed_tx_hex],
                },
            )
            data = resp.json()
            if "result" in data:
                tx_hash = data["result"]
                logger.info(f"EVM tx sent on {chain}: {tx_hash[:16]}...")
                return tx_hash
            else:
                logger.error(f"EVM tx failed: {data.get('error', {})}")
                return None
    except Exception as e:
        logger.error(f"EVM send error: {e}")
        return None


def check_gas_limit(gas_price: int, chain: str = "ethereum") -> bool:
    """Verify gas price is within our max multiplier limit."""
    # Baseline gas prices (rough averages in gwei)
    baselines = {
        "ethereum": 20_000_000_000,  # 20 gwei
        "base": 100_000_000,          # 0.1 gwei
        "polygon": 30_000_000_000,    # 30 gwei
        "bsc": 3_000_000_000,         # 3 gwei
    }
    baseline = baselines.get(chain, 20_000_000_000)
    max_allowed = int(baseline * GAS_MULTIPLIER_MAX)
    return gas_price <= max_allowed
