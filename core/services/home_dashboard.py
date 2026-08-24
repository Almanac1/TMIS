from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, F, Max, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncMonth
from django.utils import timezone

from core.models import (
    Communication,
    CommunicationType,
    CourseSession,
    DeliveryStatus,
    Enrollment,
    EnrollmentStatus,
    Inquiry,
    InquiryStatus,
    Invoice,
    Payment,
    PaymentConfirmationStatus,
    ProspectStatus,
    RecipientType,
    Student,
)
from core.services.prospect_pipeline import get_user_scoped_prospect_queryset
from core.services.governor_compensation import get_governor_compensation_data
from core.services.ownership import scope_queryset_for_user
from core.services.reporting_counts import count_unique_people


CHECK_IN_DAY_OFFSETS = (3, 10, 20)
CHECK_IN_ANCHOR_DESCRIPTION = (
    "Anchor date = earliest enrollment date in the TM Introductory Program for each student."
)


def _to_float(value):
    return float(value or Decimal("0.00"))


def _month_start(date_value):
    return date_value.replace(day=1)


def _add_months(date_value, months):
    year = date_value.year + (date_value.month - 1 + months) // 12
    month = (date_value.month - 1 + months) % 12 + 1
    return date_value.replace(year=year, month=month, day=1)


def _last_n_month_starts(today, months=6):
    current = _month_start(today)
    first = _add_months(current, -(months - 1))
    return [_add_months(first, i) for i in range(months)]


def _month_series_map(rows, key_field):
    series = {}
    for row in rows:
        month_value = row.get("month")
        if month_value:
            normalized = month_value.date() if hasattr(month_value, "date") else month_value
            series[_month_start(normalized)] = row.get(key_field, 0) or 0
    return series


def _is_check_in_completed_for_window(sent_dates, due_date, next_due_date=None):
    """A check-in is considered complete if at least one follow-up exists in its due window."""
    for sent_date in sent_dates:
        if sent_date < due_date:
            continue
        if next_due_date and sent_date >= next_due_date:
            continue
        return True
    return False


def _get_visible_enrollments(*, user):
    queryset = Enrollment.objects.all()
    if not user or not getattr(user, "is_authenticated", False):
        return queryset.none()
    if user.is_staff or user.is_superuser:
        return queryset
    teacher = getattr(user, "teacher_profile", None)
    if teacher is not None:
        return queryset.filter(session__teacher=teacher)
    return queryset.filter(student__owner=user)


def _next_prospect_action(prospect):
    if not prospect.last_contact:
        return "Make first contact"
    if prospect.status == ProspectStatus.QUALIFIED:
        return "Review enrollment readiness"
    return "Follow up"


