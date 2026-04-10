"""Trade executor — paper mode + live execution with risk controls."""

import logging
import time

from config import (
    PAPER_MODE, MAX_PER_SNIPE_USD, MAX_DAILY_USD, MAX_BANKROLL_USD,
    COOLDOWN_SECONDS,
)

# BTC/ETH/SOL at CRITICAL (>95% discount): no per-snipe cap, use full wallet
YOLO_ASSETS = {
    # Solana
    "So11111111111111111111111111111111111111112",   # SOL
    "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh", # WBTC (Solana)
    # Ethereum
    "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",  # WETH
    "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",  # WBTC
    "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf",  # cbBTC
    "0x18084fbA666a33d37592fA2633fD49a74DD93a88",  # tBTC
}
YOLO_SYMBOLS = {"BTC", "WBTC", "CBBTC", "TBTC", "ETH", "WETH", "SOL"}
from db import (
    is_duplicate, record_purchase, get_daily_spend, add_daily_spend,
    log_opportunity, mark_executed, mark_cancelled,
)
from engine.killswitch import send_killswitch_alert, wait_for_decision, update_message
from engine.honeypot import is_safe_opportunity
from engine.autosell import schedule_autosell

logger = logging.getLogger(__name__)

_last_snipe_time: float = 0
_total_spent: float = 0


async def process_opportunity(opp: dict) -> dict:
    """Full pipeline: validate -> honeypot -> alert -> wait -> execute/cancel."""
    global _last_snipe_time, _total_spent

    asset_id = opp["asset_id"]
    listing_price = opp["listing_price"]
    result = {"asset_id": asset_id, "action": "skipped", "reason": ""}

    # --- Duplicate check ---
    if await is_duplicate(asset_id):
        result["reason"] = "duplicate_asset"
        return result

    # --- Cooldown ---
    elapsed = time.time() - _last_snipe_time
    if elapsed < COOLDOWN_SECONDS:
        result["reason"] = f"cooldown ({COOLDOWN_SECONDS - elapsed:.0f}s remaining)"
        return result

    # --- Estimate USD cost ---
    cost_usd = _estimate_usd(opp)

    # --- YOLO override: BTC/ETH/SOL at CRITICAL = no cap ---
    is_yolo = _is_yolo_opportunity(opp)

    # --- Risk controls ---
    if not is_yolo and cost_usd > MAX_PER_SNIPE_USD:
        result["reason"] = f"exceeds_max_per_snipe (${cost_usd:.2f} > ${MAX_PER_SNIPE_USD})"
        return result

    if is_yolo:
        logger.warning(
            f"YOLO MODE: {opp.get('asset_name')} at {opp.get('discount_pct')}% discount — "
            f"no per-snipe cap, using full wallet balance"
        )

    daily = await get_daily_spend()
    if not is_yolo and daily + cost_usd > MAX_DAILY_USD:
        result["reason"] = f"exceeds_daily_limit (${daily:.2f} + ${cost_usd:.2f} > ${MAX_DAILY_USD})"
        return result

    if not is_yolo and _total_spent + cost_usd > MAX_BANKROLL_USD:
        result["reason"] = f"exceeds_bankroll (${_total_spent:.2f} + ${cost_usd:.2f} > ${MAX_BANKROLL_USD})"
        return result

    # --- Honeypot check ---
    safety = await is_safe_opportunity(opp)
    if not safety.get("safe", False):
        result["reason"] = f"honeypot: {safety.get('reason', 'unknown')}"
        return result

    # --- Log opportunity ---
    opp["paper_mode"] = PAPER_MODE
    opp_id = await log_opportunity(opp) or 0

    # --- Kill switch alert ---
    await send_killswitch_alert(opp, opp_id)
    decision = await wait_for_decision(opp_id)

    if decision == "cancelled":
        await mark_cancelled(opp_id)
        await update_message(opp_id, "cancelled")
        result["action"] = "cancelled"
        result["reason"] = "user_cancelled"
        return result

    # --- Execute ---
    _last_snipe_time = time.time()

    if PAPER_MODE:
        await mark_executed(opp_id, "PAPER_MODE", cost_usd)
        await update_message(opp_id, "paper")
        logger.info(f"PAPER: Would buy {asset_id} @ {listing_price} on {opp['marketplace']}")
        result["action"] = "paper_logged"
        result["opp_id"] = opp_id
        return result

    # --- Live execution ---
    try:
        tx_hash = await _execute_buy(opp)
        if tx_hash:
            await record_purchase(asset_id, opp["marketplace"], opp["chain"])
            await mark_executed(opp_id, tx_hash, cost_usd)
            await add_daily_spend(cost_usd)
            _total_spent += cost_usd
            await update_message(opp_id, "executed")
            result["action"] = "executed"
            result["tx_hash"] = tx_hash
            result["cost_usd"] = cost_usd

            # Auto-sell: list/swap for profit immediately
            try:
                sell_hash = await schedule_autosell(opp, tx_hash)
                if sell_hash:
                    result["sell_tx"] = sell_hash
                    logger.info(f"Auto-sell queued: {sell_hash}")
            except Exception as e:
                logger.error(f"Auto-sell failed: {e}")
        else:
            await update_message(opp_id, "failed")
            result["action"] = "failed"
            result["reason"] = "tx_failed"
    except Exception as e:
        logger.error(f"Execution error: {e}")
        await update_message(opp_id, "failed")
        result["action"] = "failed"
        result["reason"] = str(e)

    return result


