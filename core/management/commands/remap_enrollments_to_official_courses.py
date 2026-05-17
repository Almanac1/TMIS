from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Course, Enrollment, EnrollmentStatus, Invoice


@dataclass(frozen=True)
class DistributionRule:
    code: str
    weight: int


class Command(BaseCommand):
    help = "Remap legacy demo enrollments to the official MFG 2026 course catalog."

    TM_DISTRIBUTION = (
        DistributionRule("TM-AD", 50),
        DistributionRule("TM-CP", 20),
        DistributionRule("TM-ST", 15),
        DistributionRule("TM-FM", 10),
        DistributionRule("TM-WW", 5),
    )
    AT_DISTRIBUTION = (
        DistributionRule("AT-ST", 75),
        DistributionRule("AT-CP", 25),
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Preview only.")

    @staticmethod
    def _cycle_pick(index: int, distribution: tuple[DistributionRule, ...]) -> str:
        total = sum(item.weight for item in distribution)
        marker = index % total
        running = 0
        for item in distribution:
            running += item.weight
            if marker < running:
                return item.code
        return distribution[0].code

    @staticmethod
    def _normalize(value: str) -> str:
        return (value or "").strip().lower().replace("_", " ")

    def _resolve_target_code(self, course_name: str, session_name: str, tm_index: int, at_index: int):
        n_course = self._normalize(course_name)
        n_session = self._normalize(session_name)
        bucket = f"{n_course} {n_session}"

        if any(token in bucket for token in ("advanced technique i", "advanced technique ii", "advanced technique 1", "advanced technique 2")):
            return self._cycle_pick(at_index, self.AT_DISTRIBUTION), tm_index, at_index + 1
        if "advanced technique" in bucket:
            return self._cycle_pick(at_index, self.AT_DISTRIBUTION), tm_index, at_index + 1
        if "sidhi" in bucket or "tm-sidhi" in bucket:
            return "SID", tm_index, at_index
        if "knowledge course" in bucket or "knowledge courses" in bucket:
            return "KC", tm_index, at_index
        if "tm core 4-day course" in bucket or "tm introductory program" in bucket or "tm intro" in bucket:
            return self._cycle_pick(tm_index, self.TM_DISTRIBUTION), tm_index + 1, at_index
        if "tm " in bucket:
            return self._cycle_pick(tm_index, self.TM_DISTRIBUTION), tm_index + 1, at_index
        return None, tm_index, at_index

    def handle(self, *args, **options):
        dry_run = bool(options.get("dry_run"))
        official_courses = {
            course.code: course
            for course in Course.objects.filter(is_active=True, code__isnull=False).exclude(code="")
        }

        required_codes = {"TM-AD", "TM-CP", "TM-FM", "TM-ST", "TM-WW", "AT-ST", "AT-CP", "SID", "KC"}
        missing = sorted(required_codes - set(official_courses.keys()))
        if missing:
            self.stdout.write(self.style.ERROR(f"Missing official active courses: {', '.join(missing)}"))
            return

        enrollments = list(
            Enrollment.objects.select_related("course", "session__course", "student__prospect__contact")
            .order_by("id")
        )
        if not enrollments:
            self.stdout.write(self.style.WARNING("No enrollments found."))
            return

        tm_index = 0
        at_index = 0
        remap_plan = []
        unmapped = []
        invoices_to_regenerate = []

        for enrollment in enrollments:
            session = enrollment.session
            old_course = enrollment.course or session.course
            target_code, tm_index, at_index = self._resolve_target_code(
                old_course.name,
                session.session_name or "",
                tm_index,
                at_index,
            )
            if not target_code:
                unmapped.append(
                    f"Enrollment #{enrollment.pk} | course='{old_course.name}' | session='{session.session_name}'"
                )
                continue
            target_course = official_courses[target_code]
            if enrollment.course_id != target_course.id:
                remap_plan.append((enrollment, old_course, target_course))
            if hasattr(enrollment, "invoice"):
                invoices_to_regenerate.append(enrollment.invoice.pk)

        self.stdout.write(f"Dry-run: {'yes' if dry_run else 'no'}")
        self.stdout.write(f"Enrollments scanned: {len(enrollments)}")
        self.stdout.write(f"Enrollments to remap: {len(remap_plan)}")
        self.stdout.write(f"Invoices to regenerate numbering: {len(set(invoices_to_regenerate))}")
        self.stdout.write(f"Unmapped enrollments: {len(unmapped)}")

        self.stdout.write("Sample remaps:")
        for enrollment, old_course, target_course in remap_plan[:20]:
            self.stdout.write(
                f"- Enrollment #{enrollment.pk}: {old_course.name} -> {target_course.name} ({target_course.code})"
            )
        if unmapped:
            self.stdout.write(self.style.WARNING("Unmapped records:"))
            for line in unmapped[:30]:
                self.stdout.write(f"- {line}")
            if len(unmapped) > 30:
                self.stdout.write(f"- ... and {len(unmapped) - 30} more")

        if dry_run:
            self.stdout.write(self.style.SUCCESS("Dry-run complete. No records changed."))
            return

        with transaction.atomic():
            # Apply enrollment/session remap
            for enrollment, _, target_course in remap_plan:
                enrollment.course = target_course
                enrollment.save(update_fields=["course", "updated_at"])

            # Regenerate invoice numbers in COURSECODE-YEAR-SEQUENCE format
            invoices = list(
                Invoice.objects.select_related("enrollment__course").order_by("issue_date", "id")
            )
            seq = defaultdict(int)
            for invoice in invoices:
                code = (invoice.enrollment.course.code or "GEN").upper()
                year = invoice.issue_date.year if invoice.issue_date else 2026
                key = (code, year)
                seq[key] += 1
                invoice.invoice_number = f"{code}-{year}-{seq[key]:04d}"
                invoice.save(update_fields=["invoice_number", "updated_at"])

            # Enrollment status rule: pending payment while balance > 0, otherwise cleared proxy as ENROLLED.
            for enrollment in Enrollment.objects.select_related("invoice").all():
                try:
                    invoice = enrollment.invoice
                except Invoice.DoesNotExist:
                    enrollment.status = EnrollmentStatus.PENDING
                    enrollment.save(update_fields=["status", "updated_at"])
                    continue
                if invoice.balance_due > 0:
                    if enrollment.status != EnrollmentStatus.PENDING:
                        enrollment.status = EnrollmentStatus.PENDING
                        enrollment.save(update_fields=["status", "updated_at"])
                elif enrollment.status == EnrollmentStatus.PENDING:
                    enrollment.status = EnrollmentStatus.ENROLLED
                    enrollment.save(update_fields=["status", "updated_at"])

        self.stdout.write(
            self.style.SUCCESS(
                f"Remapped {len(remap_plan)} enrollments and regenerated {len(invoices)} invoice numbers."
            )
        )
