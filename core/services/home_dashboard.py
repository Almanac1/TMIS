from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, F, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce, TruncMonth
from django.utils import timezone

from core.models import (
    Communication,
    CommunicationType,
    CourseSession,
    Disbursement,
    DisbursementStatus,
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
from core.services.ownership import scope_queryset_for_user


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


def _build_student_check_in_reminders(*, user, today):
    intro_enrollment_subquery = (
        Enrollment.objects.filter(
            student=OuterRef("pk"),
            session__course__name="TM Introductory Program",
        )
        .order_by("enrollment_date")
        .values("enrollment_date")[:1]
    )

    visible_students = (
        scope_queryset_for_user(
            queryset=Student.objects.filter(
                enrollment_status__in=[
                    EnrollmentStatus.ACTIVE,
                    EnrollmentStatus.ENROLLED,
                ]
            ),
            model=Student,
            user=user,
        )
        .select_related("prospect", "prospect__contact")
        .annotate(check_in_anchor_dt=Subquery(intro_enrollment_subquery))
        .exclude(check_in_anchor_dt__isnull=True)
    )

    students = list(visible_students)
    if not students:
        return {
            "anchor_description": CHECK_IN_ANCHOR_DESCRIPTION,
            "due_today": [],
            "overdue": [],
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
        anchor_dt = student.check_in_anchor_dt
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
    }


def get_home_dashboard_data(*, user):
    today = timezone.localdate()
    now = timezone.now()
    month_starts = _last_n_month_starts(today, months=6)
    month_labels = [month.strftime("%b %Y") for month in month_starts]
    month_start_floor = month_starts[0]

    prospects = get_user_scoped_prospect_queryset(user)

    teacher_profile = None
    if user.is_authenticated and not user.is_staff and not user.is_superuser:
        teacher_profile = getattr(user, "teacher_profile", None)

    # 1) Prospect funnel
    funnel_labels = ["New", "Contacted", "Qualified", "Converted", "Inactive"]
    funnel_values = [
        prospects.filter(status=ProspectStatus.NEW).count(),
        prospects.filter(status=ProspectStatus.CONTACTED).count(),
        prospects.filter(status=ProspectStatus.QUALIFIED).count(),
        prospects.filter(status=ProspectStatus.CONVERTED).count(),
        prospects.filter(status=ProspectStatus.INACTIVE).count(),
    ]

    # 2) Conversion trend
    lead_rows = (
        prospects.filter(created_at__date__gte=month_start_floor)
        .annotate(month=TruncMonth("created_at"))
        .values("month")
        .annotate(total=Count("id"))
        .order_by("month")
    )
    conversion_rows = (
        Student.objects.filter(
            prospect__in=prospects,
            created_at__date__gte=month_start_floor,
        )
        .annotate(month=TruncMonth("created_at"))
        .values("month")
        .annotate(total=Count("id"))
        .order_by("month")
    )
    lead_map = _month_series_map(lead_rows, "total")
    conversion_map = _month_series_map(conversion_rows, "total")
    lead_trend = [lead_map.get(month, 0) for month in month_starts]
    conversion_trend = [conversion_map.get(month, 0) for month in month_starts]

    # 3) Follow-up health (owner-scoped)
    inquiry_scope = Inquiry.objects.filter(prospect__in=prospects)

    stale_cutoff = now - timedelta(days=3)
    follow_ups_logged_week = Communication.objects.filter(
        prospect__in=prospects,
        recipient_type=RecipientType.PROSPECT,
        communication_type=CommunicationType.FOLLOW_UP,
        sent_at__gte=now - timedelta(days=7),
    ).count()
    open_inquiries = inquiry_scope.filter(status=InquiryStatus.OPEN).count()
    in_progress_inquiries = inquiry_scope.filter(status=InquiryStatus.IN_PROGRESS).count()
    stale_inquiries = inquiry_scope.filter(
        status__in=[InquiryStatus.OPEN, InquiryStatus.IN_PROGRESS],
        inquiry_date__lt=stale_cutoff,
    ).count()

    # 4) Inquiry response speed + SLA
    first_response_subquery = (
        Communication.objects.filter(
            prospect=OuterRef("prospect"),
            recipient_type=RecipientType.PROSPECT,
            sent_at__isnull=False,
            sent_at__gte=OuterRef("inquiry_date"),
        )
        .order_by("sent_at")
        .values("sent_at")[:1]
    )
    response_rows = inquiry_scope.annotate(
        first_response_at=Subquery(first_response_subquery)
    ).values("inquiry_date", "first_response_at")
    response_hours = []
    for row in response_rows:
        first_response_at = row.get("first_response_at")
        inquiry_date = row.get("inquiry_date")
        if not first_response_at or not inquiry_date:
            continue
        delta_seconds = (first_response_at - inquiry_date).total_seconds()
        if delta_seconds >= 0:
            response_hours.append(delta_seconds / 3600)
    responded_count = len(response_hours)
    avg_response_hours = round(sum(response_hours) / responded_count, 2) if responded_count else 0
    within_sla_count = len([value for value in response_hours if value <= 24])
    sla_percent = round((within_sla_count / responded_count) * 100, 1) if responded_count else 0
    unresponded_count = max(inquiry_scope.count() - responded_count, 0)

    # 5) Upcoming sessions and capacity
    sessions_scope = CourseSession.objects.filter(
        start_date__date__gte=today,
        start_date__date__lte=today + timedelta(days=14),
    )
    if teacher_profile:
        sessions_scope = sessions_scope.filter(teacher=teacher_profile)
    elif not (user.is_staff or user.is_superuser):
        sessions_scope = sessions_scope.filter(
            enrollments__student__prospect__in=prospects
        ).distinct()

    upcoming_rows = (
        sessions_scope.annotate(enrolled_count=Count("enrollments", distinct=True))
        .select_related("course")
        .order_by("start_date")[:8]
    )
    session_labels = []
    capacity_data = []
    enrolled_data = []
    for session in upcoming_rows:
        session_labels.append(f"{session.start_date:%b %d} · {session.session_name or session.course.name}")
        capacity_data.append(session.capacity or 0)
        enrolled_data.append(session.enrolled_count or 0)

    # 6) Revenue snapshot + aging
    invoice_scope = Invoice.objects.filter(
        enrollment__student__prospect__in=prospects
    ).distinct()
    if teacher_profile:
        invoice_scope = invoice_scope.filter(enrollment__session__teacher=teacher_profile)

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

    overdue_total = invoice_scope.filter(
        due_date__lt=today,
        outstanding__gt=0,
    ).aggregate(total=Coalesce(Sum("outstanding"), Value(Decimal("0.00"))))["total"]

    bucket_0_30 = Decimal("0.00")
    bucket_31_60 = Decimal("0.00")
    bucket_61_plus = Decimal("0.00")
    for invoice in invoice_scope.filter(outstanding__gt=0):
        if not invoice.due_date:
            continue
        days_overdue = (today - invoice.due_date).days
        if days_overdue <= 0:
            continue
        if days_overdue <= 30:
            bucket_0_30 += invoice.outstanding
        elif days_overdue <= 60:
            bucket_31_60 += invoice.outstanding
        else:
            bucket_61_plus += invoice.outstanding

    # 7) Disbursement status and trend
    disbursement_scope = Disbursement.objects.exclude(status=DisbursementStatus.CANCELLED)
    if teacher_profile:
        disbursement_scope = disbursement_scope.filter(teacher=teacher_profile)
    elif not (user.is_staff or user.is_superuser):
        disbursement_scope = disbursement_scope.filter(
            enrollment__student__prospect__in=prospects
        ).distinct()

    disbursement_status_values = [
        disbursement_scope.filter(status=DisbursementStatus.PENDING).count(),
        disbursement_scope.filter(status=DisbursementStatus.APPROVED).count(),
        disbursement_scope.filter(status=DisbursementStatus.PAID).count(),
    ]
    disbursement_month_rows = (
        disbursement_scope.filter(disbursement_date__gte=month_start_floor)
        .annotate(month=TruncMonth("disbursement_date"))
        .values("month")
        .annotate(total=Coalesce(Sum("balance_due_snapshot"), Value(Decimal("0.00"))))
        .order_by("month")
    )
    disbursement_month_map = _month_series_map(disbursement_month_rows, "total")
    disbursement_trend = [_to_float(disbursement_month_map.get(month, Decimal("0.00"))) for month in month_starts]

    # 8) Top source performance
    source_rows = (
        prospects.values("source")
        .annotate(
            total=Count("id"),
            converted=Count("id", filter=Q(student_record__isnull=False)),
        )
        .order_by("-total")[:8]
    )
    source_labels = []
    source_counts = []
    source_conversion_rates = []
    for row in source_rows:
        source = (row.get("source") or "").strip() or "Unknown"
        total = row.get("total", 0) or 0
        converted = row.get("converted", 0) or 0
        rate = round((converted / total) * 100, 1) if total else 0
        source_labels.append(source)
        source_counts.append(total)
        source_conversion_rates.append(rate)

    # 9) Dashboard module previews (first 5 each, owner-scoped)
    student_preview_qs = (
        scope_queryset_for_user(
            queryset=Student.objects.all(),
            model=Student,
            user=user,
        )
        .select_related("prospect", "teacher")
        .order_by("-created_at")[:5]
    )
    inquiry_preview_qs = (
        scope_queryset_for_user(
            queryset=Inquiry.objects.all(),
            model=Inquiry,
            user=user,
        )
        .select_related("prospect", "student__prospect")
        .order_by("-inquiry_date", "-created_at")[:5]
    )
    prospect_preview_qs = (
        prospects.select_related("owner").order_by("-created_at")[:5]
    )
    check_in_reminders = _build_student_check_in_reminders(user=user, today=today)

    return {
        "kpis": {
            "total_prospects": prospects.count(),
            "active_students": Student.objects.filter(
                prospect__in=prospects
            ).exclude(enrollment_status="inactive").count(),
            "open_inquiries": open_inquiries + in_progress_inquiries,
            "outstanding_amount": _to_float(outstanding_total),
            "invoiced_total": _to_float(invoiced_total),
            "collected_total": _to_float(confirmed_collected),
            "overdue_total": _to_float(overdue_total),
            "avg_response_hours": avg_response_hours,
            "sla_percent": sla_percent,
        },
        "follow_up_health": {
            "open_inquiries": open_inquiries,
            "in_progress_inquiries": in_progress_inquiries,
            "stale_inquiries": stale_inquiries,
            "follow_ups_logged_week": follow_ups_logged_week,
        },
        "response_speed": {
            "responded_count": responded_count,
            "unresponded_count": unresponded_count,
            "within_sla_count": within_sla_count,
            "outside_sla_count": max(responded_count - within_sla_count, 0),
        },
        "charts": {
            "funnel": {
                "labels": funnel_labels,
                "values": funnel_values,
            },
            "conversion_trend": {
                "labels": month_labels,
                "leads": lead_trend,
                "converted": conversion_trend,
            },
            "follow_up_health": {
                "labels": ["Open", "In Progress", "Stale", "Follow-ups (7d)"],
                "values": [
                    open_inquiries,
                    in_progress_inquiries,
                    stale_inquiries,
                    follow_ups_logged_week,
                ],
            },
            "response_sla": {
                "labels": ["Within 24h", "After 24h", "No Response"],
                "values": [
                    within_sla_count,
                    max(responded_count - within_sla_count, 0),
                    unresponded_count,
                ],
            },
            "sessions_capacity": {
                "labels": session_labels,
                "capacity": capacity_data,
                "enrolled": enrolled_data,
            },
            "revenue_aging": {
                "labels": ["0-30 Days", "31-60 Days", "61+ Days"],
                "values": [
                    _to_float(bucket_0_30),
                    _to_float(bucket_31_60),
                    _to_float(bucket_61_plus),
                ],
            },
            "disbursement_status": {
                "labels": ["Pending", "Approved", "Paid"],
                "values": disbursement_status_values,
            },
            "disbursement_trend": {
                "labels": month_labels,
                "values": disbursement_trend,
            },
            "source_performance": {
                "labels": source_labels,
                "totals": source_counts,
                "conversion_rates": source_conversion_rates,
            },
        },
        "show_disbursement_block": bool(teacher_profile or user.is_staff or user.is_superuser),
        "previews": {
            "prospects": prospect_preview_qs,
            "students": student_preview_qs,
            "inquiries": inquiry_preview_qs,
        },
        "check_in_reminders": check_in_reminders,
    }
