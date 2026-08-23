from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional

from django.db import transaction
from django.utils import timezone

from core.models import (
    Communication,
    CommunicationType,
    Disbursement,
    Enrollment,
    EnrollmentStatus,
    Inquiry,
    InterviewForm,
    Invoice,
    InvoiceStatus,
    Meditator,
    MeditatorTransitionEvent,
    PaymentConfirmationStatus,
    Payment,
    Prospect,
    ProspectStatus,
    RecipientType,
    Student,
)


MONEY_ZERO = Decimal("0.00")
ISSUED_INVOICE_STATUSES = {
    InvoiceStatus.SENT,
    InvoiceStatus.PARTIAL,
    InvoiceStatus.PAID,
    InvoiceStatus.OVERDUE,
}


@dataclass(frozen=True)
class InvoiceAuditRow:
    invoice_id: int
    invoice_number: str
    status: str
    total_amount: Decimal
    confirmed_paid_at_conversion: Decimal
    outstanding_at_conversion: Decimal
    existed_at_conversion: bool
    issued_at_conversion: bool


@dataclass(frozen=True)
class DependencyAuditRow:
    record_type: str
    record_id: str
    action: str
    details: str


@dataclass
class StudentConversionAudit:
    student_id: int
    student_uuid: str
    student_name: str
    prospect_id: int
    prospect_uuid: str
    contact_id: int
    contact_uuid: str
    conversion_time: object
    conversion_time_source: str
    category: str
    reason: str
    compliant: bool
    is_conversion_candidate: bool
    restored_to_prospect: bool = False
    invoice_rows: List[InvoiceAuditRow] = field(default_factory=list)
    dependencies: Dict[str, int] = field(default_factory=dict)
    dependency_rows: List[DependencyAuditRow] = field(default_factory=list)

    @property
    def has_blocking_dependencies(self):
        # A Student referenced as the converted result of another Prospect is
        # an identity ambiguity. It must be reviewed rather than guessed at.
        return bool(
            self.dependencies.get("other_prospect_conversion_references", 0)
        )

    @property
    def has_serious_meditator_integrity_violation(self):
        return bool(self.dependencies.get("active_meditator_profiles", 0))

    @property
    def requires_inactive_student_history(self):
        """Whether Student-only history prevents removal of the Student row."""
        return any(
            self.dependencies.get(name, 0)
            for name in (
                "enrollments",
                "interview_forms",
                "meditator_profiles",
                "meditator_transition_events",
                "unclassified_dependents",
            )
        )


def _money(value):
    return Decimal(value or MONEY_ZERO).quantize(Decimal("0.01"))


KNOWN_STUDENT_REVERSE_RELATIONS = {
    ("core.prospect", "converted_student"),
    ("core.inquiry", "student"),
    ("core.enrollment", "student"),
    ("core.interviewform", "student"),
    ("core.meditator", "student"),
    ("core.meditatortransitionevent", "student"),
    ("core.communication", "student"),
}


def _get_unclassified_student_dependents(student: Student):
    """Fail closed when a new Student relation is added without cleanup policy."""
    dependents = []
    for relation in student._meta.related_objects:
        relation_key = (
            relation.related_model._meta.label_lower,
            relation.field.name,
        )
        if relation_key in KNOWN_STUDENT_REVERSE_RELATIONS:
            continue
        objects = relation.related_model._default_manager.filter(
            **{relation.field.name: student}
        )
        dependents.extend(
            (relation.related_model._meta.label, str(obj.pk)) for obj in objects
        )
    return dependents