def _is_yolo_opportunity(opp: dict) -> bool:
    """Check if this is a BTC/ETH/SOL CRITICAL opportunity.

    These three assets at >95% discount bypass all per-snipe caps.
    Full wallet balance goes in. Once-in-a-lifetime.
    """
    confidence = opp.get("confidence", "")
    if confidence != "CRITICAL":
        return False

    asset_id = opp.get("asset_id", "")
    asset_name = (opp.get("asset_name", "") or "").upper()
    currency = (opp.get("currency", "") or "").upper()

    # Check by contract address
    if asset_id in YOLO_ASSETS:
        return True

    # Check by symbol in name or currency
    for sym in YOLO_SYMBOLS:
        if sym in asset_name or sym in currency:
            return True

    return False


def _estimate_usd(opp: dict) -> float:
    """Rough USD estimate for an opportunity."""
    price = opp.get("listing_price", 0)
    currency = opp.get("currency", "").upper()

    # Rough price multipliers — real implementation would use live oracle
    multipliers = {
        "SOL": 83.0,
        "ETH": 1600.0,
        "MATIC": 0.22,
        "BNB": 300.0,
        "USDC": 1.0,
        "USD": 1.0,
    }
    mult = multipliers.get(currency, 1.0)
    return price * mult


async def _execute_buy(opp: dict) -> str | None:
    """Route to the appropriate marketplace buy function."""
    marketplace = opp["marketplace"]
    chain = opp["chain"]

    if marketplace == "jupiter":
        from engine.buy.jupiter_buy import execute_jupiter_swap
        # For Jupiter, asset_id is the output mint, buy with SOL
        sol_mint = "So11111111111111111111111111111111111111112"
        amount_lamports = int(opp["listing_price"] * 1_000_000_000)
        return await execute_jupiter_swap(sol_mint, opp["asset_id"], amount_lamports)

    elif marketplace == "magiceden":
        from engine.buy.magiceden_buy import execute_magiceden_buy
        price_lamports = int(opp["listing_price"] * 1_000_000_000)
        return await execute_magiceden_buy(opp["asset_id"], price_lamports)

    elif marketplace == "tensor":
        from engine.buy.tensor_buy import execute_tensor_buy
        price_lamports = int(opp["listing_price"] * 1_000_000_000)
        seller = opp.get("seller", "")
        return await execute_tensor_buy(opp["asset_id"], price_lamports, seller)

    elif marketplace == "polymarket":
        from engine.buy.polymarket_buy import execute_polymarket_buy
        return await execute_polymarket_buy(
            opp["asset_id"],
            opp["listing_price"],
            opp.get("metadata", {}).get("ask_size", 10),
        )

    elif marketplace == "opensea":
        from engine.buy.opensea_buy import execute_opensea_buy
        order_hash = opp.get("metadata", {}).get("order_hash", "")
        protocol = opp.get("metadata", {}).get("protocol_address", "0x00000000000000ADc04C56Bf30aC9d3c0aAF14dC")
        if not order_hash:
            logger.warning("OpenSea buy: no order_hash in opportunity metadata")
            return None
        return await execute_opensea_buy(order_hash, protocol, chain)

    elif marketplace in ("stockx", "tcgplayer", "godaddy", "ebay"):
        # Traditional marketplaces — HTTP purchase APIs
        logger.warning(f"{marketplace} buy: traditional marketplace execution not yet wired")
        return None

    else:
        logger.warning(f"Unknown marketplace: {marketplace}")
        return None
