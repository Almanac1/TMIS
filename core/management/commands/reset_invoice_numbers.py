from __future__ import annotations

import re
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Invoice


class Command(BaseCommand):
    help = "Reset invoice numbers to official course-based format for fictitious/demo data."

    COURSE_CODE_ALIASES = {
        "TMa": (
            "tm adult",
            "adult tm",
            "tm - adult",
            "tma",
            "tm introductory program",
            "tm core 4 day course",
            "tm core 4-day course",
        ),
        "TMc": ("tm couple", "couple tm", "tm - couple", "tmc"),
        "TMf": ("tm family", "family tm", "tm - family", "tmf"),
        "TMs": ("tm student", "student tm", "tm - student", "tms"),
        "TMwow": ("tm word of wisdom", "word of wisdom", "tm - word of wisdom", "tmwow"),
        "AT": (
            "advanced technique",
            "advanced technique i",
            "advanced technique 1",
            "advanced technique ii",
            "advanced technique 2",
            "advanced technique 3",
            "advanced technique 4",
            "at",
        ),
        "AL": ("tm-sidhi", "tm-sidhi course", "sidhi", "al"),
        "Kc": ("knowledge course", "knowledge courses", "kc"),
    }

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Preview only; do not write.")

    @staticmethod
    def _normalize(value: str) -> str:
        lowered = (value or "").strip().lower()
        lowered = lowered.replace("_", " ").replace("-", " ")
        return re.sub(r"\s+", " ", lowered)

    def _resolve_code(self, course) -> str | None:
        raw_code = (getattr(course, "code", "") or "").strip()
        if raw_code in self.COURSE_CODE_ALIASES:
            return raw_code
        normalized_name = self._normalize(course.name)
        normalized_code = self._normalize(raw_code)
        for official_code, aliases in self.COURSE_CODE_ALIASES.items():
            normalized_aliases = {self._normalize(alias) for alias in aliases}
            if normalized_name in normalized_aliases or normalized_code in normalized_aliases:
                return official_code
        return None

    def handle(self, *args, **options):
        dry_run = bool(options.get("dry_run"))
        invoices = list(
            Invoice.objects.select_related("enrollment__session__course").order_by("issue_date", "id")
        )
        if not invoices:
            self.stdout.write(self.style.WARNING("No invoices found."))
            return

        seq = defaultdict(int)
        renumber_plan = []
        missing = []
        changed_count = 0

        for invoice in invoices:
            course = invoice.enrollment.session.course
            code = self._resolve_code(course)
            if not code:
                missing.append(
                    f"Invoice #{invoice.pk} ({invoice.invoice_number}) has unmapped course "
                    f"'{course.name}' with code '{getattr(course, 'code', '') or '-'}'."
                )
                continue

            year = invoice.issue_date.year if invoice.issue_date else 2026
            key = (code, year)
            seq[key] += 1
            new_number = f"{code}-{year}-{seq[key]:04d}"
            renumber_plan.append((invoice, new_number))
            if invoice.invoice_number != new_number:
                changed_count += 1

        self.stdout.write(f"Dry-run: {'yes' if dry_run else 'no'}")
        self.stdout.write(f"Invoices scanned: {len(invoices)}")
        self.stdout.write(f"Invoices to renumber: {len(renumber_plan)}")
        self.stdout.write(f"Invoices requiring changes: {changed_count}")
        self.stdout.write(f"Unmapped invoices: {len(missing)}")
        self.stdout.write("Sample changes:")
        for invoice, new_number in renumber_plan[:15]:
            self.stdout.write(f"- {invoice.invoice_number} -> {new_number}")
        if missing:
            self.stdout.write(self.style.WARNING("Warnings:"))
            for line in missing[:30]:
                self.stdout.write(f"- {line}")
            if len(missing) > 30:
                self.stdout.write(f"- ... and {len(missing) - 30} more")

        if dry_run:
            self.stdout.write(self.style.SUCCESS("Dry-run complete. No changes applied."))
            return

        with transaction.atomic():
            for invoice, new_number in renumber_plan:
                if invoice.invoice_number == new_number:
                    continue
                invoice.invoice_number = new_number
                invoice.save(update_fields=["invoice_number", "updated_at"])
        self.stdout.write(self.style.SUCCESS(f"Updated {changed_count} invoices."))
