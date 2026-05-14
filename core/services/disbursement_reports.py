from decimal import Decimal

from django.db.models import Sum

from core.models import Disbursement, Location, Teacher


def _base_disbursement_queryset(start_date, end_date):
    return Disbursement.objects.filter(
        disbursement_date__gte=start_date,
        disbursement_date__lte=end_date,
    )


def get_location_disbursement_total(location: Location, start_date, end_date) -> Decimal:
    """Total location allocation for a location over a date range."""
    return (
        _base_disbursement_queryset(start_date, end_date)
        .filter(location=location)
        .aggregate(total=Sum("location_amount"))["total"]
        or Decimal("0.00")
    )


def get_teacher_disbursement_total(teacher: Teacher, start_date, end_date) -> Decimal:
    """Total teacher allocation for a teacher over a date range."""
    return (
        _base_disbursement_queryset(start_date, end_date)
        .filter(teacher=teacher)
        .aggregate(total=Sum("teacher_amount"))["total"]
        or Decimal("0.00")
    )


def get_disbursement_totals(start_date, end_date, teacher=None, location=None, status=None):
    """
    Return aggregated disbursement totals for a date range and optional dimensions.
    """
    queryset = _base_disbursement_queryset(start_date, end_date)
    if teacher:
        queryset = queryset.filter(teacher=teacher)
    if location:
        queryset = queryset.filter(location=location)
    if status:
        queryset = queryset.filter(status=status)

    return queryset.aggregate(
        teacher_total=Sum("teacher_amount"),
        location_total=Sum("location_amount"),
        ico_total=Sum("ico_amount"),
        gross_total=Sum("balance_due_snapshot"),
    )


def get_disbursed_total_for_period(*, report_by, start_date, end_date, teacher=None, location=None):
    """
    Return one total amount based on report dimension and date range:
    - report_by=teacher => sum(teacher_amount) for selected teacher
    - report_by=location => sum(location_amount) for selected location
    """
    if report_by == "teacher":
        if not teacher:
            return Decimal("0.00")
        return get_teacher_disbursement_total(teacher, start_date, end_date)
    if report_by == "location":
        if not location:
            return Decimal("0.00")
        return get_location_disbursement_total(location, start_date, end_date)
    return Decimal("0.00")
