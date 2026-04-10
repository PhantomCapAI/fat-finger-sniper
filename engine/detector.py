"""Fair value estimation and confidence classification."""

from config import CRITICAL_THRESHOLD_PCT, HIGH_THRESHOLD_PCT, MEDIUM_THRESHOLD_PCT


def compute_fair_value(
    floor_price: float,
    last_sales: list[float] | None = None,
) -> float:
    """Rolling fair value: weighted blend of floor + VWAP of last 10 sales.

    If no sales data, floor price is the fair value.
    Otherwise: 60% floor + 40% mean of last sales (capped at 10).
    """
    if not last_sales:
        return floor_price

    recent = last_sales[-10:]
    sales_avg = sum(recent) / len(recent) if recent else floor_price

    # Weighted: floor anchors more heavily (protects against wash trades)
    return floor_price * 0.6 + sales_avg * 0.4


def classify_confidence(discount_pct: float) -> str:
    """Classify the confidence tier based on discount percentage.

    CRITICAL: >95% below fair value (almost certainly a mistake)
    HIGH: 85-95% (very likely fat finger)
    MEDIUM: 70-85% (possible deal or liquidation)
    """
    if discount_pct >= CRITICAL_THRESHOLD_PCT:
        return "CRITICAL"
    elif discount_pct >= HIGH_THRESHOLD_PCT:
        return "HIGH"
    elif discount_pct >= MEDIUM_THRESHOLD_PCT:
        return "MEDIUM"
    return "LOW"


def build_opportunity(
    marketplace: str,
    chain: str,
    asset_id: str,
    asset_name: str,
    listing_price: float,
    fair_value: float,
    currency: str,
    url: str = "",
    seller: str = "",
    extra: dict | None = None,
) -> dict | None:
    """Build a standardized opportunity dict if it meets threshold.

    Returns None if discount is below MEDIUM threshold.
    """
    if fair_value <= 0 or listing_price <= 0:
        return None

    discount_pct = round((1 - listing_price / fair_value) * 100, 1)
    confidence = classify_confidence(discount_pct)

    if confidence == "LOW":
        return None

    opp = {
        "marketplace": marketplace,
        "chain": chain,
        "asset_id": asset_id,
        "asset_name": asset_name,
        "listing_price": listing_price,
        "fair_value": fair_value,
        "discount_pct": discount_pct,
        "confidence": confidence,
        "currency": currency,
        "url": url,
        "seller": seller,
        "metadata": extra or {},
    }
    return opp
