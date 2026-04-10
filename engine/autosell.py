"""Auto-sell logic — list sniped assets for profit after acquisition.

Strategy:
- NFTs: List at 80% of fair value (instant profit, fast exit)
- Tokens: Swap back to SOL/USDC immediately via Jupiter
- Polymarket: Hold to resolution or sell at midpoint if profitable
"""

import logging
import httpx

from config import (
    MAGICEDEN_API_BASE, OPENSEA_API_BASE, OPENSEA_API_KEY,
    JUPITER_API_BASE, JUPITER_API_KEY,
)
from wallets.solana import get_pubkey, sign_and_send_versioned_tx

logger = logging.getLogger(__name__)

# Sell at this % of fair value — locks in profit, exits fast
NFT_SELL_PCT = 0.80  # List at 80% of fair value
TOKEN_SLIPPAGE_BPS = 150  # 1.5% slippage on token sells


async def autosell_solana_nft_magiceden(
    token_mint: str,
    price_sol: float,
    fair_value_sol: float,
) -> str | None:
    """List a sniped Solana NFT on Magic Eden for quick profit.

    Lists at 80% of fair value — still a profit since we bought at a fat finger price.
    """
    pubkey = get_pubkey()
    if not pubkey:
        return None

    list_price = fair_value_sol * NFT_SELL_PCT

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{MAGICEDEN_API_BASE}/instructions/sell",
                params={
                    "seller": pubkey,
                    "tokenMint": token_mint,
                    "price": list_price,
                },
            )
            if resp.status_code != 200:
                logger.error(f"ME list failed: {resp.status_code} {resp.text[:200]}")
                return None
            data = resp.json()

        tx_b64 = data.get("txSigned") or data.get("tx", {}).get("data")
        if not tx_b64:
            logger.error(f"ME list: no tx in response")
            return None

        sig = await sign_and_send_versioned_tx(tx_b64)
        if sig:
            profit_pct = round((list_price / price_sol - 1) * 100, 1)
            logger.info(
                f"AUTO-SELL: Listed {token_mint[:12]}... on ME at {list_price:.4f} SOL "
                f"(bought {price_sol:.4f}, listing at {NFT_SELL_PCT*100}% of {fair_value_sol:.4f} fair, "
                f"+{profit_pct}% profit)"
            )
            return sig
    except Exception as e:
        logger.error(f"ME autosell error: {e}")
    return None


async def autosell_token_jupiter(
    token_mint: str,
    amount_raw: int,
) -> str | None:
    """Swap a sniped token back to SOL via Jupiter for instant exit.

    Sells the full amount with 1.5% slippage tolerance.
    """
    pubkey = get_pubkey()
    if not pubkey:
        return None

    sol_mint = "So11111111111111111111111111111111111111112"
    headers = {}
    if JUPITER_API_KEY:
        headers["x-api-key"] = JUPITER_API_KEY

    try:
        # Get quote: token → SOL
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{JUPITER_API_BASE}/quote/v6",
                params={
                    "inputMint": token_mint,
                    "outputMint": sol_mint,
                    "amount": str(amount_raw),
                    "slippageBps": str(TOKEN_SLIPPAGE_BPS),
                },
                headers=headers,
            )
            if resp.status_code != 200:
                logger.error(f"Jupiter sell quote failed: {resp.status_code}")
                return None
            quote = resp.json()

        # Get swap tx
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
                logger.error(f"Jupiter sell swap failed: {resp.status_code}")
                return None
            swap_tx = resp.json().get("swapTransaction")
            if not swap_tx:
                return None

        sig = await sign_and_send_versioned_tx(swap_tx)
        if sig:
            out_amount = int(quote.get("outAmount", "0"))
            logger.info(f"AUTO-SELL: Swapped token back to {out_amount/1e9:.6f} SOL via Jupiter")
            return sig
    except Exception as e:
        logger.error(f"Jupiter autosell error: {e}")
    return None


async def autosell_opensea_nft(
    contract: str,
    token_id: str,
    fair_value_eth: float,
    chain: str = "ethereum",
) -> str | None:
    """List a sniped ETH NFT on OpenSea for quick profit.

    Uses OpenSea's listing API — requires Seaport approval first.
    This is more complex than Solana — placeholder for now.
    """
    list_price = fair_value_eth * NFT_SELL_PCT
    logger.info(
        f"AUTO-SELL: Would list {contract}:{token_id} on OpenSea at "
        f"{list_price:.4f} ETH ({NFT_SELL_PCT*100}% of fair). "
        f"Seaport listing requires approval flow — manual for now."
    )
    # TODO: Implement Seaport createListing flow
    # Requires: setApprovalForAll on the NFT contract, then sign Seaport order
    return None


async def schedule_autosell(opp: dict, tx_hash: str):
    """Schedule an auto-sell after a successful buy.

    Routes to the correct sell function based on marketplace.
    """
    marketplace = opp.get("marketplace", "")
    chain = opp.get("chain", "")
    asset_id = opp.get("asset_id", "")
    listing_price = opp.get("listing_price", 0)
    fair_value = opp.get("fair_value", 0)

    if marketplace in ("magiceden", "tensor"):
        return await autosell_solana_nft_magiceden(
            token_mint=asset_id,
            price_sol=listing_price,
            fair_value_sol=fair_value,
        )

    elif marketplace == "jupiter":
        # For token swaps, sell the output tokens back
        # amount_raw would need to come from the buy tx result
        logger.info(f"AUTO-SELL: Jupiter token swap — already exited to SOL in buy step")
        return None

    elif marketplace == "opensea":
        parts = asset_id.split(":")
        contract = parts[0] if len(parts) > 1 else ""
        token_id = parts[1] if len(parts) > 1 else ""
        return await autosell_opensea_nft(contract, token_id, fair_value, chain)

    else:
        logger.info(f"AUTO-SELL: No auto-sell for {marketplace} — hold manually")
        return None
