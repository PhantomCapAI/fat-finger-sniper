"""Telegram alert system for fat-finger detections."""

import logging
import httpx

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)


async def send_alert(flagged: dict):
    """Send a Telegram alert for a flagged fat-finger listing."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    chain = flagged.get("chain", "?")
    marketplace = flagged.get("marketplace", "?")
    collection = flagged.get("collection", "?")
    discount = flagged.get("discount_pct", 0)

    if chain == "solana":
        price = flagged.get("listing_price_sol", 0)
        floor = flagged.get("floor_price_sol", 0)
        currency = "SOL"
        url = flagged.get("magiceden_url", "")
    else:
        price = flagged.get("listing_price", 0)
        floor = flagged.get("floor_price", 0)
        currency = flagged.get("currency", "ETH")
        url = flagged.get("opensea_url", "")

    text = (
        f"<b>FAT FINGER DETECTED</b>\n\n"
        f"<b>Collection:</b> {collection}\n"
        f"<b>Chain:</b> {chain} | {marketplace}\n"
        f"<b>Listed:</b> {price:.4f} {currency}\n"
        f"<b>Floor:</b> {floor:.4f} {currency}\n"
        f"<b>Discount:</b> {discount}% below floor\n"
        f"\n<a href=\"{url}\">View Listing</a>"
    )

    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                api_url,
                json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
    except Exception as e:
        logger.error(f"Telegram alert failed: {e}")
