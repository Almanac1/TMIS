from __future__ import annotations

import re
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import Course, Invoice


class Command(BaseCommand):
    help = "Reset fictitious invoices to official course-based numbering."

    COURSE_CODE_ALIASES = {
        "TM-AD": (
            "tm adult",
            "adult tm",
            "tm - adult",
            "tm-ad",
            "tm introductory program",
            "tm core 4 day course",
            "tm core 4-day course",
        ),
        "TM-CP": (
            "tm couple",
            "couple tm",
            "tm - couple",
            "tm-cp",
        ),
        "TM-FM": (
            "tm family",
            "family tm",
            "tm - family",
            "tm-fm",
        ),
        "TM-ST": (
            "tm student",
            "student tm",
            "tm - student",
            "tm-st",
        ),
        "TM-WW": (
            "tm word of wisdom",
            "word of wisdom",
            "tm - word of wisdom",
            "tm-ww",
        ),
        "AT-ST": (
            "advanced technique",
            "at standard",
            "at-st",
            "advanced technique i",
            "advanced technique 1",
            "advanced technique ii",
            "advanced technique 2",
            "advanced technique 3",
            "advanced technique 4",
        ),
        "AT-CP": (
            "advanced technique couple",
            "couple advanced technique",
            "at-cp",
        ),
        "SID": (
            "tm-sidhi",
            "tm-sidhi course",
            "sid",
            "sidhi",
        ),
        "KC": (
            "knowledge course",
            "knowledge courses",
            "kc",
        ),
    }

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview updates without writing to the database.",
        )

    @staticmethod
    def _normalize(value: str) -> str:
        lowered = (value or "").strip().lower()
        lowered = lowered.replace("_", " ").replace("-", " ")
        lowered = re.sub(r"\s+", " ", lowered)
        return lowered

    def _resolve_target_code(self, course: Course) -> str | None:
        current_code = (course.code or "").strip().upper()
        if current_code in self.COURSE_CODE_ALIASES:
            return current_code

        normalized_name = self._normalize(course.name)
        normalized_code = self._normalize(course.code or "")
        for official_code, aliases in self.COURSE_CODE_ALIASES.items():
            normalized_aliases = {self._normalize(alias) for alias in aliases}
            if normalized_name in normalized_aliases or normalized_code in normalized_aliases:
                return official_code
        return None

    def handle(self, *args, **options):
        dry_run = bool(options.get("dry_run"))
        current_year = timezone.localdate().year
        official_courses = {course.code: course for course in Course.objects.filter(is_active=True)}

        missing_official_codes = sorted(
            set(self.COURSE_CODE_ALIASES.keys()) - set(code for code in official_courses if code)
        )
        if missing_official_codes:
            self.stdout.write(
                self.style.ERROR(
                    "Missing active official course rows for codes: "
                    + ", ".join(missing_official_codes)
                )
            )
            return

        invoices = list(
            Invoice.objects.select_related(
                "enrollment__session__course",
                "enrollment__student__prospect__contact",
            ).order_by("issue_date", "id")
        )
        if not invoices:
            self.stdout.write(self.style.WARNING("No invoices found. Nothing to update."))
            return

        unmapped = []
        renumber_plan = []
        sequence_by_code_year = defaultdict(int)
        touched_sessions = set()
        session_reassignments = 0

        for invoice in invoices:
            session = invoice.enrollment.session
            course = session.course
            target_code = self._resolve_target_code(course)
            if not target_code:
                unmapped.append(
                    f"Invoice #{invoice.pk} ({invoice.invoice_number}) "
                    f"course='{course.name}' code='{course.code or '-'}'"
                )
                continue

            target_course = official_courses.get(target_code)
            if target_course is None:
                unmapped.append(
                    f"Invoice #{invoice.pk} ({invoice.invoice_number}) could not resolve active course for {target_code}"
                )
                continue

            if session.course_id != target_course.id and session.pk not in touched_sessions:
                session_reassignments += 1
                touched_sessions.add(session.pk)
                if not dry_run:
                    session.course = target_course
                    session.save(update_fields=["course", "updated_at"])

            source_year = invoice.issue_date.year if invoice.issue_date else (
                invoice.created_at.year if invoice.created_at else current_year
            )
            key = (target_code, source_year)
            sequence_by_code_year[key] += 1
            new_number = f"{target_code}-{source_year}-{sequence_by_code_year[key]:04d}"
            renumber_plan.append((invoice, new_number))

        preview_count = min(15, len(renumber_plan))
        self.stdout.write(f"Dry-run: {'yes' if dry_run else 'no'}")
        self.stdout.write(f"Invoices scanned: {len(invoices)}")
        self.stdout.write(f"Invoices eligible for update: {len(renumber_plan)}")
        self.stdout.write(f"Sessions to reassign: {session_reassignments}")
        self.stdout.write(f"Unmapped invoices: {len(unmapped)}")

        if renumber_plan:
            self.stdout.write("Sample invoice renumbering (before -> after):")
            for invoice, new_number in renumber_plan[:preview_count]:
                self.stdout.write(f"- {invoice.invoice_number} -> {new_number}")

        if unmapped:
            self.stdout.write(self.style.WARNING("Unmapped invoices (no automatic guess made):"))
            for line in unmapped[:30]:
                self.stdout.write(f"- {line}")
            if len(unmapped) > 30:
                self.stdout.write(f"- ... and {len(unmapped) - 30} more")

        if dry_run:
            self.stdout.write(self.style.SUCCESS("Dry-run complete. No records were changed."))
            return

        with transaction.atomic():
            for invoice, new_number in renumber_plan:
                invoice.invoice_number = new_number
                invoice.save(update_fields=["invoice_number", "updated_at"])

        self.stdout.write(
            self.style.SUCCESS(
                f"Updated {len(renumber_plan)} invoices to course-based numbering."
            )
        )
