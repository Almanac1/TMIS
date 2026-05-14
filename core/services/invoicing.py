from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import Enrollment, Invoice, InvoiceStatus

DEFAULT_TAX_RATE = Decimal("0.00")
INVOICE_DUE_DAYS = 14


def _as_decimal(value) -> Decimal:
    return Decimal(str(value or "0.00"))


def _quantize_currency(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def get_tax_rate() -> Decimal:
    configured = getattr(settings, "TMIS_INVOICE_TAX_RATE", DEFAULT_TAX_RATE)
    return _as_decimal(configured)


def _generate_unique_invoice_number() -> str:
    date_prefix = timezone.localdate().strftime("%Y%m%d")
    for _ in range(100):
        suffix = timezone.now().strftime("%H%M%S%f")[-8:]
        invoice_number = f"INV-{date_prefix}-{suffix}"
        if not Invoice.objects.filter(invoice_number=invoice_number).exists():
            return invoice_number
    raise RuntimeError("Unable to generate a unique invoice number.")


def generate_invoice_for_enrollment(enrollment: Enrollment):
    """
    Idempotently generate an invoice for an enrollment.

    Returns:
        tuple[Invoice, bool]: (invoice, created)
    """
    try:
        with transaction.atomic():
            enrollment_locked = Enrollment.objects.select_for_update().get(pk=enrollment.pk)

            try:
                return enrollment_locked.invoice, False
            except Invoice.DoesNotExist:
                pass

            subtotal = _quantize_currency(_as_decimal(enrollment_locked.fee_amount))
            discount_amount = _quantize_currency(_as_decimal(enrollment_locked.discount_amount))
            taxable_amount = max(Decimal("0.00"), subtotal - discount_amount)
            tax_amount = _quantize_currency(taxable_amount * get_tax_rate())
            total_amount = _quantize_currency(taxable_amount + tax_amount)

            issue_date = timezone.localdate()
            due_date = issue_date + timedelta(days=INVOICE_DUE_DAYS)

            invoice = Invoice.objects.create(
                owner=enrollment_locked.student.owner,
                enrollment=enrollment_locked,
                invoice_number=_generate_unique_invoice_number(),
                issue_date=issue_date,
                due_date=due_date,
                subtotal=subtotal,
                discount_amount=discount_amount,
                tax_amount=tax_amount,
                total_amount=total_amount,
                status=InvoiceStatus.DRAFT,
            )
            return invoice, True
    except IntegrityError:
        # Handle rare race conditions where a concurrent worker created it first.
        return Invoice.objects.get(enrollment=enrollment), False
