from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import CourseSession, Enrollment, Student, StudentGovernorAssignment


class Command(BaseCommand):
    help = "Backfill and normalize student-governor assignment relationships."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Preview only; do not write.")

    def handle(self, *args, **options):
        dry_run = bool(options.get("dry_run"))
        create_plan = []
        duplicates = []

        # 1) Primary governor links
        for student in Student.objects.select_related("teacher").all():
            if not student.teacher_id:
                continue
            exists = StudentGovernorAssignment.objects.filter(
                student=student, teacher=student.teacher, is_primary=True
            ).exists()
            if not exists:
                create_plan.append(
                    (
                        student,
                        student.teacher,
                        None,
                        None,
                        "Primary Governor",
                        "",
                        student.updated_at or timezone.now(),
                        True,
                    )
                )

        # 2) Enrollment-level governor links
        for enrollment in Enrollment.objects.select_related(
            "student", "teacher", "course", "session__teacher", "session__course"
        ).all():
            teacher = enrollment.teacher or enrollment.session.teacher or enrollment.student.teacher
            if not teacher:
                continue
            role = "Enrollment Governor"
            course_name = enrollment.course.name if enrollment.course_id else ""
            exists = StudentGovernorAssignment.objects.filter(
                student=enrollment.student,
                teacher=teacher,
                enrollment=enrollment,
                role=role,
            ).exists()
            if not exists:
                create_plan.append(
                    (
                        enrollment.student,
                        teacher,
                        enrollment,
                        enrollment.session,
                        role,
                        course_name,
                        enrollment.enrollment_date or timezone.now(),
                        bool(enrollment.student.teacher_id == teacher.id),
                    )
                )

        # 3) Session-level links via enrollments
        for session in CourseSession.objects.select_related("teacher", "course").all():
            if not session.teacher_id:
                continue
            for enrollment in session.enrollments.select_related("student").all():
                exists = StudentGovernorAssignment.objects.filter(
                    student=enrollment.student,
                    teacher=session.teacher,
                    session=session,
                    role="Session Governor",
                ).exists()
                if not exists:
                    create_plan.append(
                        (
                            enrollment.student,
                            session.teacher,
                            None,
                            session,
                            "Session Governor",
                            session.course.name if session.course_id else "",
                            enrollment.enrollment_date or timezone.now(),
                            bool(enrollment.student.teacher_id == session.teacher_id),
                        )
                    )

        # duplicate detection by signature
        signature_counts = {}
        for assignment in StudentGovernorAssignment.objects.values(
            "student_id", "teacher_id", "enrollment_id", "session_id", "role"
        ):
            key = (
                assignment["student_id"],
                assignment["teacher_id"],
                assignment["enrollment_id"],
                assignment["session_id"],
                assignment["role"],
            )
            signature_counts[key] = signature_counts.get(key, 0) + 1
        duplicates = [key for key, count in signature_counts.items() if count > 1]

        self.stdout.write(f"Dry-run: {'yes' if dry_run else 'no'}")
        self.stdout.write(f"Assignments to create: {len(create_plan)}")
        self.stdout.write(f"Duplicate signatures detected: {len(duplicates)}")
        self.stdout.write("Sample planned creates:")
        for item in create_plan[:20]:
            student, teacher, enrollment, session, role, _, assigned_date, is_primary = item
            self.stdout.write(
                f"- student={student.pk} teacher={teacher.pk} role={role} "
                f"enrollment={getattr(enrollment, 'pk', None)} session={getattr(session, 'pk', None)} "
                f"assigned={assigned_date.date()} primary={is_primary}"
            )

        if duplicates:
            self.stdout.write(self.style.WARNING("Duplicate assignment signatures:"))
            for key in duplicates[:20]:
                self.stdout.write(f"- {key}")
            if len(duplicates) > 20:
                self.stdout.write(f"- ... and {len(duplicates) - 20} more")

        if dry_run:
            self.stdout.write(self.style.SUCCESS("Dry-run complete. No changes applied."))
            return

        with transaction.atomic():
            for (
                student,
                teacher,
                enrollment,
                session,
                role,
                course_name,
                assigned_date,
                is_primary,
            ) in create_plan:
                StudentGovernorAssignment.objects.get_or_create(
                    student=student,
                    teacher=teacher,
                    enrollment=enrollment,
                    session=session,
                    role=role,
                    defaults={
                        "course": course_name,
                        "assigned_date": assigned_date,
                        "status": "active",
                        "is_primary": is_primary,
                    },
                )

        self.stdout.write(self.style.SUCCESS(f"Created {len(create_plan)} governor assignment records."))