def _build_student_check_in_reminders(*, user, today):
    intro_enrollments = list(
        _get_visible_enrollments(user=user)
        .filter(session__course__name="TM Introductory Program")
        .select_related("course", "session__teacher")
        .order_by("student_id", "enrollment_date", "pk")
    )
    intro_by_student = {}
    for enrollment in intro_enrollments:
        intro_by_student.setdefault(enrollment.student_id, enrollment)
    visible_students = (
        Student.objects.filter(
            pk__in=intro_by_student,
            enrollment_status__in=[
                EnrollmentStatus.ACTIVE,
                EnrollmentStatus.ENROLLED,
            ],
        )
        .select_related("prospect", "prospect__contact", "teacher", "owner")
    )

    students = list(visible_students)
    if not students:
        return {
            "anchor_description": CHECK_IN_ANCHOR_DESCRIPTION,
            "due_today": [],
            "overdue": [],
            "total_count": 0,
        }

    student_ids = [student.pk for student in students]
    follow_ups = (
        Communication.objects.filter(
            student_id__in=student_ids,
            recipient_type=RecipientType.STUDENT,
            communication_type=CommunicationType.FOLLOW_UP,
            sent_at__isnull=False,
        )
        .values("student_id", "sent_at")
        .order_by("student_id", "sent_at")
    )
    follow_up_map = {}
    for row in follow_ups:
        sent_dt = row["sent_at"]
        sent_date = sent_dt.date() if hasattr(sent_dt, "date") else sent_dt
        follow_up_map.setdefault(row["student_id"], []).append(sent_date)

    due_today = []
    overdue = []
    for student in students:
        intro_enrollment = intro_by_student[student.pk]
        anchor_dt = intro_enrollment.enrollment_date
        anchor_date = anchor_dt.date() if hasattr(anchor_dt, "date") else anchor_dt
        sent_dates = follow_up_map.get(student.pk, [])

        due_dates = {
            day: anchor_date + timedelta(days=day)
            for day in CHECK_IN_DAY_OFFSETS
        }
        next_day_map = {3: due_dates[10], 10: due_dates[20], 20: None}

        for day in CHECK_IN_DAY_OFFSETS:
            due_date = due_dates[day]
            completed = _is_check_in_completed_for_window(
                sent_dates,
                due_date,
                next_day_map[day],
            )
            if completed:
                continue

            entry = {
                "student": student,
                "enrollment": intro_enrollment,
                "course": intro_enrollment.course,
                "governor": intro_enrollment.session.teacher,
                "day_label": f"Day {day}",
                "due_date": due_date,
            }
            if due_date == today:
                due_today.append(entry)
            elif due_date < today:
                entry["days_overdue"] = (today - due_date).days
                overdue.append(entry)

    due_today.sort(key=lambda item: (item["due_date"], item["student"].pk))
    overdue.sort(key=lambda item: (-item["days_overdue"], item["due_date"], item["student"].pk))

    return {
        "anchor_description": CHECK_IN_ANCHOR_DESCRIPTION,
        "due_today": due_today,
        "overdue": overdue,
        "total_count": len(due_today) + len(overdue),
    }


