from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from core.models import (
    Communication,
    CommunicationChannel,
    CommunicationType,
    Course,
    CourseSession,
    DeliveryStatus,
    Enrollment,
    EnrollmentStatus,
    Location,
    RecipientType,
    SessionStatus,
    Student,
    Teacher,
)
from core.services.home_dashboard import _build_student_check_in_reminders


SESSION_NAME = "Dashboard Check-in Reminder Demo"
ENROLLMENT_MARKER = "CHECKIN_REMINDER_DEMO"


@dataclass(frozen=True)
class Scenario:
    key: str
    label: str
    enrollment_age_days: int
    count: int
    completed_prior_days: tuple[int, ...] = ()


SCENARIOS = (
    Scenario("day_3_due_today", "Day 3 due today", 3, 3),
    Scenario("day_10_due_today", "Day 10 due today", 10, 3, (3,)),
    Scenario("day_20_due_today", "Day 20 due today", 20, 3, (3, 10)),
    Scenario("missed_day_3", "Missed Day 3", 5, 2),
    Scenario("missed_day_10", "Missed Day 10", 12, 2, (3,)),
    Scenario("missed_day_20", "Missed Day 20", 22, 2, (3, 10)),
)


def _aware_at(date_value, hour=9):
    return timezone.make_aware(
        datetime.combine(date_value, time(hour, 0)),
        timezone.get_current_timezone(),
    )


