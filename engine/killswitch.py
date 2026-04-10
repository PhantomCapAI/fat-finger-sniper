"""Telegram kill switch — 60-second window with inline keyboard."""

import asyncio
import logging
import time

import httpx

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, KILLSWITCH_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

# Track pending opportunities: opp_id -> {"cancelled": bool, "expires": float}
_pending: dict[int, dict] = {}


def _format_alert(opp: dict, opp_id: int) -> str:
    """Format the Telegram alert message."""
    confidence = opp.get("confidence", "?")
    emoji = {"CRITICAL": "\U0001f6a8", "HIGH": "\u26a0\ufe0f", "MEDIUM": "\U0001f50d"}.get(confidence, "\u2753")

    return (
        f"{emoji} <b>FAT FINGER — {confidence}</b>\n\n"
        f"<b>Asset:</b> {opp.get('asset_name', opp.get('asset_id', '?'))}\n"
        f"<b>Marketplace:</b> {opp.get('marketplace', '?')}\n"
        f"<b>Chain:</b> {opp.get('chain', '?')}\n"
        f"<b>Listed:</b> {opp.get('listing_price', 0):.6f} {opp.get('currency', '?')}\n"
        f"<b>Fair Value:</b> {opp.get('fair_value', 0):.6f} {opp.get('currency', '?')}\n"
        f"<b>Discount:</b> {opp.get('discount_pct', 0)}%\n"
        f"<b>Seller:</b> <code>{opp.get('seller', '?')[:16]}...</code>\n"
        f"\n<b>Auto-execute in {KILLSWITCH_TIMEOUT_SECONDS}s unless cancelled</b>\n"
        f"\n{opp.get('url', '')}"
    )


async def send_killswitch_alert(opp: dict, opp_id: int) -> int | None:
    """Send Telegram alert with CANCEL / BUY NOW inline keyboard.

    Returns the message_id for tracking callback queries.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return None

    text = _format_alert(opp, opp_id)
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "\u274c CANCEL", "callback_data": f"cancel:{opp_id}"},
                {"text": "\u26a1 BUY NOW", "callback_data": f"buy:{opp_id}"},
            ]
        ]
    }

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                    "reply_markup": keyboard,
                },
            )
            data = resp.json()
            if data.get("ok"):
                msg_id = data["result"]["message_id"]
                _pending[opp_id] = {
                    "cancelled": False,
                    "buy_now": False,
                    "expires": time.time() + KILLSWITCH_TIMEOUT_SECONDS,
                    "msg_id": msg_id,
                }
                return msg_id
    except Exception as e:
        logger.error(f"Kill switch alert failed: {e}")

    return None


def handle_callback(opp_id: int, action: str):
    """Handle inline keyboard callback (cancel or buy_now)."""
    if opp_id in _pending:
        if action == "cancel":
            _pending[opp_id]["cancelled"] = True
        elif action == "buy":
            _pending[opp_id]["buy_now"] = True


async def wait_for_decision(opp_id: int) -> str:
    """Wait for kill switch timeout or user action.

    Returns: "execute", "cancelled", or "buy_now"
    """
    if opp_id not in _pending:
        return "execute"

    deadline = _pending[opp_id]["expires"]
    while time.time() < deadline:
        if _pending[opp_id]["cancelled"]:
            return "cancelled"
        if _pending[opp_id]["buy_now"]:
            return "buy_now"
        await asyncio.sleep(1)

    # Timeout — no cancel received
    if _pending[opp_id]["cancelled"]:
        return "cancelled"
    return "execute"


async def update_message(opp_id: int, result: str):
    """Edit the original message to show the outcome."""
    if not TELEGRAM_BOT_TOKEN or opp_id not in _pending:
        return

    msg_id = _pending[opp_id].get("msg_id")
    if not msg_id:
        return

    emoji = {
        "executed": "\u2705 EXECUTED",
        "cancelled": "\u274c CANCELLED",
        "paper": "\U0001f4dd PAPER LOGGED",
        "failed": "\u274c FAILED",
        "skipped": "\u23ed SKIPPED",
    }.get(result, result)

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageReplyMarkup"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Remove inline keyboard
            await client.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "message_id": msg_id,
                "reply_markup": {"inline_keyboard": []},
            })
            # Send follow-up
            await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": f"{emoji} — OPP #{opp_id}",
                    "reply_to_message_id": msg_id,
                },
            )
    except Exception as e:
        logger.error(f"Update message failed: {e}")

    _pending.pop(opp_id, None)
