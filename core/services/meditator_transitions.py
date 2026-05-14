from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import (
    Communication,
    CommunicationType,
    Course,
    CourseSession,
    Enrollment,
    EnrollmentStatus,
    Location,
    Meditator,
    MeditatorTransitionEvent,
    MeditatorTransitionEventType,
    MeditatorTransitionTrigger,
    RecipientType,
    SessionStatus,
    Student,
    Teacher,
)

INTRO_COURSE_NAME = "TM Introductory Program"
CHECK_IN_DAY_OFFSETS = (3, 10, 20)


@dataclass(frozen=True)
class MeditatorEligibility:
    eligible: bool
    anchor_date: object | None
    intro_completed_on: object | None
    day20_completed_on: object | None
    missing_reasons: tuple[str, ...]


def _to_local_date(value):
    if value is None:
        return None
    return value.date() if hasattr(value, "date") else value


def _is_check_in_completed_for_window(sent_dates, due_date, next_due_date=None):
    for sent_date in sent_dates:
        if sent_date < due_date:
            continue
        if next_due_date and sent_date >= next_due_date:
            continue
        return True
    return False


def _first_completed_date_for_window(sent_dates, due_date, next_due_date=None):
    candidates = []
    for sent_date in sent_dates:
        if sent_date < due_date:
            continue
        if next_due_date and sent_date >= next_due_date:
            continue
        candidates.append(sent_date)
    return min(candidates) if candidates else None


def _get_intro_completed_enrollment(student: Student):
    return (
        Enrollment.objects.filter(
            student=student,
            session__course__name=INTRO_COURSE_NAME,
            status=EnrollmentStatus.COMPLETED,
        )
        .select_related("session", "session__course")
        .order_by("enrollment_date", "pk")
        .first()
    )


def _get_intro_anchor_date(student: Student):
    intro_enrollment = (
        Enrollment.objects.filter(
            student=student,
            session__course__name=INTRO_COURSE_NAME,
        )
        .order_by("enrollment_date", "pk")
        .values_list("enrollment_date", flat=True)
        .first()
    )
    return _to_local_date(intro_enrollment)


def _get_student_follow_up_dates(student: Student):
    follow_up_rows = (
        Communication.objects.filter(
            student=student,
            recipient_type=RecipientType.STUDENT,
            communication_type=CommunicationType.FOLLOW_UP,
            sent_at__isnull=False,
        )
        .order_by("sent_at")
        .values_list("sent_at", flat=True)
    )
    return [_to_local_date(sent_at) for sent_at in follow_up_rows]


def evaluate_student_meditator_eligibility(student: Student) -> MeditatorEligibility:
    intro_completed = _get_intro_completed_enrollment(student)
    if not intro_completed:
        return MeditatorEligibility(
            eligible=False,
            anchor_date=_get_intro_anchor_date(student),
            intro_completed_on=None,
            day20_completed_on=None,
            missing_reasons=("intro_not_completed",),
        )

    # Explicit business anchor date: earliest Introductory Program enrollment date.
    anchor_date = _get_intro_anchor_date(student)
    if not anchor_date:
        return MeditatorEligibility(
            eligible=False,
            anchor_date=None,
            intro_completed_on=_to_local_date(intro_completed.enrollment_date),
            day20_completed_on=None,
            missing_reasons=("intro_anchor_missing",),
        )

    sent_dates = _get_student_follow_up_dates(student)
    due_dates = {day: anchor_date + timedelta(days=day) for day in CHECK_IN_DAY_OFFSETS}
    next_day_map = {3: due_dates[10], 10: due_dates[20], 20: None}

    missing = []
    for day in CHECK_IN_DAY_OFFSETS:
        due_date = due_dates[day]
        completed = _is_check_in_completed_for_window(sent_dates, due_date, next_day_map[day])
        if not completed:
            missing.append(f"day_{day}_check_in_missing")

    if missing:
        return MeditatorEligibility(
            eligible=False,
            anchor_date=anchor_date,
            intro_completed_on=_to_local_date(intro_completed.enrollment_date),
            day20_completed_on=None,
            missing_reasons=tuple(missing),
        )

    day20_completed_on = _first_completed_date_for_window(
        sent_dates,
        due_dates[20],
        None,
    )
    return MeditatorEligibility(
        eligible=True,
        anchor_date=anchor_date,
        intro_completed_on=_to_local_date(intro_completed.enrollment_date),
        day20_completed_on=day20_completed_on,
        missing_reasons=(),
    )


