from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from core.currency import format_currency
from core.models import ProspectStatus
from core.services.student_cleanup import (
    audit_student_conversions,
    safely_revert_invalid_student,
)
from core.services.student_integrity import (
    capture_protected_record_snapshot,
    compare_protected_record_snapshots,
    verify_active_student_integrity,
)


class Command(BaseCommand):
    help = (
        "Audit and safely revert Student records that did not satisfy the "
        "Prospect conversion financial requirement. Defaults to dry-run."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report affected records without modifying the database (default).",
        )
        parser.add_argument(
            "--execute",
            action="store_true",
            help="Restore explicitly selected invalid Students to the Prospect stage.",
        )
        parser.add_argument(
            "--verify",
            action="store_true",
            help="Run read-only post-cleanup integrity verification.",
        )
        parser.add_argument(
            "--student-id",
            action="append",
            type=int,
            dest="student_ids",
            help="Student ID to inspect or execute. Repeat for multiple reviewed IDs.",
        )
        parser.add_argument(
            "--confirm-count",
            type=int,
            help="Required with --execute; must equal the selected invalid record count.",
        )
        parser.add_argument(
            "--include-inferred",
            action="store_true",
            help=(
                "Allow execution when conversion time is inferred from Student.created_at. "
                "Without this flag, inferred-time records are never modified."
            ),
        )
        parser.add_argument(
            "--revert-status",
            choices=[
                ProspectStatus.NEW,
                ProspectStatus.CONTACTED,
                ProspectStatus.QUALIFIED,
                ProspectStatus.INACTIVE,
                ProspectStatus.BAD_LEAD,
            ],
            default=ProspectStatus.QUALIFIED,
            help="Prospect status applied after a safe revert (default: qualified).",
        )

    def handle(self, *args, **options):
        selected_modes = sum(
            bool(options[name]) for name in ("dry_run", "execute", "verify")
        )
        if selected_modes > 1:
            raise CommandError(
                "Choose only one of --dry-run, --execute, or --verify."
            )

        if options["verify"]:
            self._write_verification(
                verify_active_student_integrity(),
                fail_on_violation=True,
            )
            return

        execute = bool(options["execute"])
        student_ids = options.get("student_ids") or []
        if execute and not student_ids:
            raise CommandError(
                "--execute requires at least one reviewed --student-id. "
                "Unscoped bulk execution is intentionally disabled."
            )

        audits = audit_student_conversions(student_ids or None)
        if student_ids:
            found_ids = {audit.student_id for audit in audits}
            missing_ids = sorted(set(student_ids) - found_ids)
            if missing_ids:
                raise CommandError(
                    "Student ID(s) not found: " + ", ".join(map(str, missing_ids))
                )

        invalid_audits = [
            audit
            for audit in audits
            if (
                audit.is_conversion_candidate
                and not audit.compliant
                and not audit.restored_to_prospect
            )
        ]
        non_conversion_count = sum(
            not audit.is_conversion_candidate and not audit.restored_to_prospect
            for audit in audits
        )
        restored_count = sum(audit.restored_to_prospect for audit in audits)
        serious_meditator_count = sum(
            audit.has_serious_meditator_integrity_violation
            for audit in invalid_audits
        )
        if execute:
            confirm_count = options.get("confirm_count")
            if confirm_count is None:
                raise CommandError("--execute requires --confirm-count.")
            if confirm_count != len(invalid_audits):
                raise CommandError(
                    "--confirm-count does not match the selected invalid record count "
                    f"({len(invalid_audits)})."
                )
            blocked = []
            for audit in invalid_audits:
                if audit.has_blocking_dependencies:
                    blocked.append(
                        f"Student {audit.student_id}: ambiguous Prospect dependency"
                    )
                elif (
                    audit.conversion_time_source != "prospect.converted_at"
                    and not options["include_inferred"]
                ):
                    blocked.append(
                        f"Student {audit.student_id}: inferred conversion timestamp"
                    )
            if blocked:
                raise CommandError(
                    "Execution preflight failed; no records were modified. "
                    + "; ".join(blocked)
                )

        mode = "EXECUTE" if execute else "DRY RUN"
        self.stdout.write(self.style.MIGRATE_HEADING(f"Mode: {mode}"))
        self.stdout.write(
            "Rule: issued donation statement + confirmed payment in full + "
            "zero outstanding balance at conversion."
        )
        self.stdout.write("")

        execution_results = []
        protected_snapshot_verified = False
        if execute:
            try:
                with transaction.atomic():
                    protected_before = capture_protected_record_snapshot()
                    for audit in invalid_audits:
                        result = self._write_audit(
                            audit,
                            execute=True,
                            options=options,
                        )
                        if result:
                            execution_results.append(result)
                    protected_after = capture_protected_record_snapshot()
                    protected_changes = compare_protected_record_snapshots(
                        protected_before,
                        protected_after,
                    )
                    if protected_changes:
                        raise IntegrityError(
                            "Cleanup changed protected Contact, Prospect, Invoice, "
                            f"or Payment ID sets: {protected_changes}"
                        )
                    protected_snapshot_verified = True
            except IntegrityError as exc:
                raise CommandError(
                    "Cleanup encountered an integrity error. The entire cleanup "
                    "transaction was rolled back; no selected record was partially "
                    "cleaned."
                ) from exc
            for level, message in execution_results:
                style = self.style.SUCCESS if level == "success" else self.style.WARNING
                self.stdout.write(style(message))
            if protected_snapshot_verified:
                self.stdout.write(
                    self.style.SUCCESS(
                        "VERIFIED: Contact, Prospect, donation statement/invoice, "
                        "and payment ID sets are unchanged."
                    )
                )
        else:
            for audit in invalid_audits:
                self._write_audit(audit, execute=False, options=options)

        category_counts = Counter(audit.category for audit in invalid_audits)
        self.stdout.write(self.style.MIGRATE_HEADING("Summary"))
        self.stdout.write(f"Students checked: {len(audits)}")
        self.stdout.write(
            "Compliant conversions: "
            f"{sum(a.compliant and a.is_conversion_candidate for a in audits)}"
        )
        self.stdout.write(f"Not marked as converted: {non_conversion_count}")
        self.stdout.write(f"Already restored to Prospect: {restored_count}")
        self.stdout.write(f"Violating: {len(invalid_audits)}")
        self.stdout.write(
            "SERIOUS Meditator integrity violations: "
            f"{serious_meditator_count}"
        )
        for category in (
            "no_invoice",
            "invoice_after_conversion",
            "unissued_or_invalid_invoice",
            "no_payment",
            "partial_or_outstanding",
        ):
            self.stdout.write(f"{category}: {category_counts[category]}")

        if execute:
            self._write_verification(
                verify_active_student_integrity(),
                fail_on_violation=False,
            )

        if not execute:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    "Dry run only: no database records were modified. Execution requires "
                    "--execute, explicit --student-id values, and an exact --confirm-count."
                )
            )

    def _write_audit(self, audit, *, execute, options):
        inferred = audit.conversion_time_source != "prospect.converted_at"
        if audit.has_blocking_dependencies:
            proposed_action = (
                "BLOCKED: another Prospect references this Student conversion"
            )
        elif inferred and not options["include_inferred"]:
            proposed_action = "BLOCKED FOR EXECUTION: inferred conversion timestamp"
        elif audit.requires_inactive_student_history:
            proposed_action = (
                f"REUSE Contact #{audit.contact_id}; REACTIVATE existing Prospect "
                f"#{audit.prospect_id}; retain Student as inactive historical record; "
                "keep original financial, inquiry, communication, check-in, and "
                "course-history links"
            )
        else:
            proposed_action = (
                f"REUSE Contact #{audit.contact_id}; REACTIVATE existing Prospect "
                f"#{audit.prospect_id}; remove empty Student shell; set Prospect "
                f"status to {options['revert_status']}"
            )

        self.stdout.write(self.style.HTTP_INFO("=" * 78))
        self.stdout.write(
            f"Student ID={audit.student_id} UUID={audit.student_uuid} "
            f"Name={audit.student_name}"
        )
        self.stdout.write(
            f"Prospect ID={audit.prospect_id} UUID={audit.prospect_uuid} | "
            f"Contact ID={audit.contact_id} UUID={audit.contact_uuid}"
        )
        self.stdout.write(
            f"Conversion time={audit.conversion_time.isoformat()} "
            f"({audit.conversion_time_source})"
        )
        self.stdout.write(f"Violation={audit.category}: {audit.reason}")
        if audit.has_serious_meditator_integrity_violation:
            self.stdout.write(
                self.style.ERROR(
                    "SERIOUS INTEGRITY VIOLATION: invalid Student has an active "
                    "Meditator lifecycle. Execution will invalidate the active "
                    "Meditator status while preserving its profile, transition "
                    "events, timestamps, and metadata as audit history."
                )
            )

        if audit.invoice_rows:
            for invoice in audit.invoice_rows:
                self.stdout.write(
                    "Invoice "
                    f"ID={invoice.invoice_id} Number={invoice.invoice_number} "
                    f"Status={invoice.status} Amount={format_currency(invoice.total_amount)} "
                    "Total payment received at conversion="
                    f"{format_currency(invoice.confirmed_paid_at_conversion)} "
                    "Outstanding at conversion="
                    f"{format_currency(invoice.outstanding_at_conversion)} "
                    f"Existed={invoice.existed_at_conversion} "
                    f"Issued={invoice.issued_at_conversion}"
                )
        else:
            self.stdout.write(
                f"Invoice ID=- Amount={format_currency(0)} "
                f"Total payment received at conversion={format_currency(0)} "
                f"Outstanding at conversion={format_currency(0)}"
            )

        nonzero_dependencies = {
            key: value for key, value in audit.dependencies.items() if value
        }
        self.stdout.write(
            "Dependencies="
            + (str(nonzero_dependencies) if nonzero_dependencies else "none")
        )
        if audit.dependency_rows:
            self.stdout.write("Dependent records and disposition:")
            for dependency in audit.dependency_rows:
                self.stdout.write(
                    f"- {dependency.record_type} ID={dependency.record_id} | "
                    f"Action={dependency.action} | {dependency.details}"
                )
        if (
            audit.dependencies.get("student_inquiries")
            and not audit.requires_inactive_student_history
        ):
            self.stdout.write(
                "Dependency handling: preserve and reassign Student inquiries "
                "to the existing Prospect."
            )
        if (
            audit.dependencies.get("student_communications")
            and not audit.requires_inactive_student_history
        ):
            self.stdout.write(
                "Dependency handling: preserve and reassign communications, "
                "notes, and check-in history to the existing Prospect."
            )
        if audit.dependencies.get("student_notes"):
            self.stdout.write(
                "Dependency handling: merge unique Student notes into the "
                "existing Prospect notes."
            )
        historical_keys = (
            "enrollments",
            "course_sessions",
            "donation_statements_invoices",
            "payments",
            "disbursements",
            "interview_forms",
            "meditator_profiles",
            "meditator_transition_events",
            "unclassified_dependents",
        )
        if any(audit.dependencies.get(key) for key in historical_keys):
            self.stdout.write(
                "Dependency handling: retain the Student as inactive historical "
                "parent; preserve enrollments, sessions, accounting records, "
                "inquiries/communications, check-ins, attendance/interviews, "
                "and Meditator history with their original links."
            )
        self.stdout.write(f"Proposed action={proposed_action}")

        if not execute:
            return None
        if audit.has_blocking_dependencies:
            return (
                "warning",
                "SKIPPED: another Prospect references this Student conversion.",
            )
        if inferred and not options["include_inferred"]:
            return (
                "warning",
                "SKIPPED: add --include-inferred only after reviewing timestamp evidence.",
            )

        _refreshed_audit, changed, message = safely_revert_invalid_student(
            audit.student_id,
            revert_status=options["revert_status"],
        )
        if changed:
            return "success", f"CHANGED: {message}"
        return "warning", f"SKIPPED: {message}"

    def _write_verification(self, report, *, fail_on_violation):
        self.stdout.write(self.style.MIGRATE_HEADING("Student integrity verification"))
        self.stdout.write(f"Student rows: {report.students_total}")
        self.stdout.write(
            f"Active converted Students: {report.active_converted_students}"
        )
        self.stdout.write(
            f"Financially eligible active Students: {report.eligible_active_students}"
        )
        self.stdout.write(
            f"Pre-conversion enrollment shells: {report.preconversion_student_shells}"
        )
        self.stdout.write(
            f"Inactive historical shells: {report.inactive_historical_shells}"
        )
        self.stdout.write(f"Missing required invoice: {report.missing_invoice}")
        self.stdout.write(
            f"Unpaid/outstanding invoice: {report.unpaid_or_outstanding}"
        )
        self.stdout.write(
            f"Inconsistent lifecycle markers: {report.inconsistent_lifecycle}"
        )
        self.stdout.write(
            f"Duplicate Prospect-to-Contact links: {report.duplicate_prospect_contacts}"
        )
        for violation in report.violations:
            self.stdout.write(
                f"- Student ID={violation.student_id} Prospect ID={violation.prospect_id} "
                f"Invoice ID={violation.invoice_id or '-'} | {violation.reason}"
            )
        if report.passed:
            self.stdout.write(
                self.style.SUCCESS(
                    "VERIFICATION PASSED: every active converted Student satisfies "
                    "the current invoice/payment rule."
                )
            )
        else:
            message = (
                "VERIFICATION FAILED: one or more active Student lifecycle or "
                "identity integrity violations remain. No records were modified "
                "by verification."
            )
            self.stdout.write(self.style.ERROR(message))
            if fail_on_violation:
                raise CommandError(message)
