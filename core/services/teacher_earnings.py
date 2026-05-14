from decimal import Decimal

from django.db.models import Count, Sum, Q
from django.utils import timezone

from core.models import Disbursement, DisbursementStatus, Enrollment, Teacher


def get_teacher_earnings_dashboard_data():
    """
    Return aggregated earnings and teaching stats for each teacher.

    Earnings use Disbursement.teacher_amount and exclude cancelled disbursements.
    """
    today = timezone.localdate()

    disbursement_qs = Disbursement.objects.exclude(status=DisbursementStatus.CANCELLED)

    totals_by_teacher = {
        row["teacher_id"]: {
            "today_total": row["today_total"] or Decimal("0.00"),
            "month_total": row["month_total"] or Decimal("0.00"),
            "year_total": row["year_total"] or Decimal("0.00"),
        }
        for row in disbursement_qs.values("teacher_id").annotate(
            today_total=Sum("teacher_amount", filter=Q(disbursement_date=today)),
            month_total=Sum(
                "teacher_amount",
                filter=Q(
                    disbursement_date__year=today.year,
                    disbursement_date__month=today.month,
                ),
            ),
            year_total=Sum(
                "teacher_amount",
                filter=Q(disbursement_date__year=today.year),
            ),
        )
    }

    students_by_teacher = {
        row["session__teacher_id"]: row["student_count"]
        for row in Enrollment.objects.values("session__teacher_id").annotate(
            student_count=Count("student", distinct=True)
        )
    }

    breakdown_by_teacher = {}
    breakdown_rows = (
        disbursement_qs.values(
            "teacher_id",
            "location__name",
            "enrollment__session__id",
            "enrollment__session__session_name",
            "enrollment__session__course__name",
        )
        .annotate(
            total_earnings=Sum("teacher_amount"),
            students_taught=Count("enrollment__student", distinct=True),
        )
        .order_by(
            "teacher_id",
            "enrollment__session__course__name",
            "enrollment__session__session_name",
            "location__name",
        )
    )

    for row in breakdown_rows:
        teacher_id = row["teacher_id"]
        breakdown_by_teacher.setdefault(teacher_id, []).append(
            {
                "course_name": row["enrollment__session__course__name"] or "-",
                "session_name": row["enrollment__session__session_name"] or "Unnamed Session",
                "location_name": row["location__name"] or "-",
                "earnings": row["total_earnings"] or Decimal("0.00"),
                "students_taught": row["students_taught"] or 0,
            }
        )

    teacher_rows = []
    teachers = Teacher.objects.all().order_by("first_name", "last_name")
    for teacher in teachers:
        totals = totals_by_teacher.get(
            teacher.pk,
            {
                "today_total": Decimal("0.00"),
                "month_total": Decimal("0.00"),
                "year_total": Decimal("0.00"),
            },
        )
        teacher_rows.append(
            {
                "teacher": teacher,
                "today_total": totals["today_total"],
                "month_total": totals["month_total"],
                "year_total": totals["year_total"],
                "student_count": students_by_teacher.get(teacher.pk, 0),
                "session_location_breakdown": breakdown_by_teacher.get(teacher.pk, []),
            }
        )

    return {
        "generated_on": today,
        "teachers": teacher_rows,
    }
