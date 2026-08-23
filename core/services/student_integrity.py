from dataclasses import dataclass, field
from typing import Dict, List, Set

from django.db.models import Count

from core.models import (
    Contact,
    EnrollmentStatus,
    Invoice,
    Payment,
    Prospect,
    ProspectStatus,
    Student,
)
from core.services.prospect_conversion import get_prospect_conversion_eligibility_map


@dataclass(frozen=True)
class ProtectedRecordSnapshot:
    contact_ids: Set[object]
    prospect_ids: Set[object]
    invoice_ids: Set[object]
    payment_ids: Set[object]


@dataclass(frozen=True)
class ActiveStudentViolation:
    student_id: object
    prospect_id: object
    reason: str
    invoice_id: object = None


@dataclass
class StudentIntegrityReport:
    students_total: int
    active_converted_students: int
    eligible_active_students: int
    preconversion_student_shells: int
    inactive_historical_shells: int
    missing_invoice: int
    unpaid_or_outstanding: int
    inconsistent_lifecycle: int
    duplicate_prospect_contacts: int
    violations: List[ActiveStudentViolation] = field(default_factory=list)

    @property
    def passed(self):
        return not (
            self.missing_invoice
            or self.unpaid_or_outstanding
            or self.inconsistent_lifecycle
            or self.duplicate_prospect_contacts
        )


def capture_protected_record_snapshot() -> ProtectedRecordSnapshot:
    return ProtectedRecordSnapshot(
        contact_ids=set(Contact.objects.values_list("pk", flat=True)),
        prospect_ids=set(Prospect.objects.values_list("pk", flat=True)),
        invoice_ids=set(Invoice.objects.values_list("pk", flat=True)),
        payment_ids=set(Payment.objects.values_list("pk", flat=True)),
    )


def compare_protected_record_snapshots(
    before: ProtectedRecordSnapshot,
    after: ProtectedRecordSnapshot,
) -> Dict[str, Dict[str, Set[object]]]:
    changes = {}
    for name in ("contact_ids", "prospect_ids", "invoice_ids", "payment_ids"):
        before_ids = getattr(before, name)
        after_ids = getattr(after, name)
        if before_ids != after_ids:
            changes[name] = {
                "removed": before_ids - after_ids,
                "added": after_ids - before_ids,
            }
    return changes


def verify_active_student_integrity() -> StudentIntegrityReport:
    students = list(Student.objects.select_related("prospect").order_by("pk"))
    active_converted = []
    inconsistent = []
    preconversion = 0
    historical = 0

    for student in students:
        prospect = student.prospect
        markers = (
            prospect.status == ProspectStatus.CONVERTED,
            bool(prospect.converted_to_student),
            prospect.converted_student_id == student.pk,
            prospect.converted_at is not None,
        )
        if any(markers) and student.enrollment_status != EnrollmentStatus.INACTIVE:
            active_converted.append(student)
            if not all(markers):
                inconsistent.append(student)
        elif any(markers):
            inconsistent.append(student)
        elif student.enrollment_status == EnrollmentStatus.INACTIVE:
            historical += 1
        else:
            # The current enrollment/invoice architecture creates this shell
            # before conversion. It is not an active Student lifecycle.
            preconversion += 1

    eligibility_map = get_prospect_conversion_eligibility_map(
        [student.prospect for student in active_converted]
    )
    violations = []
    missing_invoice = 0
    unpaid = 0
    eligible = 0
    for student in active_converted:
        result = eligibility_map[student.prospect_id]
        if result.eligible:
            eligible += 1
            continue
        if "no donation statement" in result.message:
            missing_invoice += 1
        else:
            unpaid += 1
        violations.append(
            ActiveStudentViolation(
                student_id=student.pk,
                prospect_id=student.prospect_id,
                reason=result.message,
                invoice_id=result.invoice_id,
            )
        )

    for student in inconsistent:
        violations.append(
            ActiveStudentViolation(
                student_id=student.pk,
                prospect_id=student.prospect_id,
                reason="Prospect/Student lifecycle conversion markers are inconsistent.",
            )
        )

    duplicate_prospect_contacts = sum(
        max(0, row["total"] - 1)
        for row in (
            Prospect.objects.exclude(contact_id__isnull=True)
            .values("contact_id")
            .annotate(total=Count("pk"))
            .filter(total__gt=1)
        )
    )

    return StudentIntegrityReport(
        students_total=len(students),
        active_converted_students=len(active_converted),
        eligible_active_students=eligible,
        preconversion_student_shells=preconversion,
        inactive_historical_shells=historical,
        missing_invoice=missing_invoice,
        unpaid_or_outstanding=unpaid,
        inconsistent_lifecycle=len(inconsistent),
        duplicate_prospect_contacts=duplicate_prospect_contacts,
        violations=violations,
    )
