from __future__ import annotations

from decimal import Decimal, InvalidOperation


CURRENCY_SYMBOL = "₦"


def format_currency(value) -> str:
    """Format a monetary value for user-facing TMIS output."""
    try:
        amount = Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        amount = Decimal("0.00")
    return f"{CURRENCY_SYMBOL}{amount:,.2f}"
