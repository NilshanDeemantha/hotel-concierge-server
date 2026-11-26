"""The cancellation and refund policy: how much of a paid booking is refunded, by days before check-in."""

from datetime import date

# (days before check-in, at least) -> percent of the payment that is refunded. The first row that fits wins.
TIERS = ((30, 100), (21, 90), (14, 75), (7, 50), (0, 25))

POLICY = (
    "Cancellation and refunds: cancel 30 or more days before check-in for a full refund. "
    "21 to 29 days before check-in, 10% is deducted. 14 to 20 days before, 25% is deducted. "
    "7 to 13 days before, 50% is deducted. Within 7 days of check-in, 75% is deducted. "
    "The refund goes back to the card that paid, usually in 5 to 10 business days. "
    "A booking that is not paid in time is cancelled automatically and nothing is charged."
)


def quote(check_in: str, total_lkr: int, today: date) -> dict:
    """What a guest gets back if they cancel today. A stay that has already started gets nothing (days is negative)."""
    days = (date.fromisoformat(check_in) - today).days
    percent = next((p for d, p in TIERS if days >= d), 0)
    return {"daysBeforeCheckIn": days, "refundPercent": percent, "deductPercent": 100 - percent,
            "refundLKR": total_lkr * percent // 100}