def get_student_dependencies(student: Student):
    prospect = student.prospect
    enrollments = Enrollment.objects.filter(student=student)
    invoices = Invoice.objects.filter(enrollment__student=student)
    payments = Payment.objects.filter(invoice__enrollment__student=student)
    communications = Communication.objects.filter(student=student)
    interview_forms = InterviewForm.objects.filter(student=student)
    session_ids = set(enrollments.values_list("session_id", flat=True))
    session_ids.update(
        interview_forms.exclude(session_id__isnull=True).values_list(
            "session_id", flat=True
        )
    )
    unclassified_dependents = _get_unclassified_student_dependents(student)
    return {
        "enrollments": enrollments.count(),
        "course_sessions": len(session_ids),
        "completed_enrollments_attendance": enrollments.filter(
            status=EnrollmentStatus.COMPLETED
        ).count(),
        "donation_statements_invoices": invoices.count(),
        "payments": payments.count(),
        "disbursements": Disbursement.objects.filter(
            enrollment__student=student
        ).count(),
        "student_inquiries": Inquiry.objects.filter(student=student).count(),
        "prospect_inquiries_preserved": Inquiry.objects.filter(prospect=prospect).count(),
        "student_communications": communications.count(),
        "attendance_check_ins": communications.filter(
            communication_type=CommunicationType.FOLLOW_UP,
            sent_at__isnull=False,
        ).count(),
        "prospect_communications_preserved": Communication.objects.filter(
            prospect=prospect
        ).count(),
        "interview_forms": interview_forms.count(),
        "meditator_profiles": Meditator.objects.filter(student=student).count(),
        "active_meditator_profiles": Meditator.objects.filter(
            student=student,
            is_active=True,
        ).count(),
        "meditator_transition_events": MeditatorTransitionEvent.objects.filter(
            student=student
        ).count(),
        "other_prospect_conversion_references": Prospect.objects.filter(
            converted_student=student
        ).exclude(pk=prospect.pk).count(),
        "student_notes": int(bool((student.notes or "").strip())),
        "enrollment_notes": enrollments.exclude(notes="").count(),
        "invoice_notes": invoices.exclude(notes="").count(),
        "payment_notes": payments.exclude(notes="").count(),
        "disbursement_notes": Disbursement.objects.filter(
            enrollment__student=student
        ).exclude(notes="").count(),
        "communication_notes": communications.exclude(notes="").count(),
        "interview_notes": interview_forms.exclude(notes="").count(),
        "unclassified_dependents": len(unclassified_dependents),
    }


