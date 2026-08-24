from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db.models import DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce

from core.models import (
    DisbursementStatus,
    Enrollment,
    EnrollmentStatus,
    PaymentConfirmationStatus,
    Teacher,
)
from core.services.revenue_allocation import allocate_revenue, money


VALID_COMPENSATION_STATUSES = (
    EnrollmentStatus.ENROLLED,
    EnrollmentStatus.ACTIVE,
    EnrollmentStatus.COMPLETED,
)


@dataclass(frozen=True)
class CompensationTotals:
    enrollment_count: int = 0
    enrollment_amount: Decimal = Decimal("0.00")
    amount_paid: Decimal = Decimal("0.00")
    compensation_due: Decimal = Decimal("0.00")
    compensation_funded: Decimal = Decimal("0.00")
    compensation_unfunded: Decimal = Decimal("0.00")
    amount_disbursed: Decimal = Decimal("0.00")
    outstanding_compensation: Decimal = Decimal("0.00")
    outstanding_disbursement: Decimal = Decimal("0.00")


def _compensation_enrollments(*, teacher=None):
    queryset = Enrollment.objects.filter(
        status__in=VALID_COMPENSATION_STATUSES,
    )
    if teacher is not None:
        queryset = queryset.filter(session__teacher=teacher)

    decimal_output = DecimalField(max_digits=14, decimal_places=2)
    return (
        queryset.select_related(
            "student",
            "student__prospect",
            "student__prospect__contact",
            "course",
            "session",
            "session__teacher",
            "invoice",
            "disbursement",
        )
        .annotate(
            confirmed_payment_total=Coalesce(
                Sum(
                    "invoice__payments__amount_paid",
                    filter=Q(
                        invoice__payments__confirmation_status=PaymentConfirmationStatus.CONFIRMED
                    ),
                ),
                Value(Decimal("0.00")),
                output_field=decimal_output,
            )
        )
        .order_by("-enrollment_date", "-pk")
    )


def _row_for_enrollment(enrollment):
    enrollment_amount = money(
        (enrollment.fee_amount or Decimal("0.00"))
        - (enrollment.discount_amount or Decimal("0.00"))
    )
    amount_paid = min(money(enrollment.confirmed_payment_total), enrollment_amount)
    student_balance = money(max(enrollment_amount - amount_paid, Decimal("0.00")))

    entitlement = allocate_revenue(enrollment_amount).governor
    funded = min(allocate_revenue(amount_paid).governor, entitlement)

    disbursement = getattr(enrollment, "disbursement", None)
    amount_disbursed = Decimal("0.00")
    if disbursement and disbursement.status == DisbursementStatus.PAID:
        amount_disbursed = min(money(disbursement.teacher_amount), entitlement)

    return {
        "enrollment": enrollment,
        "student": enrollment.student,
        "teacher": enrollment.session.teacher,
        "course": enrollment.course,
        "enrollment_date": enrollment.enrollment_date,
        "enrollment_amount": enrollment_amount,
        "amount_paid": amount_paid,
        "student_balance": student_balance,
        "compensation_due": entitlement,
        "compensation_funded": funded,
        "compensation_unfunded": money(max(entitlement - funded, Decimal("0.00"))),
        "amount_disbursed": amount_disbursed,
        "outstanding_compensation": money(
            max(entitlement - amount_disbursed, Decimal("0.00"))
        ),
        "outstanding_disbursement": money(
            max(funded - amount_disbursed, Decimal("0.00"))
        ),
    }


def _sum_rows(rows):
    fields = (
        "enrollment_amount",
        "amount_paid",
        "compensation_due",
        "compensation_funded",
        "compensation_unfunded",
        "amount_disbursed",
        "outstanding_compensation",
        "outstanding_disbursement",
    )
    values = {
        field: money(sum((row[field] for row in rows), Decimal("0.00")))
        for field in fields
    }
    return CompensationTotals(enrollment_count=len(rows), **values)


def get_governor_compensation_data(*, user):
    """Return permission-scoped entitlement, funding, payout, and enrollment detail."""
    is_administrator = bool(user and (user.is_staff or user.is_superuser))
    teacher = None if is_administrator else getattr(user, "teacher_profile", None)
    if not is_administrator and teacher is None:
        return {
            "is_administrator": False,
            "can_view": False,
            "totals": CompensationTotals(),
            "teachers": [],
            "enrollments": [],
        }

    rows = [_row_for_enrollment(item) for item in _compensation_enrollments(teacher=teacher)]
    teacher_rows = []
    allowed_teachers = Teacher.objects.all() if is_administrator else Teacher.objects.filter(pk=teacher.pk)
    for current_teacher in allowed_teachers.order_by("first_name", "last_name"):
        current_rows = [row for row in rows if row["teacher"].pk == current_teacher.pk]
        teacher_rows.append(
            {
                "teacher": current_teacher,
                "totals": _sum_rows(current_rows),
                "enrollments": current_rows,
            }
        )

    return {
        "is_administrator": is_administrator,
        "can_view": True,
        "totals": _sum_rows(rows),
        "teachers": teacher_rows,
        "enrollments": rows,
    }