def ensure_meditator_transition_for_student(student: Student) -> Meditator | None:
    if not student or not student.pk:
        return None

    eligibility = evaluate_student_meditator_eligibility(student)
    if not eligibility.eligible:
        return None

    intro_dt = eligibility.intro_completed_on
    day20_dt = eligibility.day20_completed_on
    transitioned_at_date = max(d for d in [intro_dt, day20_dt] if d is not None)
    transitioned_at = timezone.make_aware(datetime.combine(transitioned_at_date, time(9, 0)))

    metadata = {
        "anchor_date": str(eligibility.anchor_date) if eligibility.anchor_date else None,
        "rule": "intro_completed_and_day3_day10_day20_checkins_completed",
    }

    with transaction.atomic():
        meditator, created = Meditator.objects.get_or_create(
            student=student,
            defaults={
                "transitioned_at": transitioned_at,
                "transition_trigger": MeditatorTransitionTrigger.INTRO_AND_DAY20_COMPLETED,
                "intro_completed_on": intro_dt,
                "day20_completed_on": day20_dt,
                "metadata": metadata,
            },
        )

        if not created:
            fields_to_update = []
            if meditator.intro_completed_on != intro_dt:
                meditator.intro_completed_on = intro_dt
                fields_to_update.append("intro_completed_on")
            if meditator.day20_completed_on != day20_dt:
                meditator.day20_completed_on = day20_dt
                fields_to_update.append("day20_completed_on")
            if meditator.transitioned_at != transitioned_at:
                meditator.transitioned_at = transitioned_at
                fields_to_update.append("transitioned_at")
            if meditator.metadata != metadata:
                meditator.metadata = metadata
                fields_to_update.append("metadata")
            if fields_to_update:
                meditator.save(update_fields=fields_to_update + ["updated_at"])

        MeditatorTransitionEvent.objects.get_or_create(
            student=student,
            event_type=MeditatorTransitionEventType.TRANSITIONED,
            transition_trigger=MeditatorTransitionTrigger.INTRO_AND_DAY20_COMPLETED,
            defaults={
                "meditator": meditator,
                "triggered_at": transitioned_at,
                "intro_completed_on": intro_dt,
                "day20_completed_on": day20_dt,
                "metadata": metadata,
            },
        )

    return meditator


def _is_fictitious_student(student: Student) -> bool:
    email = (student.email or "").lower()
    owner_username = (
        student.owner.username.lower() if getattr(student, "owner", None) else ""
    )
    return (
        email.endswith("@example.com")
        or owner_username.startswith("teacher_user_")
        or owner_username.startswith("demo_owner_")
    )


def _get_or_create_intro_enrollment_for_student(student: Student, anchor_dt):
    intro_course = Course.objects.filter(name=INTRO_COURSE_NAME).first()
    if not intro_course:
        intro_course = Course.objects.create(
            name=INTRO_COURSE_NAME,
            description="Auto-created intro course for meditator transition backfill.",
        )

    existing_intro_enrollment = (
        Enrollment.objects.filter(
            student=student,
            session__course=intro_course,
        )
        .select_related("session")
        .order_by("enrollment_date", "pk")
        .first()
    )
    if existing_intro_enrollment:
        if existing_intro_enrollment.status != EnrollmentStatus.COMPLETED:
            existing_intro_enrollment.status = EnrollmentStatus.COMPLETED
            existing_intro_enrollment.save(update_fields=["status", "updated_at"])
        if existing_intro_enrollment.enrollment_date > anchor_dt:
            existing_intro_enrollment.enrollment_date = anchor_dt
            existing_intro_enrollment.save(update_fields=["enrollment_date", "updated_at"])
        return existing_intro_enrollment

    teacher = Teacher.objects.order_by("id").first()
    location = Location.objects.order_by("id").first()
    if not teacher or not location:
        return None

    intro_session = CourseSession.objects.create(
        owner=student.owner,
        course=intro_course,
        teacher=teacher,
        session_name=f"Auto Intro Session for Student {student.pk}",
        start_date=anchor_dt,
        end_date=anchor_dt + timedelta(hours=2),
        location=location,
        delivery_mode=intro_course.format,
        status=SessionStatus.COMPLETED,
    )
    return Enrollment.objects.create(
        student=student,
        session=intro_session,
        enrollment_date=anchor_dt,
        status=EnrollmentStatus.COMPLETED,
        fee_amount=intro_course.standard_fee,
        discount_amount=0,
        notes="Auto-created for fictitious meditator transition backfill.",
    )


