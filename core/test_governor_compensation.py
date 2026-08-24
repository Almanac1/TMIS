from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import (
    Contact,
    Course,
    CourseSession,
    Disbursement,
    DisbursementStatus,
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
from core.services.governor_compensation import get_governor_compensation_data
from core.services.disbursements import generate_disbursement_for_enrollment
from core.services.revenue_allocation import allocate_revenue


class GovernorCompensationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="governor", password="safe-password-123"
        )
        self.teacher = Teacher.objects.create(
            user=self.user,
            first_name="Ama",
            last_name="Mensah",
            email="ama.governor@example.com",
        )
        self.location = Location.objects.create(name="Compensation Test Centre")
        self.sequence = 0

    def create_enrollment(
        self,
        *,
        teacher=None,
        fee=Decimal("1000.00"),
        discount=Decimal("0.00"),
        confirmed_payment=None,
        payment_status=PaymentConfirmationStatus.CONFIRMED,
        enrollment_status=EnrollmentStatus.ENROLLED,
    ):
        self.sequence += 1
        index = self.sequence
        teacher = teacher or self.teacher
        contact = Contact.objects.create(
            first_name=f"Student{index}",
            last_name="Example",
            email=f"student{index}@example.com",
        )
        prospect = Prospect.objects.create(owner=self.user, contact=contact)
        student = Student.objects.create(owner=self.user, prospect=prospect, teacher=teacher)
        course = Course.objects.create(name=f"Course {index}", standard_fee=fee)
        session = CourseSession.objects.create(
            owner=self.user,
            course=course,
            teacher=teacher,
            session_name=f"Session {index}",
            start_date=timezone.now() + timedelta(days=1),
            end_date=timezone.now() + timedelta(days=2),
            location=self.location,
        )
        enrollment = Enrollment.objects.create(
            student=student,
            course=course,
            session=session,
            enrollment_date=timezone.now() - timedelta(days=index),
            status=enrollment_status,
            fee_amount=fee,
            discount_amount=discount,
        )
        final_amount = fee - discount
        invoice = Invoice.objects.create(
            owner=self.user,
            enrollment=enrollment,
            invoice_number=f"COMP-{index}",
            issue_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=14),
            subtotal=fee,
            discount_amount=discount,
            tax_amount=Decimal("0.00"),
            total_amount=final_amount,
            status=InvoiceStatus.SENT,
        )
        if confirmed_payment is not None:
            Payment.objects.create(
                owner=self.user,
                invoice=invoice,
                payment_date=timezone.now(),
                amount_paid=confirmed_payment,
                payment_method="transfer",
                confirmation_status=payment_status,
            )
        return enrollment

    def test_partial_payment_does_not_reduce_total_entitlement(self):
        self.create_enrollment(confirmed_payment=Decimal("400.00"))

        totals = get_governor_compensation_data(user=self.user)["totals"]

        self.assertEqual(totals.compensation_due, Decimal("500.00"))
        self.assertEqual(totals.compensation_funded, Decimal("200.00"))
        self.assertEqual(totals.compensation_unfunded, Decimal("300.00"))
        self.assertEqual(totals.amount_disbursed, Decimal("0.00"))
        self.assertEqual(totals.outstanding_compensation, Decimal("500.00"))

    def test_discounted_enrollment_uses_final_chargeable_value(self):
        self.create_enrollment(
            fee=Decimal("1200.00"),
            discount=Decimal("200.00"),
            confirmed_payment=Decimal("1000.00"),
        )

        totals = get_governor_compensation_data(user=self.user)["totals"]

        self.assertEqual(totals.enrollment_amount, Decimal("1000.00"))
        self.assertEqual(totals.compensation_due, Decimal("500.00"))
        self.assertEqual(totals.compensation_funded, Decimal("500.00"))

    def test_only_paid_disbursement_reduces_outstanding_compensation(self):
        enrollment = self.create_enrollment(confirmed_payment=Decimal("1000.00"))
        disbursement = Disbursement.objects.create(
            enrollment=enrollment,
            teacher=self.teacher,
            location=self.location,
            balance_due_snapshot=Decimal("1000.00"),
            teacher_amount=Decimal("0.00"),
            national_office_amount=Decimal("0.00"),
            ico_amount=Decimal("0.00"),
            marketing_amount=Decimal("0.00"),
            disbursement_date=timezone.localdate(),
            status=DisbursementStatus.PENDING,
        )
        pending = get_governor_compensation_data(user=self.user)["totals"]
        self.assertEqual(pending.amount_disbursed, Decimal("0.00"))

        disbursement.status = DisbursementStatus.PAID
        disbursement.save()
        paid = get_governor_compensation_data(user=self.user)["totals"]
        self.assertEqual(paid.amount_disbursed, Decimal("500.00"))
        self.assertEqual(paid.outstanding_compensation, Decimal("0.00"))

    def test_governor_is_scoped_to_their_own_enrollments(self):
        other_user = get_user_model().objects.create_user(username="other-governor")
        other_teacher = Teacher.objects.create(
            user=other_user,
            first_name="Other",
            last_name="Governor",
            email="other.governor@example.com",
        )
        self.create_enrollment(confirmed_payment=Decimal("400.00"))
        self.create_enrollment(teacher=other_teacher, confirmed_payment=Decimal("1000.00"))

        data = get_governor_compensation_data(user=self.user)

        self.assertEqual(data["totals"].enrollment_count, 1)
        self.assertEqual(data["totals"].compensation_due, Decimal("500.00"))
        self.assertEqual(data["enrollments"][0]["teacher"], self.teacher)

    def test_dashboard_card_shows_full_entitlement_for_partial_payment(self):
        self.create_enrollment(confirmed_payment=Decimal("400.00"))
        self.client.force_login(self.user)

        response = self.client.get(reverse("core:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Governor Compensation")
        self.assertContains(response, "Total Compensation Due")
        self.assertContains(response, "GHS 500.00")
        self.assertContains(response, "Amount Already Disbursed")
        self.assertContains(response, "Outstanding Compensation")

    def test_pending_payment_does_not_fund_compensation(self):
        self.create_enrollment(
            confirmed_payment=Decimal("400.00"),
            payment_status=PaymentConfirmationStatus.PENDING,
        )

        totals = get_governor_compensation_data(user=self.user)["totals"]

        self.assertEqual(totals.compensation_due, Decimal("500.00"))
        self.assertEqual(totals.compensation_funded, Decimal("0.00"))

    def test_unpaid_historical_enrollment_still_creates_full_entitlement(self):
        enrollment = self.create_enrollment(confirmed_payment=None)
        Enrollment.objects.filter(pk=enrollment.pk).update(
            enrollment_date=timezone.now() - timedelta(days=500)
        )

        totals = get_governor_compensation_data(user=self.user)["totals"]

        self.assertEqual(totals.compensation_due, Decimal("500.00"))
        self.assertEqual(totals.compensation_funded, Decimal("0.00"))

    def test_additional_confirmed_payment_increases_funding_not_entitlement(self):
        enrollment = self.create_enrollment(confirmed_payment=Decimal("250.00"))
        before = get_governor_compensation_data(user=self.user)["totals"]
        Payment.objects.create(
            owner=self.user,
            invoice=enrollment.invoice,
            payment_date=timezone.now(),
            amount_paid=Decimal("350.00"),
            payment_method="transfer",
            confirmation_status=PaymentConfirmationStatus.CONFIRMED,
        )

        after = get_governor_compensation_data(user=self.user)["totals"]

        self.assertEqual(before.compensation_due, Decimal("500.00"))
        self.assertEqual(after.compensation_due, Decimal("500.00"))
        self.assertEqual(before.compensation_funded, Decimal("125.00"))
        self.assertEqual(after.compensation_funded, Decimal("300.00"))

    def test_disbursement_generation_is_idempotent_and_uses_shared_formula(self):
        enrollment = self.create_enrollment(confirmed_payment=Decimal("1000.00"))

        first, first_created = generate_disbursement_for_enrollment(enrollment)
        second, second_created = generate_disbursement_for_enrollment(enrollment)

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Disbursement.objects.filter(enrollment=enrollment).count(), 1)
        self.assertEqual(first.ico_amount, Decimal("125.00"))
        self.assertEqual(first.national_office_amount, Decimal("200.00"))
        self.assertEqual(first.teacher_amount, Decimal("500.00"))
        self.assertEqual(first.marketing_amount, Decimal("175.00"))

    def test_shared_allocation_formula_totals_exactly(self):
        allocation = allocate_revenue(Decimal("1000.00"))

        self.assertEqual(allocation.ico, Decimal("125.00"))
        self.assertEqual(allocation.national_office, Decimal("200.00"))
        self.assertEqual(allocation.governor, Decimal("500.00"))
        self.assertEqual(allocation.marketing, Decimal("175.00"))
        self.assertEqual(
            allocation.ico
            + allocation.national_office
            + allocation.governor
            + allocation.marketing,
            Decimal("1000.00"),
        )

    def test_multiple_enrollments_for_one_governor_are_aggregated(self):
        self.create_enrollment(fee=Decimal("1000.00"), confirmed_payment=Decimal("1000.00"))
        self.create_enrollment(fee=Decimal("600.00"), confirmed_payment=None)

        totals = get_governor_compensation_data(user=self.user)["totals"]

        self.assertEqual(totals.enrollment_count, 2)
        self.assertEqual(totals.compensation_due, Decimal("800.00"))
        self.assertEqual(totals.compensation_funded, Decimal("500.00"))