def get_home_dashboard_data(*, user):
    today = timezone.localdate()
    month_starts = _last_n_month_starts(today, months=6)
    month_labels = [month.strftime("%b %Y") for month in month_starts]
    month_start_floor = month_starts[0]

    prospects = get_user_scoped_prospect_queryset(user)
    all_prospects = get_user_scoped_prospect_queryset(user, include_archived=True)

    teacher_profile = None
    if user.is_authenticated and not user.is_staff and not user.is_superuser:
        teacher_profile = getattr(user, "teacher_profile", None)

    enrollment_scope = _get_visible_enrollments(user=user).exclude(
        status__in=[
            EnrollmentStatus.CANCELLED,
            EnrollmentStatus.WITHDRAWN,
            EnrollmentStatus.INACTIVE,
        ]
    )

    # KPI conversion totals
    converted_filter = (
        Q(status=ProspectStatus.CONVERTED)
        | Q(converted_to_student=True)
        | Q(converted_student__isnull=False)
    )
    prospect_total = count_unique_people(all_prospects, contact_field="contact_id")
    converted_total = count_unique_people(
        all_prospects.filter(converted_filter),
        contact_field="contact_id",
    )
    conversion_percent = round((converted_total / prospect_total) * 100, 1) if prospect_total else 0

    enrollment_course_rows = list(
        enrollment_scope.values("course__name")
        .annotate(total=Count("pk"))
        .order_by("-total", "course__name")[:8]
    )

    # Open operational inquiries
    inquiry_scope = scope_queryset_for_user(
        queryset=Inquiry.objects.all(),
        model=Inquiry,
        user=user,
    )
    open_inquiries = inquiry_scope.filter(status=InquiryStatus.OPEN).count()
    in_progress_inquiries = inquiry_scope.filter(status=InquiryStatus.IN_PROGRESS).count()

    # Upcoming sessions
    sessions_scope = CourseSession.objects.filter(
        start_date__date__gte=today,
        start_date__date__lte=today + timedelta(days=14),
    )
    if teacher_profile:
        sessions_scope = sessions_scope.filter(teacher=teacher_profile)
    elif not (user.is_staff or user.is_superuser):
        sessions_scope = sessions_scope.filter(
            Q(owner=user) | Q(enrollments__in=enrollment_scope)
        ).distinct()

    upcoming_rows = (
        sessions_scope.annotate(enrolled_count=Count("enrollments", distinct=True))
        .select_related("course", "teacher", "location")
        .order_by("start_date")[:5]
    )

    # Revenue snapshot and monthly trend
    invoice_scope = Invoice.objects.filter(enrollment__in=enrollment_scope).distinct()

    invoice_scope = invoice_scope.annotate(
        paid_confirmed=Coalesce(
            Sum(
                "payments__amount_paid",
                filter=Q(
                    payments__confirmation_status=PaymentConfirmationStatus.CONFIRMED
                ),
            ),
            Value(Decimal("0.00")),
        ),
    ).annotate(outstanding=F("total_amount") - F("paid_confirmed"))

    invoiced_total = invoice_scope.aggregate(
        total=Coalesce(Sum("total_amount"), Value(Decimal("0.00")))
    )["total"]

    confirmed_collected = Payment.objects.filter(
        invoice__in=invoice_scope,
        confirmation_status=PaymentConfirmationStatus.CONFIRMED,
    ).aggregate(total=Coalesce(Sum("amount_paid"), Value(Decimal("0.00"))))["total"]

    outstanding_total = invoice_scope.aggregate(
        total=Coalesce(Sum("outstanding"), Value(Decimal("0.00")))
    )["total"]

    invoice_month_rows = (
        invoice_scope.filter(issue_date__gte=month_start_floor)
        .annotate(month=TruncMonth("issue_date"))
        .values("month")
        .annotate(total=Coalesce(Sum("total_amount"), Value(Decimal("0.00"))))
        .order_by("month")
    )
    payment_scope = Payment.objects.filter(
        invoice__enrollment__in=enrollment_scope,
        confirmation_status=PaymentConfirmationStatus.CONFIRMED,
    )
    payment_month_rows = (
        payment_scope.filter(payment_date__date__gte=month_start_floor)
        .annotate(month=TruncMonth("payment_date"))
        .values("month")
        .annotate(total=Coalesce(Sum("amount_paid"), Value(Decimal("0.00"))))
        .order_by("month")
    )
    invoice_month_map = _month_series_map(invoice_month_rows, "total")
    payment_month_map = _month_series_map(payment_month_rows, "total")

    # Compact operational follow-up widget
    attention_prospects = list(
        prospects.select_related("contact", "owner", "teacher", "course_interest")
        .annotate(
            last_contact=Max(
                "communications__sent_at",
                filter=Q(communications__sent_at__isnull=False),
            ),
            contact_attempts=Count(
                "communications",
                filter=(
                    Q(communications__sent_at__isnull=False)
                    | ~Q(communications__delivery_status=DeliveryStatus.QUEUED)
                ),
                distinct=True,
            ),
        )
        .order_by(F("last_contact").asc(nulls_first=True), "created_at")[:5]
    )
    for prospect in attention_prospects:
        prospect.dashboard_next_action = _next_prospect_action(prospect)

    check_in_reminders = _build_student_check_in_reminders(user=user, today=today)
    governor_compensation = get_governor_compensation_data(user=user)

    return {
        "kpis": {
            "total_prospects": prospect_total,
            "converted_students": converted_total,
            "conversion_percent": conversion_percent,
            "active_students": count_unique_people(
                Student.objects.filter(enrollments__in=enrollment_scope).exclude(
                    enrollment_status=EnrollmentStatus.INACTIVE
                ),
                contact_field="prospect__contact_id",
            ),
            "open_inquiries": open_inquiries + in_progress_inquiries,
            "outstanding_amount": _to_float(outstanding_total),
            "invoiced_total": _to_float(invoiced_total),
            "collected_total": _to_float(confirmed_collected),
        },
        "charts": {
            "enrollments_by_course": {
                "labels": [row["course__name"] or "Unassigned" for row in enrollment_course_rows],
                "values": [row["total"] for row in enrollment_course_rows],
            },
            "revenue_trend": {
                "labels": month_labels,
                "invoiced": [
                    _to_float(invoice_month_map.get(month, Decimal("0.00")))
                    for month in month_starts
                ],
                "received": [
                    _to_float(payment_month_map.get(month, Decimal("0.00")))
                    for month in month_starts
                ],
            },
        },
        "governor_compensation": governor_compensation,
        "operations": {
            "upcoming_sessions": upcoming_rows,
            "attention_prospects": attention_prospects,
        },
        "check_in_reminders": check_in_reminders,
    }
