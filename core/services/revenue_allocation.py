from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


MONEY_PLACES = Decimal("0.01")

ICO_RATE = Decimal("0.125")
NATIONAL_OFFICE_RATE = Decimal("0.20")
GOVERNOR_RATE = Decimal("0.50")
MARKETING_RATE = Decimal("0.175")


def money(value) -> Decimal:
    return Decimal(str(value or "0.00")).quantize(MONEY_PLACES, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class RevenueAllocation:
    amount: Decimal
    ico: Decimal
    national_office: Decimal
    governor: Decimal
    marketing: Decimal


def allocate_revenue(amount) -> RevenueAllocation:
    """Allocate a charge or receipt using TMIS's single revenue-allocation formula."""
    amount = money(amount)
    if amount < Decimal("0.00"):
        raise ValueError("Revenue allocation amount cannot be negative.")

    ico = money(amount * ICO_RATE)
    national_office = money(amount * NATIONAL_OFFICE_RATE)
    governor = money(amount * GOVERNOR_RATE)
    # Assign any currency-rounding remainder to marketing so the allocation is exact.
    marketing = money(amount - ico - national_office - governor)
    return RevenueAllocation(
        amount=amount,
        ico=ico,
        national_office=national_office,
        governor=governor,
        marketing=marketing,
    )
