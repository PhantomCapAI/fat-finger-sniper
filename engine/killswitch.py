"""Telegram kill switch — 60-second window with inline keyboard.

Also hosts text-command handlers (/start /status /stop /paper /balance)
since they share the same bot token and HTTP client machinery.
"""

import asyncio
import logging
import time

import httpx

from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, KILLSWITCH_TIMEOUT_SECONDS,
    PAPER_MODE, MAX_PER_SNIPE_USD, MAX_DAILY_USD, MAX_BANKROLL_USD,
    SOLANA_RPC_URL, PHANTOM_TREASURY,
)

logger = logging.getLogger(__name__)

# Track pending opportunities: opp_id -> {"cancelled": bool, "expires": float}
_pending: dict[int, dict] = {}

# Global scanner pause flag — /stop sets this, executor checks it.
_scanner_paused: bool = False


def is_scanner_paused() -> bool:
    return _scanner_paused


def set_scanner_paused(paused: bool) -> None:
    global _scanner_paused
    _scanner_paused = paused


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


# --- Text command handlers ---------------------------------------------

async def _send_message(chat_id: str, text: str) -> None:
    if not TELEGRAM_BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                url,
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
            )
    except Exception as e:
        logger.error(f"send_message failed: {e}")


PUBLIC_SOLANA_RPC = "https://api.mainnet-beta.solana.com"


async def _rpc_get_balance(rpc_url: str, pubkey: str) -> tuple[int | None, str | None]:
    """Hit a single Solana RPC for getBalance. Returns (lamports, error_message)."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                rpc_url,
                json={"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [pubkey]},
            )
            data = resp.json()
            if "error" in data and data["error"] is not None:
                err = data["error"]
                msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                return None, f"rpc_error: {msg}"
            result = data.get("result")
            if not isinstance(result, dict) or "value" not in result:
                return None, f"unexpected_shape: {str(data)[:160]}"
            return int(result["value"]), None
    except Exception as e:
        return None, f"exception: {e}"


async def _get_sol_balance(pubkey: str) -> float | None:
    if not pubkey:
        return None

    # Try the configured RPC first (usually Helius with embedded key).
    if SOLANA_RPC_URL:
        lamports, err = await _rpc_get_balance(SOLANA_RPC_URL, pubkey)
        if lamports is not None:
            return lamports / 1_000_000_000
        logger.warning(f"primary RPC getBalance failed ({err}); trying public fallback")

    # Fallback to public mainnet-beta so /balance is still informative
    # when the primary RPC key is dead.
    lamports, err = await _rpc_get_balance(PUBLIC_SOLANA_RPC, pubkey)
    if lamports is None:
        logger.error(f"public RPC fallback also failed: {err}")
        return None
    return lamports / 1_000_000_000


async def _format_status() -> str:
    # Lazy imports to avoid circular dep on db.py at module load
    try:
        from db import get_stats, get_daily_spend
        stats = await get_stats()
        total = stats.get("total_opportunities", 0) if isinstance(stats, dict) else 0
        executed = stats.get("executed", 0) if isinstance(stats, dict) else 0
        daily = await get_daily_spend()
    except Exception as e:
        logger.error(f"status db read failed: {e}")
        total = executed = 0
        daily = 0.0

    mode_line = "\u2705 ON" if PAPER_MODE else "\U0001f6a8 OFF"
    pause_line = " (PAUSED)" if _scanner_paused else ""
    return (
        f"<b>\U0001f3af PHANTOMFINGER STATUS</b>{pause_line}\n\n"
        f"<b>Paper mode:</b> {mode_line}\n"
        f"<b>Wallet:</b> <code>{PHANTOM_TREASURY[:4]}...{PHANTOM_TREASURY[-4:]}</code>\n"
        f"<b>Spent today:</b> ${daily:.2f} / ${MAX_DAILY_USD:.0f}\n"
        f"<b>Per-snipe cap:</b> ${MAX_PER_SNIPE_USD:.0f}\n"
        f"<b>Bankroll cap:</b> ${MAX_BANKROLL_USD:.0f}\n"
        f"<b>Opportunities logged:</b> {total}\n"
        f"<b>Executed:</b> {executed}"
    )


async def handle_text_command(message: dict) -> None:
    """Dispatch /start /status /stop /paper /balance.

    Silently ignores messages from unauthorized chats and unknown commands.
    """
    text = (message.get("text") or "").strip()
    chat_id = str(message.get("chat", {}).get("id", ""))

    if chat_id != TELEGRAM_CHAT_ID:
        return
    if not text.startswith("/"):
        return

    cmd = text.split()[0].split("@")[0].lower()

    if cmd == "/start":
        mode = "\u2705 ON" if PAPER_MODE else "\U0001f6a8 OFF"
        reply = (
            f"<b>\U0001f3af PHANTOMFINGER armed</b>\n\n"
            f"Paper mode: {mode}\n"
            f"Commands: /status /stop /paper /balance"
        )
        await _send_message(chat_id, reply)

    elif cmd == "/status":
        await _send_message(chat_id, await _format_status())

    elif cmd == "/stop":
        set_scanner_paused(True)
        await _send_message(
            chat_id,
            "<b>\U0001f6d1 Scanner PAUSED</b>\n\nNew opportunities will be skipped. "
            "Restart the service to resume (no /start_snipe command yet).",
        )

    elif cmd == "/paper":
        # Read-only: never toggle paper mode from a Telegram command.
        mode = "\u2705 ON (safe)" if PAPER_MODE else "\U0001f6a8 OFF (LIVE)"
        await _send_message(
            chat_id,
            f"<b>Paper mode:</b> {mode}\n\n"
            f"To change, update PAPER_MODE env var on Zeabur and restart. "
            f"Intentionally not toggleable from Telegram.",
        )

    elif cmd == "/balance":
        bal = await _get_sol_balance(PHANTOM_TREASURY)
        if bal is None:
            await _send_message(chat_id, "Balance query failed (RPC or wallet unset).")
        else:
            await _send_message(
                chat_id,
                f"<b>Wallet:</b> <code>{PHANTOM_TREASURY[:4]}...{PHANTOM_TREASURY[-4:]}</code>\n"
                f"<b>SOL balance:</b> {bal:.4f} SOL",
            )
