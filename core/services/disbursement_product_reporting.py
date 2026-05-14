from decimal import Decimal

from django.db.models import Count, Sum

from core.models import Disbursement, DisbursementStatus


def get_disbursement_reporting_data(*, start_date, end_date, report_by, teacher=None, location=None):
    """
    Product-facing disbursement report data.

    report_by:
    - teacher => totals from teacher_amount
    - location => totals from location_amount
    """
    queryset = (
        Disbursement.objects.exclude(status=DisbursementStatus.CANCELLED)
        .filter(disbursement_date__gte=start_date, disbursement_date__lte=end_date)
        .select_related("teacher", "location", "enrollment", "enrollment__session")
    )

    if teacher:
        queryset = queryset.filter(teacher=teacher)
    if location:
        queryset = queryset.filter(location=location)

    amount_field = "teacher_amount" if report_by == "teacher" else "location_amount"

    total_amount = queryset.aggregate(total=Sum(amount_field)).get("total") or Decimal("0.00")

    breakdown_rows = (
        queryset.values(
            "disbursement_date",
            "teacher__first_name",
            "teacher__last_name",
            "location__name",
            "enrollment__session__session_name",
            "status",
        )
        .annotate(
            row_total=Sum(amount_field),
            disbursement_count=Count("id"),
        )
        .order_by("-disbursement_date", "teacher__first_name", "location__name")
    )

    return {
        "amount_field": amount_field,
        "total_amount": total_amount,
        "breakdown_rows": breakdown_rows,
        "row_count": len(breakdown_rows),
    }
