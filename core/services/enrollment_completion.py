from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce

from core.models import (
    Enrollment,
    EnrollmentStatus,
    Invoice,
    InvoiceStatus,
    PaymentConfirmationStatus,
    Student,
)


MONEY_ZERO = Decimal("0.00")
ISSUED_INVOICE_STATUSES = {
    InvoiceStatus.SENT,
    InvoiceStatus.PARTIAL,
    InvoiceStatus.PAID,
    InvoiceStatus.OVERDUE,
}
NON_BILLABLE_ENROLLMENT_STATUSES = {
    EnrollmentStatus.CANCELLED,
    EnrollmentStatus.WITHDRAWN,
    EnrollmentStatus.INACTIVE,
}


@dataclass(frozen=True)
class CompletionFinancialCheck:
    eligible: bool
    message: str
    outstanding_balance: Decimal = MONEY_ZERO
    invoice_id: int | None = None


def _money(value) -> Decimal:
    return Decimal(value or MONEY_ZERO).quantize(Decimal("0.01"))


def get_confirmed_payment_total(invoice: Invoice) -> Decimal:
    return _money(
        invoice.payments.filter(
            confirmation_status=PaymentConfirmationStatus.CONFIRMED,
        ).aggregate(
            total=Coalesce(
                Sum("amount_paid"),
                Value(MONEY_ZERO),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            )
        )["total"]
    )


def check_enrollment_completion_financials(enrollment: Enrollment) -> CompletionFinancialCheck:
    if not enrollment.pk:
        return CompletionFinancialCheck(
            eligible=False,
            message="Enrollment cannot be completed before it is saved and invoiced.",
        )
    try:
        invoice = enrollment.invoice
    except Invoice.DoesNotExist:
        return CompletionFinancialCheck(
            eligible=False,
            message="Enrollment cannot be completed until an invoice has been issued.",
        )
    if invoice.status not in ISSUED_INVOICE_STATUSES:
        return CompletionFinancialCheck(
            eligible=False,
            invoice_id=invoice.pk,
            message="Enrollment cannot be completed because its invoice is not active and issued.",
        )

    outstanding = max(
        MONEY_ZERO,
        _money(invoice.total_amount) - get_confirmed_payment_total(invoice),
    ).quantize(Decimal("0.01"))
    if outstanding > MONEY_ZERO:
        return CompletionFinancialCheck(
            eligible=False,
            invoice_id=invoice.pk,
            outstanding_balance=outstanding,
            message=(
                "Enrollment cannot be completed until the outstanding balance of "
                f"GHS {outstanding:.2f} is paid with confirmed payments."
            ),
        )
    return CompletionFinancialCheck(
        eligible=True,
        invoice_id=invoice.pk,
        message="Enrollment invoice is fully paid.",
    )


def validate_enrollment_completion_financials(enrollment: Enrollment) -> None:
    result = check_enrollment_completion_financials(enrollment)
    if not result.eligible:
        raise ValidationError({"status": result.message})


def check_student_completion_financials(student: Student) -> CompletionFinancialCheck:
    if not student.pk:
        return CompletionFinancialCheck(
            eligible=False,
            message="Student cannot be completed before enrollment and invoicing.",
        )

    billable_enrollments = list(
        Enrollment.objects.filter(student=student)
        .exclude(status__in=NON_BILLABLE_ENROLLMENT_STATUSES)
        .select_related("invoice")
        .order_by("pk")
    )
    if not billable_enrollments:
        return CompletionFinancialCheck(
            eligible=False,
            message="Student cannot be completed without an active enrollment.",
        )
    for enrollment in billable_enrollments:
        result = check_enrollment_completion_financials(enrollment)
        if not result.eligible:
            return CompletionFinancialCheck(
                eligible=False,
                invoice_id=result.invoice_id,
                outstanding_balance=result.outstanding_balance,
                message=f"Student cannot be completed. Enrollment #{enrollment.pk}: {result.message}",
            )

    # A cancelled enrollment can still carry a live accounting invoice. It
    # remains a real outstanding balance until finance cancels or settles it.
    active_invoices = Invoice.objects.filter(enrollment__student=student).exclude(
        status=InvoiceStatus.CANCELLED,
    )
    for invoice in active_invoices:
        outstanding = max(
            MONEY_ZERO,
            _money(invoice.total_amount) - get_confirmed_payment_total(invoice),
        ).quantize(Decimal("0.01"))
        if outstanding > MONEY_ZERO:
            return CompletionFinancialCheck(
                eligible=False,
                invoice_id=invoice.pk,
                outstanding_balance=outstanding,
                message=(
                    "Student cannot be completed while invoice "
                    f"{invoice.invoice_number} has an outstanding confirmed-payment "
                    f"balance of GHS {outstanding:.2f}."
                ),
            )
    return CompletionFinancialCheck(
        eligible=True,
        message="All enrollment invoices are fully paid.",
    )


def validate_student_completion_financials(student: Student) -> None:
    result = check_student_completion_financials(student)
    if not result.eligible:
        raise ValidationError({"enrollment_status": result.message})
