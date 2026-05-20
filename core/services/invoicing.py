from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import Enrollment, Invoice, InvoiceStatus

DEFAULT_TAX_RATE = Decimal("0.00")
INVOICE_DUE_DAYS = 14
DEFAULT_YEAR = 2026

COURSE_CODE_ALIASES = {
    "AL": ("tm-sidhi", "tm-sidhi course", "sidhi", "sid"),
    "AT": ("advanced technique", "advanced technique i", "advanced technique ii", "at"),
    "TMA": ("tm - adult", "tm adult", "adult tm"),
    "TMC": ("tm - couple", "tm couple", "couple tm"),
    "TMF": ("tm - family", "tm family", "family tm"),
    "TMS": ("tm - student", "tm student", "student tm"),
    "TMWOW": ("tm - word of wisdom", "tm word of wisdom", "word of wisdom"),
    "KC": ("knowledge courses", "knowledge course", "kc"),
}


def _as_decimal(value) -> Decimal:
    return Decimal(str(value or "0.00"))


def _quantize_currency(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def get_tax_rate() -> Decimal:
    configured = getattr(settings, "TMIS_INVOICE_TAX_RATE", DEFAULT_TAX_RATE)
    return _as_decimal(configured)


def _normalize(value: str) -> str:
    return " ".join((value or "").strip().lower().replace("-", " ").replace("_", " ").split())


def _resolve_course_code(enrollment: Enrollment) -> str:
    course = getattr(enrollment.session, "course", None)
    if not course:
        return "UNK"

    raw_code = (getattr(course, "code", "") or "").strip()
    if raw_code:
        return raw_code

    normalized_name = _normalize(getattr(course, "name", ""))
    for code, aliases in COURSE_CODE_ALIASES.items():
        if normalized_name in {_normalize(alias) for alias in aliases}:
            if code == "TMA":
                return "TMa"
            if code == "TMC":
                return "TMc"
            if code == "TMF":
                return "TMf"
            if code == "TMS":
                return "TMs"
            if code == "TMWOW":
                return "TMwow"
            if code == "KC":
                return "Kc"
            return code
    return "UNK"


def _next_sequence_for(code: str, year: int) -> int:
    prefix = f"{code}-{year}-"
    existing = (
        Invoice.objects.filter(invoice_number__startswith=prefix)
        .order_by("-invoice_number")
        .values_list("invoice_number", flat=True)
    )
    max_seq = 0
    for number in existing:
        parts = number.rsplit("-", 1)
        if len(parts) != 2:
            continue
        try:
            seq = int(parts[1])
        except (TypeError, ValueError):
            continue
        if seq > max_seq:
            max_seq = seq
    return max_seq + 1


def _generate_course_invoice_number(enrollment: Enrollment, issue_date) -> str:
    code = _resolve_course_code(enrollment)
    year = issue_date.year if issue_date else DEFAULT_YEAR
    for _ in range(100):
        sequence = _next_sequence_for(code, year)
        candidate = f"{code}-{year}-{sequence:04d}"
        if not Invoice.objects.filter(invoice_number=candidate).exists():
            return candidate
    raise RuntimeError("Unable to generate a unique course-based invoice number.")


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
                invoice_number=_generate_course_invoice_number(enrollment_locked, issue_date),
                issue_date=issue_date,
                due_date=due_date,
                subtotal=subtotal,
                discount_amount=discount_amount,
                tax_amount=tax_amount,
                total_amount=total_amount,
                status=InvoiceStatus.SENT,
            )
            return invoice, True
    except IntegrityError:
        # Handle rare race conditions where a concurrent worker created it first.
        return Invoice.objects.get(enrollment=enrollment), False