def _ensure_check_in_follow_ups(student: Student, anchor_date):
    follow_up_dates = {
        day: anchor_date + timedelta(days=day + 1)
        for day in CHECK_IN_DAY_OFFSETS
    }
    for day, follow_up_date in follow_up_dates.items():
        start_dt = timezone.make_aware(datetime.combine(follow_up_date, time.min))
        end_dt = timezone.make_aware(datetime.combine(follow_up_date, time.max))

        exists = Communication.objects.filter(
            student=student,
            recipient_type=RecipientType.STUDENT,
            communication_type=CommunicationType.FOLLOW_UP,
            sent_at__gte=start_dt,
            sent_at__lte=end_dt,
        ).exists()
        if exists:
            continue

        sent_at = timezone.make_aware(datetime.combine(follow_up_date, time(10, 0)))
        Communication.objects.create(
            owner=student.owner,
            recipient_type=RecipientType.STUDENT,
            student=student,
            channel="email",
            communication_type=CommunicationType.FOLLOW_UP,
            subject=f"Day {day} Check-in",
            body="Auto-generated fictitious check-in for meditator transition backfill.",
            sent_at=sent_at,
            delivery_status="sent",
            provider_status="simulated",
            notes="Auto-generated for fictitious transition backfill.",
        )


def backfill_fictitious_meditator_transitions(target_ratio: float = 0.30) -> dict:
    students = list(
        Student.objects.select_related("owner", "prospect", "prospect__contact")
        .filter(
            Q(prospect__contact__email__iendswith="@example.com")
            | Q(owner__username__startswith="teacher_user_")
            | Q(owner__username__startswith="demo_owner_")
        )
        .order_by("pk")
    )

    if not students:
        return {
            "fictitious_students": 0,
            "already_eligible": 0,
            "transitioned": 0,
            "promoted_for_target": 0,
            "target_ratio": target_ratio,
        }

    transitioned = 0
    already_eligible = 0
    for student in students:
        eligibility = evaluate_student_meditator_eligibility(student)
        if eligibility.eligible:
            already_eligible += 1
            if ensure_meditator_transition_for_student(student):
                transitioned += 1

    minimum_target = max(1, int(round(len(students) * target_ratio)))
    current_meditators = Meditator.objects.filter(student__in=students).count()
    promoted_for_target = 0

    if current_meditators < minimum_target:
        needed = minimum_target - current_meditators
        candidates = [
            student
            for student in students
            if not hasattr(student, "meditator_profile")
        ]
        now = timezone.now()

        for offset, student in enumerate(candidates):
            if needed <= 0:
                break
            anchor_dt = now - timedelta(days=50 + (offset * 2))
            enrollment = _get_or_create_intro_enrollment_for_student(student, anchor_dt)
            if not enrollment:
                continue

            anchor_date = _to_local_date(enrollment.enrollment_date)
            _ensure_check_in_follow_ups(student, anchor_date)
            meditator = ensure_meditator_transition_for_student(student)
            if meditator:
                promoted_for_target += 1
                needed -= 1

    return {
        "fictitious_students": len(students),
        "already_eligible": already_eligible,
        "transitioned": transitioned,
        "promoted_for_target": promoted_for_target,
        "target_ratio": target_ratio,
    }
