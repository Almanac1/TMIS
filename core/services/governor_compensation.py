from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import DecimalField, Prefetch, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from core.models import (
    DisbursementStatus,
    Enrollment,
    EnrollmentStatus,
    Payment,
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


def _compensation_enrollments(*, teacher=None, filters=None):
    filters = filters or {}
    queryset = Enrollment.objects.filter(
        status__in=VALID_COMPENSATION_STATUSES,
    )
    if teacher is not None:
        queryset = queryset.filter(session__teacher=teacher)
    if filters.get("teacher") is not None:
        queryset = queryset.filter(session__teacher=filters["teacher"])
    if filters.get("course") is not None:
        queryset = queryset.filter(course=filters["course"])
    if filters.get("start_date"):
        queryset = queryset.filter(enrollment_date__date__gte=filters["start_date"])
    if filters.get("end_date"):
        queryset = queryset.filter(enrollment_date__date__lte=filters["end_date"])

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
        .prefetch_related(
            Prefetch(
                "invoice__payments",
                queryset=Payment.objects.filter(
                    confirmation_status=PaymentConfirmationStatus.CONFIRMED
                ).order_by("payment_date", "pk"),
                to_attr="confirmed_compensation_payments",
            )
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

    invoice = getattr(enrollment, "invoice", None)
    confirmed_payments = (
        getattr(invoice, "confirmed_compensation_payments", []) if invoice else []
    )
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
        "disbursement_status": disbursement.status if disbursement else "none",
        "disbursement": disbursement,
        "confirmed_payments": confirmed_payments,
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


def _funding_status(row):
    if row["compensation_funded"] <= Decimal("0.00"):
        return "unfunded"
    if row["compensation_funded"] >= row["compensation_due"]:
        return "fully_funded"
    return "partially_funded"


def _month_start(value):
    if hasattr(value, "date"):
        value = timezone.localtime(value).date() if timezone.is_aware(value) else value.date()
    return value.replace(day=1)


def _month_sequence(start, end):
    current = start
    while current <= end:
        yield current
        current = date(current.year + (current.month == 12), (current.month % 12) + 1, 1)


def _chart_analytics(rows, teacher_rows):
    accrued_by_month = defaultdict(lambda: Decimal("0.00"))
    funded_by_month = defaultdict(lambda: Decimal("0.00"))
    disbursed_by_month = defaultdict(lambda: Decimal("0.00"))
    course_totals = defaultdict(
        lambda: {"due": Decimal("0.00"), "funded": Decimal("0.00"), "disbursed": Decimal("0.00")}
    )
    aging = {
        "0–30 days": Decimal("0.00"),
        "31–60 days": Decimal("0.00"),
        "61–90 days": Decimal("0.00"),
        "90+ days": Decimal("0.00"),
    }
    today = timezone.localdate()

    for row in rows:
        accrued_by_month[_month_start(row["enrollment_date"])] += row["compensation_due"]
        course = str(row["course"])
        course_totals[course]["due"] += row["compensation_due"]
        course_totals[course]["funded"] += row["compensation_funded"]
        course_totals[course]["disbursed"] += row["amount_disbursed"]

        remaining_funding = row["compensation_due"]
        for payment in row["confirmed_payments"]:
            allocation = min(allocate_revenue(payment.amount_paid).governor, remaining_funding)
            if allocation <= Decimal("0.00"):
                break
            funded_by_month[_month_start(payment.payment_date)] += allocation
            remaining_funding -= allocation

        disbursement = row["disbursement"]
        if disbursement and disbursement.status == DisbursementStatus.PAID:
            disbursed_by_month[_month_start(disbursement.disbursement_date)] += row[
                "amount_disbursed"
            ]

        if row["outstanding_disbursement"] > Decimal("0.00"):
            dates = [
                timezone.localtime(item.payment_date).date()
                if timezone.is_aware(item.payment_date)
                else item.payment_date.date()
                for item in row["confirmed_payments"]
            ]
            funded_on = min(dates) if dates else today
            days = max((today - funded_on).days, 0)
            bucket = (
                "0–30 days"
                if days <= 30
                else "31–60 days"
                if days <= 60
                else "61–90 days"
                if days <= 90
                else "90+ days"
            )
            aging[bucket] += row["outstanding_disbursement"]

    all_months = set(accrued_by_month) | set(funded_by_month) | set(disbursed_by_month)
    months = list(_month_sequence(min(all_months), max(all_months))) if all_months else []
    top_teachers = sorted(
        (item for item in teacher_rows if item["totals"].enrollment_count),
        key=lambda item: item["totals"].compensation_due,
        reverse=True,
    )[:10]
    courses = sorted(
        course_totals,
        key=lambda name: course_totals[name]["due"],
        reverse=True,
    )

    def numbers(values):
        return [float(money(value)) for value in values]

    return {
        "monthly": {
            "labels": [item.strftime("%b %Y") for item in months],
            "accrued": numbers(accrued_by_month[item] for item in months),
            "funded": numbers(funded_by_month[item] for item in months),
            "disbursed": numbers(disbursed_by_month[item] for item in months),
        },
        "governors": {
            "labels": [str(item["teacher"]) for item in top_teachers],
            "due": numbers(item["totals"].compensation_due for item in top_teachers),
            "funded": numbers(item["totals"].compensation_funded for item in top_teachers),
            "disbursed": numbers(item["totals"].amount_disbursed for item in top_teachers),
        },
        "courses": {
            "labels": courses,
            "due": numbers(course_totals[name]["due"] for name in courses),
            "funded": numbers(course_totals[name]["funded"] for name in courses),
        },
        "aging": {
            "labels": list(aging),
            "values": numbers(aging.values()),
        },
    }


def get_governor_compensation_data(*, user, filters=None):
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

    filters = filters or {}
    rows = [
        _row_for_enrollment(item)
        for item in _compensation_enrollments(teacher=teacher, filters=filters)
    ]
    if filters.get("funding_status"):
        rows = [row for row in rows if _funding_status(row) == filters["funding_status"]]
    if filters.get("disbursement_status"):
        rows = [
            row
            for row in rows
            if row["disbursement_status"] == filters["disbursement_status"]
        ]
    teacher_rows = []
    allowed_teachers = Teacher.objects.all() if is_administrator else Teacher.objects.filter(pk=teacher.pk)
    for current_teacher in allowed_teachers.order_by("first_name", "last_name"):
        current_rows = [row for row in rows if row["teacher"].pk == current_teacher.pk]
        if current_rows:
            teacher_rows.append(
                {
                    "teacher": current_teacher,
                    "totals": _sum_rows(current_rows),
                    "enrollments": current_rows,
                }
            )

    totals = _sum_rows(rows)
    funded_but_undisbursed = totals.outstanding_disbursement
    position_unfunded = money(
        max(totals.compensation_due - totals.amount_disbursed - funded_but_undisbursed, Decimal("0.00"))
    )
    funding_rate = (
        float((totals.compensation_funded / totals.compensation_due) * Decimal("100"))
        if totals.compensation_due
        else 0.0
    )
    return {
        "is_administrator": is_administrator,
        "can_view": True,
        "totals": totals,
        "teachers": teacher_rows,
        "enrollments": rows,
        "position": {
            "disbursed": float(totals.amount_disbursed),
            "funded": float(totals.compensation_funded),
            "funded_undisbursed": float(funded_but_undisbursed),
            "unfunded": float(position_unfunded),
            "funding_rate": round(funding_rate, 1),
        },
        "analytics": _chart_analytics(rows, teacher_rows),
    }