def get_dependency_audit_rows(
    student: Student,
    *,
    retain_student_history: bool,
) -> List[DependencyAuditRow]:
    rows = []
    enrollments = list(
        Enrollment.objects.filter(student=student).select_related("course", "session")
    )
    enrollment_action = "PRESERVE on inactive Student history"
    for enrollment in enrollments:
        rows.append(
            DependencyAuditRow(
                record_type="Enrollment",
                record_id=str(enrollment.pk),
                action=enrollment_action,
                details=(
                    f"course={enrollment.course_id} session={enrollment.session_id} "
                    f"status={enrollment.status}"
                ),
            )
        )

    session_map = {enrollment.session_id: enrollment.session for enrollment in enrollments}
    for interview in InterviewForm.objects.filter(student=student).select_related("session"):
        if interview.session_id:
            session_map[interview.session_id] = interview.session
        rows.append(
            DependencyAuditRow(
                record_type="InterviewForm",
                record_id=str(interview.pk),
                action="PRESERVE on inactive Student history",
                details=f"session={interview.session_id or '-'} status={interview.status}",
            )
        )
    for session_id, session in session_map.items():
        rows.append(
            DependencyAuditRow(
                record_type="CourseSession",
                record_id=str(session_id),
                action="PRESERVE; never delete through Student cleanup",
                details=f"course={session.course_id} status={session.status}",
            )
        )

    invoices = Invoice.objects.filter(enrollment__student=student)
    for invoice in invoices:
        rows.append(
            DependencyAuditRow(
                record_type="DonationStatement/Invoice",
                record_id=str(invoice.pk),
                action="PRESERVE as accounting history",
                details=(
                    f"number={invoice.invoice_number} status={invoice.status} "
                    f"amount={_money(invoice.total_amount):.2f}"
                ),
            )
        )
    for payment in Payment.objects.filter(invoice__enrollment__student=student):
        rows.append(
            DependencyAuditRow(
                record_type="Payment",
                record_id=str(payment.pk),
                action="PRESERVE as accounting history",
                details=(
                    f"invoice={payment.invoice_id} amount={_money(payment.amount_paid):.2f} "
                    f"confirmation={payment.confirmation_status}"
                ),
            )
        )
    for disbursement in Disbursement.objects.filter(enrollment__student=student):
        rows.append(
            DependencyAuditRow(
                record_type="Disbursement",
                record_id=str(disbursement.pk),
                action="PRESERVE as accounting history",
                details=f"enrollment={disbursement.enrollment_id} status={disbursement.status}",
            )
        )

    stage_action = (
        "PRESERVE original Student link"
        if retain_student_history
        else "REASSIGN to existing Prospect before Student removal"
    )
    for communication in Communication.objects.filter(student=student):
        record_type = (
            "Attendance/Check-in Communication"
            if communication.communication_type == CommunicationType.FOLLOW_UP
            and communication.sent_at is not None
            else "Communication"
        )
        rows.append(
            DependencyAuditRow(
                record_type=record_type,
                record_id=str(communication.pk),
                action=stage_action,
                details=(
                    f"type={communication.communication_type} "
                    f"status={communication.delivery_status} "
                    f"enrollment={communication.enrollment_id or '-'}"
                ),
            )
        )
    for inquiry in Inquiry.objects.filter(student=student):
        rows.append(
            DependencyAuditRow(
                record_type="Inquiry",
                record_id=str(inquiry.pk),
                action=stage_action,
                details=f"status={inquiry.status}",
            )
        )
    for meditator in Meditator.objects.filter(student=student):
        rows.append(
            DependencyAuditRow(
                record_type="Meditator",
                record_id=str(meditator.pk),
                action=(
                    "INVALIDATE active lifecycle; PRESERVE profile as audit history"
                    if meditator.is_active
                    else "PRESERVE previously invalidated audit history"
                ),
                details=(
                    f"active={meditator.is_active} "
                    f"transitioned_at={meditator.transitioned_at.isoformat()}"
                ),
            )
        )
    for event in MeditatorTransitionEvent.objects.filter(student=student):
        rows.append(
            DependencyAuditRow(
                record_type="MeditatorTransitionEvent",
                record_id=str(event.pk),
                action="PRESERVE on inactive Student history",
                details=f"triggered_at={event.triggered_at.isoformat()}",
            )
        )
    if (student.notes or "").strip():
        rows.append(
            DependencyAuditRow(
                record_type="StudentNotes",
                record_id=str(student.pk),
                action="MERGE unique text into existing Prospect notes",
                details="Student notes are not discarded",
            )
        )
    for model_label, object_id in _get_unclassified_student_dependents(student):
        rows.append(
            DependencyAuditRow(
                record_type=model_label,
                record_id=object_id,
                action="PRESERVE; retain inactive Student (unclassified relation)",
                details="No automatic cleanup policy; fail closed",
            )
        )
    return rows


def _merge_student_notes_into_prospect(student: Student, prospect: Prospect) -> bool:
    student_notes = (student.notes or "").strip()
    prospect_notes = (prospect.notes or "").strip()
    if not student_notes or student_notes == prospect_notes or student_notes in prospect_notes:
        return False
    restored_note = f"[Restored from invalid Student #{student.pk}]\n{student_notes}"
    prospect.notes = f"{prospect_notes}\n\n{restored_note}" if prospect_notes else restored_note
    return True


