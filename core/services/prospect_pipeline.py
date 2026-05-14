from django.db.models import Count, Q
from django.utils import timezone

from core.models import (
    Communication,
    CommunicationType,
    DeliveryStatus,
    Inquiry,
    InquiryStatus,
    Prospect,
    ProspectStatus,
    RecipientType,
)


def get_user_scoped_prospect_queryset(user, *, include_archived: bool = False):
    """
    Return prospects visible to a specific account user.

    Scope rules:
    - Staff/superusers can view all prospects.
    - Product users can view only prospects they own.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return Prospect.objects.none()

    if user.is_staff or user.is_superuser:
        queryset = Prospect.objects.all()
    else:
        queryset = Prospect.objects.filter(owner=user)

    if not include_archived:
        queryset = queryset.filter(is_archived=False).exclude(
            Q(converted_to_student=True)
            | Q(converted_student__isnull=False)
            | Q(status=ProspectStatus.CONVERTED)
        )
    return queryset


def get_prospect_pipeline_queryset(
    *, user, q="", status="", interest_level="", preferred_contact_method="", converted=""
):
    """
    Return prospect pipeline queryset filtered for product UI.
    """
    queryset = get_user_scoped_prospect_queryset(user)

    if q:
        queryset = queryset.filter(
            Q(contact__first_name__icontains=q)
            | Q(contact__last_name__icontains=q)
            | Q(contact__email__icontains=q)
            | Q(contact__phone_number__icontains=q)
            | Q(source__icontains=q)
        )

    if status:
        queryset = queryset.filter(status=status)

    if interest_level:
        queryset = queryset.filter(interest_level=interest_level)

    if preferred_contact_method:
        queryset = queryset.filter(preferred_contact_method=preferred_contact_method)

    if converted == "yes":
        queryset = queryset.filter(student_record__isnull=False)
    elif converted == "no":
        queryset = queryset.filter(student_record__isnull=True)

    return queryset


def get_prospect_dashboard_metrics(*, user):
    """Top-level summary metrics for prospect dashboard."""
    prospects = get_user_scoped_prospect_queryset(user)
    return {
        "total_prospects": prospects.count(),
        "new_prospects": prospects.filter(status=ProspectStatus.NEW).count(),
        "qualified_prospects": prospects.filter(status=ProspectStatus.QUALIFIED).count(),
        "converted_prospects": prospects.filter(student_record__isnull=False).count(),
        "open_inquiries": Inquiry.objects.filter(
            prospect__in=prospects,
            status__in=[InquiryStatus.OPEN, InquiryStatus.IN_PROGRESS],
        ).count(),
    }


def get_pipeline_status_breakdown(*, user):
    """Breakdown of prospect counts by status."""
    rows = (
        get_user_scoped_prospect_queryset(user)
        .values("status")
        .annotate(total=Count("id"))
        .order_by("status")
    )
    return [
        {
            "label": ProspectStatus(row["status"]).label if row["status"] else "Unknown",
            "value": row["total"],
        }
        for row in rows
    ]


def get_prospect_detail_context(prospect: Prospect):
    """Load prospect timeline information in one place for detail page."""
    inquiries = prospect.inquiries.select_related("assigned_to").all()
    communications = prospect.communications.select_related("enrollment").all()
    student = getattr(prospect, "student_record", None)

    return {
        "inquiries": inquiries,
        "communications": communications,
        "student": student,
        "is_converted": student is not None,
    }


def convert_prospect_to_student_for_pipeline(prospect: Prospect):
    """
    Convert prospect to student idempotently for product workflow.
    Also updates prospect status to converted.
    """
    student, created = prospect.convert_to_student()
    student_changed = False
    if student.owner_id != prospect.owner_id:
        student.owner = prospect.owner
        student_changed = True
    if student.teacher_id != prospect.teacher_id:
        student.teacher = prospect.teacher
        student_changed = True
    if (prospect.notes or "").strip() and not (student.notes or "").strip():
        student.notes = prospect.notes
        student_changed = True
    if student_changed:
        student.save()
    return student, created


def log_prospect_follow_up(*, prospect: Prospect, channel: str, subject: str, body: str):
    """Create a follow-up communication log entry for a prospect."""
    return Communication.objects.create(
        owner=prospect.owner,
        recipient_type=RecipientType.PROSPECT,
        prospect=prospect,
        channel=channel,
        communication_type=CommunicationType.FOLLOW_UP,
        subject=subject,
        body=body,
        sent_at=timezone.now(),
        delivery_status=DeliveryStatus.SENT,
    )
