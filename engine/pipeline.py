"""Content pipeline integration — route snipe alerts through Claire for X posting."""

import logging
import httpx

logger = logging.getLogger(__name__)

PIPELINE_URL = "https://phantom-pipeline.zeabur.app"


def _fun_headline(confidence: str, asset_name: str, discount_pct: float) -> str:
    """Generate fun alert text based on confidence tier."""
    if confidence == "CRITICAL":
        return f"\U0001f3b0 LOTTO TICKET LANDED — {asset_name} at {discount_pct}% off"
    elif confidence == "HIGH":
        return f"\U0001f525 FAT FINGER CAUGHT — {asset_name} at {discount_pct}% off"
    else:
        return f"\U0001f440 FUMBLE SPOTTED — {asset_name} at {discount_pct}% off"


def _x_draft(opp: dict) -> str:
    """Format a draft X post for the content pipeline.

    Short, hype, real numbers. Routes through Claire for polish.
    """
    asset = opp.get("asset_name", "?")
    price = opp.get("listing_price", 0)
    fair = opp.get("fair_value", 0)
    discount = opp.get("discount_pct", 0)
    currency = opp.get("currency", "")
    marketplace = opp.get("marketplace", "")

    return (
        f"Just sniped {asset} at {price:.4f} {currency} — "
        f"worth {fair:.4f} {currency}. "
        f"That's a {discount}% fat finger on {marketplace}. \U0001f3b0"
    )


async def send_to_pipeline(opp: dict, action: str):
    """Send a snipe result to phantom-pipeline for X posting via Claire.

    Only sends executed snipes (live or paper).
    """
    if action not in ("executed", "paper_logged"):
        return

    draft = _x_draft(opp)
    headline = _fun_headline(
        opp.get("confidence", "MEDIUM"),
        opp.get("asset_name", "?"),
        opp.get("discount_pct", 0),
    )

    payload = {
        "agent": "fat-finger-sniper",
        "type": "snipe_alert",
        "route_to": "claire",
        "action": "polish_and_post",
        "draft": draft,
        "headline": headline,
        "data": {
            "asset": opp.get("asset_name"),
            "marketplace": opp.get("marketplace"),
            "chain": opp.get("chain"),
            "listing_price": opp.get("listing_price"),
            "fair_value": opp.get("fair_value"),
            "discount_pct": opp.get("discount_pct"),
            "confidence": opp.get("confidence"),
            "currency": opp.get("currency"),
            "paper_mode": opp.get("paper_mode", True),
        },
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{PIPELINE_URL}/v1/ingest",
                json=payload,
            )
            if resp.status_code == 200:
                logger.info(f"Pipeline: sent snipe alert for {opp.get('asset_name')}")
            else:
                logger.warning(f"Pipeline response: {resp.status_code}")
    except Exception as e:
        logger.error(f"Pipeline send failed: {e}")


async def send_fun_telegram(opp: dict, bot_token: str, chat_id: str):
    """Send the fun Telegram alert after a successful snipe."""
    if not bot_token or not chat_id:
        return

    confidence = opp.get("confidence", "MEDIUM")
    headline = _fun_headline(confidence, opp.get("asset_name", "?"), opp.get("discount_pct", 0))

    text = (
        f"{headline}\n\n"
        f"<b>Asset:</b> {opp.get('asset_name', '?')}\n"
        f"<b>Price:</b> {opp.get('listing_price', 0):.6f} {opp.get('currency', '?')}\n"
        f"<b>Fair Value:</b> {opp.get('fair_value', 0):.6f} {opp.get('currency', '?')}\n"
        f"<b>Marketplace:</b> {opp.get('marketplace', '?')} | {opp.get('chain', '?')}\n"
        f"<b>Paper:</b> {'Yes' if opp.get('paper_mode') else 'No'}"
    )

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
            })
    except Exception as e:
        logger.error(f"Fun telegram failed: {e}")