class Command(BaseCommand):
    help = (
        "Seed local demo Introductory Program enrollments so the Home dashboard "
        "shows realistic Day 3, Day 10, and Day 20 reminders."
    )

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview the assignments without changing the database (default).",
        )
        mode.add_argument(
            "--execute",
            action="store_true",
            help="Create/update the local demo session, enrollments, and prerequisite check-ins.",
        )
        parser.add_argument(
            "--owner",
            default="admin",
            help="Username that should own the demo records (default: admin).",
        )

    def _assert_local_development_database(self):
        if settings.DEBUG:
            return
        configured_name = str(connection.settings_dict.get("NAME") or "")
        expected_sqlite = Path(settings.BASE_DIR) / "db.sqlite3"
        if connection.vendor == "sqlite" and Path(configured_name).resolve() == expected_sqlite.resolve():
            return
        raise CommandError(
            "Refusing to seed check-in reminders outside the local development database."
        )

    @staticmethod
    def _scenario_marker(scenario):
        return f"{ENROLLMENT_MARKER}:{scenario.key}"

    def _existing_assignments(self):
        assignments = {}
        seeded = (
            Enrollment.objects.filter(
                session__session_name=SESSION_NAME,
                notes__startswith=f"{ENROLLMENT_MARKER}:",
            )
            .select_related("student__prospect__contact", "session")
            .order_by("pk")
        )
        for enrollment in seeded:
            scenario_key = enrollment.notes.split(":", 1)[-1]
            assignments.setdefault(scenario_key, []).append(enrollment)
        return assignments

    def _candidate_students(self, *, owner, excluded_student_ids, needed):
        candidates = list(
            Student.objects.filter(
                owner=owner,
                enrollment_status__in=(EnrollmentStatus.ACTIVE, EnrollmentStatus.ENROLLED),
            )
            .exclude(pk__in=excluded_student_ids)
            .exclude(enrollments__session__course__name="TM Introductory Program")
            .exclude(
                communications__recipient_type=RecipientType.STUDENT,
                communications__communication_type=CommunicationType.FOLLOW_UP,
                communications__sent_at__isnull=False,
            )
            .select_related("prospect__contact")
            .distinct()
            .order_by("pk")[:needed]
        )
        if len(candidates) < needed:
            raise CommandError(
                f"Only {len(candidates)} suitable demo students are available; {needed} are required."
            )
        return candidates

    def _build_plan(self, *, owner, today):
        existing = self._existing_assignments()
        selected_ids = {
            enrollment.student_id
            for enrollments in existing.values()
            for enrollment in enrollments
        }
        missing_total = sum(
            max(0, scenario.count - len(existing.get(scenario.key, [])))
            for scenario in SCENARIOS
        )
        candidates = iter(
            self._candidate_students(
                owner=owner,
                excluded_student_ids=selected_ids,
                needed=missing_total,
            )
        )

        plan = []
        for scenario in SCENARIOS:
            scenario_enrollments = existing.get(scenario.key, [])[: scenario.count]
            for enrollment in scenario_enrollments:
                plan.append((scenario, enrollment.student, enrollment))
            for _ in range(scenario.count - len(scenario_enrollments)):
                plan.append((scenario, next(candidates), None))
        return plan

    @staticmethod
    def _get_supporting_records(owner):
        course = Course.objects.filter(name="TM Introductory Program").first()
        if course is None:
            raise CommandError("TM Introductory Program course does not exist.")
        teacher = Teacher.objects.filter(status="active").order_by("pk").first()
        location = Location.objects.filter(is_active=True).order_by("pk").first()
        if teacher is None or location is None:
            raise CommandError("An active Teacher and Location are required for the demo session.")
        return course, teacher, location

    def _print_plan(self, *, plan, today, dry_run):
        self.stdout.write(f"Today: {today}")
        self.stdout.write(f"Mode: {'DRY RUN' if dry_run else 'EXECUTE'}")
        for scenario in SCENARIOS:
            self.stdout.write("")
            self.stdout.write(f"{scenario.label}:")
            for planned_scenario, student, enrollment in plan:
                if planned_scenario.key != scenario.key:
                    continue
                target_date = today - timedelta(days=scenario.enrollment_age_days)
                action = "update" if enrollment else "create"
                self.stdout.write(
                    f"- Student #{student.pk} {student.prospect} | "
                    f"enrollment_date={target_date} | {action}"
                )

    def _seed_prior_check_ins(self, *, enrollment, scenario, anchor_date):
        for day in scenario.completed_prior_days:
            marker = f"{ENROLLMENT_MARKER}:{scenario.key}:completed_day_{day}"
            sent_date = anchor_date + timedelta(days=day)
            Communication.objects.update_or_create(
                student=enrollment.student,
                enrollment=enrollment,
                notes=marker,
                defaults={
                    "owner": enrollment.student.owner,
                    "recipient_type": RecipientType.STUDENT,
                    "prospect": None,
                    "channel": CommunicationChannel.EMAIL,
                    "communication_type": CommunicationType.FOLLOW_UP,
                    "subject": f"Demo Day {day} check-in completed",
                    "body": "Local demo check-in created by seed_checkin_reminders.",
                    "sent_at": _aware_at(sent_date, 10),
                    "delivery_status": DeliveryStatus.SENT,
                    "provider_status": "local_demo_seed",
                },
            )

    def _execute_plan(self, *, owner, today, plan):
        course, teacher, location = self._get_supporting_records(owner)
        with transaction.atomic():
            session, _ = CourseSession.objects.get_or_create(
                owner=owner,
                course=course,
                session_name=SESSION_NAME,
                defaults={
                    "teacher": teacher,
                    "location": location,
                    "start_date": _aware_at(today, 9),
                    "end_date": _aware_at(today + timedelta(days=1), 17),
                    "delivery_mode": course.format,
                    "status": SessionStatus.OPEN,
                    "capacity": sum(scenario.count for scenario in SCENARIOS),
                },
            )
            seeded_enrollments = []
            for scenario, student, existing_enrollment in plan:
                anchor_date = today - timedelta(days=scenario.enrollment_age_days)
                if existing_enrollment is None:
                    enrollment = Enrollment.objects.create(
                        student=student,
                        course=course,
                        session=session,
                        enrollment_date=_aware_at(anchor_date),
                        status=EnrollmentStatus.ENROLLED,
                        fee_amount=course.standard_fee or Decimal("0.00"),
                        discount_amount=Decimal("0.00"),
                        notes=self._scenario_marker(scenario),
                    )
                else:
                    enrollment = existing_enrollment
                    enrollment.enrollment_date = _aware_at(anchor_date)
                    enrollment.save(update_fields=["enrollment_date", "updated_at"])
                self._seed_prior_check_ins(
                    enrollment=enrollment,
                    scenario=scenario,
                    anchor_date=anchor_date,
                )
                seeded_enrollments.append(enrollment)
        return seeded_enrollments

    def _verify(self, *, owner, today, plan):
        reminders = _build_student_check_in_reminders(user=owner, today=today)
        planned_ids = {student.pk for _, student, _ in plan}
        due_rows = [row for row in reminders["due_today"] if row["student"].pk in planned_ids]
        overdue_rows = [row for row in reminders["overdue"] if row["student"].pk in planned_ids]
        expected_due = {3: 3, 10: 3, 20: 3}
        expected_overdue = {3: 2, 10: 2, 20: 2}
        actual_due = {
            day: sum(row["day_label"] == f"Day {day}" for row in due_rows)
            for day in expected_due
        }
        actual_overdue = {
            day: sum(row["day_label"] == f"Day {day}" for row in overdue_rows)
            for day in expected_overdue
        }
        duplicate_rows = len(due_rows) + len(overdue_rows) - len(
            {row["student"].pk for row in due_rows + overdue_rows}
        )
        if actual_due != expected_due or actual_overdue != expected_overdue or duplicate_rows:
            raise CommandError(
                "Dashboard verification failed: "
                f"due={actual_due}, overdue={actual_overdue}, duplicate_rows={duplicate_rows}."
            )
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Dashboard verification passed."))
        self.stdout.write(f"- Due Today: {actual_due}")
        self.stdout.write(f"- Missed Check-ins: {actual_overdue}")
        self.stdout.write("- Duplicate reminder students: 0")

    def handle(self, *args, **options):
        self._assert_local_development_database()
        execute = bool(options.get("execute"))
        dry_run = not execute
        owner = get_user_model().objects.filter(username=options["owner"]).first()
        if owner is None:
            raise CommandError(f"Owner '{options['owner']}' does not exist.")

        today = timezone.localdate()
        # Validate supporting data even in dry-run mode.
        self._get_supporting_records(owner)
        plan = self._build_plan(owner=owner, today=today)
        self._print_plan(plan=plan, today=today, dry_run=dry_run)
        if dry_run:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING("Dry run complete. No records changed."))
            self.stdout.write("Run again with --execute to apply this plan.")
            return

        self._execute_plan(owner=owner, today=today, plan=plan)
        refreshed_plan = self._build_plan(owner=owner, today=today)
        self._verify(owner=owner, today=today, plan=refreshed_plan)
