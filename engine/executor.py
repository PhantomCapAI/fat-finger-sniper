"""Trade executor — paper mode + live execution with risk controls."""

import asyncio
import logging
import time
from datetime import datetime, timezone

from config import (
    PAPER_MODE, MAX_PER_SNIPE_USD, MAX_DAILY_USD, MAX_BANKROLL_USD,
    COOLDOWN_SECONDS, GAS_MULTIPLIER_MAX,
)
from db import (
    is_duplicate, record_purchase, get_daily_spend, add_daily_spend,
    log_opportunity, mark_executed, mark_cancelled,
)
from engine.killswitch import send_killswitch_alert, wait_for_decision, update_message
from engine.honeypot import is_safe_opportunity

logger = logging.getLogger(__name__)

_last_snipe_time: float = 0
_total_spent: float = 0


async def process_opportunity(opp: dict) -> dict:
    """Full pipeline: validate → honeypot → alert → wait → execute/cancel.

    Returns result dict with action taken.
    """
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
    # TODO: Real price feeds. For now use listing_price as proxy.
    cost_usd = listing_price  # Simplified — needs oracle for real USD conversion

    # --- Risk controls ---
    if cost_usd > MAX_PER_SNIPE_USD:
        result["reason"] = f"exceeds_max_per_snipe (${cost_usd:.2f} > ${MAX_PER_SNIPE_USD})"
        return result

    daily = await get_daily_spend()
    if daily + cost_usd > MAX_DAILY_USD:
        result["reason"] = f"exceeds_daily_limit (${daily:.2f} + ${cost_usd:.2f} > ${MAX_DAILY_USD})"
        return result

    if _total_spent + cost_usd > MAX_BANKROLL_USD:
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


async def _execute_buy(opp: dict) -> str | None:
    """Execute the actual purchase transaction.

    Routes to the appropriate marketplace SDK based on opp details.
    Returns tx hash on success, None on failure.
    """
    marketplace = opp["marketplace"]
    chain = opp["chain"]

    # TODO: Implement per-marketplace execution
    # Each marketplace has its own buy flow:
    # - OpenSea: Seaport fulfillment via web3
    # - Magic Eden: Direct Solana instruction
    # - Tensor: Tensor SDK buy instruction
    # - Jupiter: Swap instruction
    # - Polymarket: CLOB order via API
    # - StockX/TCGPlayer/GoDaddy/eBay: HTTP purchase APIs

    logger.warning(f"Live execution not yet implemented for {marketplace}/{chain}")
    return None