def audit_student_conversion(student: Student):
    prospect = student.prospect
    contact = prospect.contact
    conversion_time = prospect.converted_at or student.created_at
    conversion_time_source = (
        "prospect.converted_at"
        if prospect.converted_at
        else "student.created_at fallback"
    )
    is_conversion_candidate = bool(
        prospect.status == ProspectStatus.CONVERTED
        or prospect.converted_to_student
        or prospect.converted_student_id is not None
        or prospect.converted_at is not None
    )
    restored_to_prospect = bool(
        student.enrollment_status == EnrollmentStatus.INACTIVE
        and prospect.status != ProspectStatus.CONVERTED
        and not prospect.converted_to_student
        and prospect.converted_student_id is None
        and prospect.converted_at is None
    )

    invoices = list(
        Invoice.objects.filter(enrollment__student=student)
        .prefetch_related("payments")
        .order_by("issue_date", "pk")
    )
    invoice_rows = []
    for invoice in invoices:
        existed_at_conversion = bool(
            invoice.created_at <= conversion_time
            and invoice.issue_date <= conversion_time.date()
        )
        issued_at_conversion = bool(
            existed_at_conversion and invoice.status in ISSUED_INVOICE_STATUSES
        )
        confirmed_payments = [
            payment
            for payment in invoice.payments.all()
            if payment.confirmation_status == PaymentConfirmationStatus.CONFIRMED
            and payment.payment_date <= conversion_time
        ]
        confirmed_paid = _money(
            sum(
                (payment.amount_paid for payment in confirmed_payments),
                MONEY_ZERO,
            )
        )
        outstanding = _money(
            max(MONEY_ZERO, _money(invoice.total_amount) - confirmed_paid)
        )
        invoice_rows.append(
            InvoiceAuditRow(
                invoice_id=invoice.pk,
                invoice_number=invoice.invoice_number,
                status=invoice.status,
                total_amount=_money(invoice.total_amount),
                confirmed_paid_at_conversion=confirmed_paid,
                outstanding_at_conversion=outstanding,
                existed_at_conversion=existed_at_conversion,
                issued_at_conversion=issued_at_conversion,
            )
        )

    category = "compliant"
    reason = "Conversion requirements were satisfied."
    if not invoice_rows:
        category = "no_invoice"
        reason = "No associated invoice/donation statement exists."
    else:
        existing_rows = [row for row in invoice_rows if row.existed_at_conversion]
        active_existing_rows = [
            row for row in existing_rows if row.status != InvoiceStatus.CANCELLED
        ]
        issued_rows = [row for row in active_existing_rows if row.issued_at_conversion]
        if not existing_rows:
            category = "invoice_after_conversion"
            reason = "Associated invoice(s) were created or issued after conversion."
        elif not issued_rows or len(issued_rows) != len(active_existing_rows):
            category = "unissued_or_invalid_invoice"
            reason = "No valid issued invoice existed at conversion."
        else:
            payment_count = 0
            outstanding = MONEY_ZERO
            for invoice in invoices:
                matching_row = next(
                    row for row in issued_rows if row.invoice_id == invoice.pk
                ) if any(row.invoice_id == invoice.pk for row in issued_rows) else None
                if matching_row is None:
                    continue
                payment_count += sum(
                    1
                    for payment in invoice.payments.all()
                    if payment.confirmation_status
                    == PaymentConfirmationStatus.CONFIRMED
                    and payment.payment_date <= conversion_time
                )
                outstanding += matching_row.outstanding_at_conversion
            if payment_count == 0:
                category = "no_payment"
                reason = "No confirmed payment existed at conversion."
            elif outstanding > MONEY_ZERO:
                category = "partial_or_outstanding"
                reason = (
                    "Confirmed payment was partial; outstanding balance at "
                    f"conversion was GHS {_money(outstanding):.2f}."
                )

    dependencies = get_student_dependencies(student)
    retain_student_history = any(
        dependencies.get(name, 0)
        for name in (
            "enrollments",
            "interview_forms",
            "meditator_profiles",
            "meditator_transition_events",
            "unclassified_dependents",
        )
    )
    return StudentConversionAudit(
        student_id=student.pk,
        student_uuid=str(student.uuid),
        student_name=str(prospect),
        prospect_id=prospect.pk,
        prospect_uuid=str(prospect.uuid),
        contact_id=contact.pk,
        contact_uuid=str(contact.uuid),
        conversion_time=conversion_time,
        conversion_time_source=conversion_time_source,
        category=category,
        reason=reason,
        compliant=category == "compliant",
        is_conversion_candidate=is_conversion_candidate,
        restored_to_prospect=restored_to_prospect,
        invoice_rows=invoice_rows,
        dependencies=dependencies,
        dependency_rows=get_dependency_audit_rows(
            student,
            retain_student_history=retain_student_history,
        ),
    )


def audit_student_conversions(student_ids: Optional[List[int]] = None):
    queryset = Student.objects.select_related("prospect__contact").order_by("pk")
    if student_ids:
        queryset = queryset.filter(pk__in=student_ids)
    return [audit_student_conversion(student) for student in queryset]


