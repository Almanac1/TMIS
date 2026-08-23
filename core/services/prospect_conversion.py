from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce

from core.models import (
    Invoice,
    InvoiceStatus,
    PaymentConfirmationStatus,
    Prospect,
    Student,
)


ISSUED_INVOICE_STATUSES = (
    InvoiceStatus.SENT,
    InvoiceStatus.PARTIAL,
    InvoiceStatus.PAID,
    InvoiceStatus.OVERDUE,
)
MONEY_ZERO = Decimal("0.00")


def get_or_create_student_enrollment_shell(prospect: Prospect):
    """Create the Student FK parent needed for enrollment without converting."""
    existing = Student.objects.filter(prospect=prospect).first()
    if existing is not None:
        return existing, False

    duplicate_student = prospect.find_potential_duplicate_student()
    if duplicate_student:
        raise ValidationError(
            (
                "Potential duplicate student detected "
                f"(existing Student #{duplicate_student.pk}). "
                "Please review records before enrolling."
            )
        )

    defaults = {
        "owner": prospect.owner,
        "teacher": prospect.teacher,
        "notes": prospect.notes,
    }
    try:
        with transaction.atomic():
            return Student.objects.get_or_create(
                prospect=prospect,
                defaults=defaults,
            )
    except IntegrityError:
        # A concurrent enrollment may have created the one-to-one shell.
        student = Student.objects.filter(prospect=prospect).first()
        if student is None:
            raise
        return student, False


@dataclass(frozen=True)
class ProspectConversionEligibility:
    eligible: bool
    message: str = ""
    invoice_id: Optional[int] = None
    outstanding_balance: Decimal = MONEY_ZERO


def _invoice_rows_for_prospects(prospect_ids):
    return (
        Invoice.objects.filter(enrollment__student__prospect_id__in=prospect_ids)
        .annotate(
            confirmed_payment_count=Count(
                "payments",
                filter=Q(
                    payments__confirmation_status=PaymentConfirmationStatus.CONFIRMED
                ),
            ),
            confirmed_total_paid=Coalesce(
                Sum(
                    "payments__amount_paid",
                    filter=Q(
                        payments__confirmation_status=PaymentConfirmationStatus.CONFIRMED
                    ),
                ),
                Value(MONEY_ZERO),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
        )
        .values(
            "pk",
            "invoice_number",
            "status",
            "total_amount",
            "enrollment__student__prospect_id",
            "confirmed_payment_count",
            "confirmed_total_paid",
        )
        .order_by("-issue_date", "-pk")
    )


def _blocked(message, *, invoice_id=None, outstanding=MONEY_ZERO):
    return ProspectConversionEligibility(
        eligible=False,
        message=f"Cannot convert this prospect: {message}",
        invoice_id=invoice_id,
        outstanding_balance=outstanding,
    )


def _evaluate_invoice_rows(rows):
    rows = list(rows)
    if not rows:
        return _blocked("no donation statement has been issued.")

    active_rows = [row for row in rows if row["status"] != InvoiceStatus.CANCELLED]
    if not active_rows:
        return _blocked("no valid donation statement has been issued.")

    draft = next(
        (row for row in active_rows if row["status"] not in ISSUED_INVOICE_STATUSES),
        None,
    )
    if draft:
        return _blocked(
            "the donation statement has not been issued.",
            invoice_id=draft["pk"],
        )

    for row in active_rows:
        invoice_id = row["pk"]
        if not row["confirmed_payment_count"]:
            return _blocked(
                "payment has not been received.",
                invoice_id=invoice_id,
            )

        total_amount = Decimal(row["total_amount"] or MONEY_ZERO)
        confirmed_paid = Decimal(row["confirmed_total_paid"] or MONEY_ZERO)
        outstanding = max(MONEY_ZERO, total_amount - confirmed_paid).quantize(
            Decimal("0.01")
        )
        if outstanding > MONEY_ZERO:
            return _blocked(
                (
                    "the donation statement has an outstanding balance of "
                    f"GHS {outstanding:.2f}."
                ),
                invoice_id=invoice_id,
                outstanding=outstanding,
            )

    return ProspectConversionEligibility(eligible=True)


def get_prospect_conversion_eligibility(prospect: Prospect):
    rows = _invoice_rows_for_prospects([prospect.pk])
    return _evaluate_invoice_rows(rows)


def get_prospect_conversion_eligibility_map(prospects):
    prospects = list(prospects)
    prospect_ids = [prospect.pk for prospect in prospects]
    grouped_rows = {prospect_id: [] for prospect_id in prospect_ids}
    if prospect_ids:
        for row in _invoice_rows_for_prospects(prospect_ids):
            grouped_rows[row["enrollment__student__prospect_id"]].append(row)
    return {
        prospect_id: _evaluate_invoice_rows(grouped_rows[prospect_id])
        for prospect_id in prospect_ids
    }


def attach_prospect_conversion_eligibility(prospects):
    prospects = list(prospects)
    eligibility_map = get_prospect_conversion_eligibility_map(prospects)
    for prospect in prospects:
        eligibility = eligibility_map[prospect.pk]
        prospect.student_conversion_eligible = eligibility.eligible
        prospect.student_conversion_block_message = eligibility.message
    return prospects


def validate_prospect_conversion_financial_eligibility(prospect: Prospect):
    eligibility = get_prospect_conversion_eligibility(prospect)
    if not eligibility.eligible:
        raise ValidationError(eligibility.message)
    return eligibility
