from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    Contact,
    Course,
    CourseSession,
    Enrollment,
    EnrollmentStatus,
    Invoice,
    InvoiceStatus,
    Location,
    Payment,
    PaymentConfirmationStatus,
    Prospect,
    Student,
    Teacher,
)


class EnrollmentCompletionFinancialGateTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="completion_gate_user",
            password="safe-password-123",
        )
        self.client.force_login(self.user)
        self.teacher = Teacher.objects.create(
            first_name="Gate",
            last_name="Teacher",
            email="gate.teacher@example.com",
        )
        self.location = Location.objects.create(name="Completion Gate Center")
        self.course = Course.objects.create(
            name="TM Introductory Program",
            standard_fee=Decimal("500.00"),
        )
        self.session = CourseSession.objects.create(
            owner=self.user,
            course=self.course,
            teacher=self.teacher,
            session_name="Completion Gate Session",
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=1),
            location=self.location,
        )
        self.student = Student.objects.create(
            owner=self.user,
            prospect=Prospect.objects.create(
                owner=self.user,
                contact=Contact.objects.create(
                    first_name="Completion",
                    last_name="Candidate",
                ),
            ),
        )
        self.enrollment = Enrollment.objects.create(
            student=self.student,
            course=self.course,
            session=self.session,
            enrollment_date=timezone.now(),
            status=EnrollmentStatus.ENROLLED,
            fee_amount=Decimal("500.00"),
        )

    def _invoice(self, total=Decimal("500.00"), status=InvoiceStatus.SENT):
        return Invoice.objects.create(
            owner=self.user,
            enrollment=self.enrollment,
            invoice_number=f"COMPLETION-{self.enrollment.pk}",
            issue_date=timezone.localdate(),
            total_amount=total,
            subtotal=total,
            status=status,
        )

    def _payment(self, invoice, amount, confirmation_status):
        return Payment.objects.create(
            owner=self.user,
            invoice=invoice,
            payment_date=timezone.now(),
            amount_paid=amount,
            payment_method="transfer",
            confirmation_status=confirmation_status,
        )

    def _complete_enrollment(self):
        self.enrollment.status = EnrollmentStatus.COMPLETED
        self.enrollment.save(update_fields=["status", "updated_at"])

    def test_enrollment_cannot_complete_without_invoice(self):
        with self.assertRaisesMessage(ValidationError, "until an invoice has been issued"):
            self._complete_enrollment()

    def test_enrollment_cannot_complete_with_unpaid_or_partial_invoice(self):
        invoice = self._invoice()
        self._payment(invoice, Decimal("200.00"), PaymentConfirmationStatus.CONFIRMED)

        with self.assertRaisesMessage(ValidationError, "₦300.00"):
            self._complete_enrollment()

    def test_pending_failed_and_reversed_payments_do_not_count(self):
        invoice = self._invoice()
        for status in (
            PaymentConfirmationStatus.PENDING,
            PaymentConfirmationStatus.FAILED,
            PaymentConfirmationStatus.REVERSED,
        ):
            self._payment(invoice, Decimal("500.00"), status)

        with self.assertRaisesMessage(ValidationError, "₦500.00"):
            self._complete_enrollment()
        self.assertEqual(invoice.total_paid, Decimal("0.00"))
        self.assertEqual(invoice.balance_due, Decimal("500.00"))

    def test_fully_confirmed_payment_allows_enrollment_completion(self):
        invoice = self._invoice(status=InvoiceStatus.SENT)
        self._payment(invoice, Decimal("500.00"), PaymentConfirmationStatus.CONFIRMED)

        self._complete_enrollment()

        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, EnrollmentStatus.COMPLETED)
        self.assertEqual(invoice.total_paid, Decimal("500.00"))
        self.assertEqual(invoice.balance_due, Decimal("0.00"))

    def test_student_cannot_complete_while_any_active_invoice_is_outstanding(self):
        invoice = self._invoice()
        self._payment(invoice, Decimal("250.00"), PaymentConfirmationStatus.CONFIRMED)
        self.student.enrollment_status = EnrollmentStatus.COMPLETED

        with self.assertRaisesMessage(ValidationError, "₦250.00"):
            self.student.save(update_fields=["enrollment_status", "updated_at"])

    def test_student_can_complete_after_all_enrollments_are_fully_paid(self):
        invoice = self._invoice()
        self._payment(invoice, Decimal("500.00"), PaymentConfirmationStatus.CONFIRMED)
        self._complete_enrollment()
        self.student.enrollment_status = EnrollmentStatus.COMPLETED

        self.student.save(update_fields=["enrollment_status", "updated_at"])

        self.student.refresh_from_db()
        self.assertEqual(self.student.enrollment_status, EnrollmentStatus.COMPLETED)

    def test_legacy_completed_student_page_flags_outstanding_balance(self):
        invoice = self._invoice()
        self._payment(invoice, Decimal("100.00"), PaymentConfirmationStatus.CONFIRMED)
        Student.objects.filter(pk=self.student.pk).update(
            enrollment_status=EnrollmentStatus.COMPLETED,
        )

        response = self.client.get(
            reverse("core:student-detail", kwargs={"pk": self.student.pk}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Completion integrity violation")
        self.assertContains(response, "₦400.00")