def safely_revert_invalid_student(student_id: int, *, revert_status: str):
    with transaction.atomic():
        student = (
            Student.objects.select_for_update()
            .select_related("prospect__contact")
            .get(pk=student_id)
        )
        prospect = Prospect.objects.select_for_update().get(pk=student.prospect_id)
        audit = audit_student_conversion(student)
        if not audit.is_conversion_candidate and not audit.restored_to_prospect:
            return audit, False, "Record is not marked as a completed conversion."
        if audit.compliant:
            return audit, False, "Record is now compliant; no cleanup performed."
        if audit.restored_to_prospect:
            return audit, False, "Record has already been restored to Prospect."
        if audit.has_blocking_dependencies:
            return (
                audit,
                False,
                "Blocked because another Prospect references this Student conversion.",
            )

        invalidation_time = timezone.now()
        for meditator in Meditator.objects.select_for_update().filter(
            student=student,
            is_active=True,
        ):
            metadata = dict(meditator.metadata or {})
            invalidations = list(metadata.get("integrity_invalidations") or [])
            invalidations.append(
                {
                    "invalidated_at": invalidation_time.isoformat(),
                    "reason": audit.reason,
                    "violation_category": audit.category,
                    "student_id": str(student.pk),
                    "prospect_id": str(prospect.pk),
                }
            )
            metadata["integrity_invalidations"] = invalidations
            meditator.is_active = False
            meditator.invalidated_at = invalidation_time
            meditator.invalidation_reason = (
                "Student lifecycle was invalid and restored to Prospect: "
                f"{audit.reason}"
            )
            meditator.metadata = metadata
            meditator.save(
                update_fields=[
                    "is_active",
                    "invalidated_at",
                    "invalidation_reason",
                    "metadata",
                    "updated_at",
                ]
            )

        retain_student_history = audit.requires_inactive_student_history

        # Student.prospect and Prospect.contact are protected one-to-one links.
        # Always restore those exact records: never infer identity from names or
        # contact details, and never create a replacement Contact or Prospect.
        # When Student-only history requires an inactive shell, keep inquiries
        # and communications attached to it so their original lifecycle stage,
        # enrollment context, and check-in meaning remain intact. If the shell
        # can be removed, reassign those records before deletion.
        if not retain_student_history:
            Inquiry.objects.filter(student=student).update(
                prospect=prospect,
                student=None,
            )
            Communication.objects.filter(student=student).update(
                recipient_type=RecipientType.PROSPECT,
                prospect=prospect,
                student=None,
            )

        notes_merged = _merge_student_notes_into_prospect(student, prospect)

        if retain_student_history:
            # Enrollments, invoices/payments, interviews, and meditator history
            # are Student-only in the current schema. Keep their parent as an
            # inactive historical shell rather than cascading away that data.
            if student.enrollment_status != EnrollmentStatus.INACTIVE:
                Student.objects.filter(pk=student.pk).update(
                    enrollment_status=EnrollmentStatus.INACTIVE
                )
        else:
            student.delete()

        prospect.status = revert_status
        prospect.is_archived = False
        prospect.converted_to_student = False
        prospect.converted_student = None
        prospect.converted_at = None
        update_fields = [
            "status",
            "is_archived",
            "converted_to_student",
            "converted_student",
            "converted_at",
            "updated_at",
        ]
        if notes_merged:
            update_fields.append("notes")
        prospect.save(
            update_fields=update_fields
        )
        if retain_student_history:
            meditator_message = (
                " Active Meditator lifecycle invalidated with audit history preserved."
                if audit.has_serious_meditator_integrity_violation
                else ""
            )
            message = (
                f"Existing Prospect #{prospect.pk} restored using Contact "
                f"#{prospect.contact_id}; Student marked inactive to preserve "
                "dependent history, including original inquiry/communication "
                "links; no Contact or Prospect created."
                f"{meditator_message}"
            )
        else:
            message = (
                f"Existing Prospect #{prospect.pk} restored using Contact "
                f"#{prospect.contact_id}; empty Student shell removed; Student "
                "inquiries/communications moved to Prospect; no Contact or "
                "Prospect created."
            )
        return audit, True, message
