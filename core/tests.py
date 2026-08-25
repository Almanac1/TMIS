from django.contrib.auth import get_user_model
from datetime import timedelta
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError
from io import StringIO
from django.test import TestCase, SimpleTestCase
from django.test.utils import override_settings
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from types import SimpleNamespace
from unittest.mock import patch

from .models import (
    Communication,
    Contact,
    Course,
    CourseFormat,
    CourseSession,
    CourseStatus,
    Enrollment,
    EnrollmentStatus,
    Invoice,
    InvoiceStatus,
    Location,
    Meditator,
    MeditatorTransitionEvent,
    Payment,
    PaymentConfirmationStatus,
    Prospect,
    ProspectStatus,
    SessionStatus,
    Student,
    Teacher,
)
from .services.invoicing import generate_invoice_for_enrollment
from .services.enrollment_eligibility import is_eligible_for_course
from .forms import EnrollmentForm


def make_prospect_financially_eligible(prospect, *, user=None, amount=Decimal("500.00")):
    """Build a paid issued invoice using the CRM's existing financial relationships."""
    owner = user or prospect.owner
    student, _ = Student.objects.get_or_create(
        prospect=prospect,
        defaults={"owner": owner},
    )
    teacher = Teacher.objects.create(
        first_name="Paid",
        last_name=f"Teacher {prospect.pk}",
        email=f"paid.teacher.{prospect.pk}@example.com",
    )
    location = Location.objects.create(name=f"Paid Conversion Center {prospect.pk}")
    course = Course.objects.create(
        name=f"Paid Conversion Course {prospect.pk}",
        standard_fee=amount,
    )
    session = CourseSession.objects.create(
        owner=owner,
        course=course,
        teacher=teacher,
        session_name=f"Paid Conversion Session {prospect.pk}",
        start_date=timezone.now() + timedelta(days=2),
        end_date=timezone.now() + timedelta(days=3),
        location=location,
        status=SessionStatus.SCHEDULED,
    )
    enrollment = Enrollment.objects.create(
        student=student,
        course=course,
        session=session,
        enrollment_date=timezone.now(),
        fee_amount=amount,
        discount_amount=Decimal("0.00"),
    )
    invoice = Invoice.objects.create(
        owner=owner,
        enrollment=enrollment,
        invoice_number=f"PAID-CONVERSION-{prospect.pk}",
        issue_date=timezone.localdate(),
        due_date=timezone.localdate() + timedelta(days=14),
        subtotal=amount,
        discount_amount=Decimal("0.00"),
        tax_amount=Decimal("0.00"),
        total_amount=amount,
        status=InvoiceStatus.PAID,
    )
    Payment.objects.create(
        owner=owner,
        invoice=invoice,
        payment_date=timezone.now(),
        amount_paid=amount,
        payment_method="transfer",
        confirmation_status=PaymentConfirmationStatus.CONFIRMED,
    )
    return student, invoice


class EnrollmentFamilyDetectionUnitTests(SimpleTestCase):
    def test_tm_family_detected_by_code(self):
        course = SimpleNamespace(code="TMf", name="Anything")
        self.assertTrue(EnrollmentForm._is_tm_family_course(course))

    def test_tm_family_detected_by_name(self):
        course = SimpleNamespace(code="", name="TM - family")
        self.assertTrue(EnrollmentForm._is_tm_family_course(course))

    def test_non_family_not_detected(self):
        course = SimpleNamespace(code="TMa", name="TM - adult")
        self.assertFalse(EnrollmentForm._is_tm_family_course(course))


class StudentArchiveBehaviorTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="tester",
            password="safe-password-123",
        )
        self.prospect = Prospect.objects.create(
            owner=self.user,
            contact=Contact.objects.create(
                first_name="Amara",
                last_name="Anderson",
                email="amara@example.com",
            ),
        )
        self.student = Student.objects.create(
            owner=self.user,
            prospect=self.prospect,
        )

    def test_student_delete_route_is_not_available(self):
        with self.assertRaises(NoReverseMatch):
            reverse("core:student-delete", kwargs={"pk": self.student.pk})

    def test_student_archive_view_marks_student_inactive(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("core:student-archive", kwargs={"pk": self.student.pk})
        )
        self.assertRedirects(
            response, reverse("core:student-detail", kwargs={"pk": self.student.pk})
        )
        self.student.refresh_from_db()
        self.assertEqual(self.student.enrollment_status, EnrollmentStatus.INACTIVE)


class StudentDuplicatePreventionTests(TestCase):
    def test_convert_blocks_duplicate_identity_with_same_name_and_phone(self):
        first_prospect = Prospect.objects.create(
            contact=Contact.objects.create(
                first_name="Liam",
                last_name="Mensah",
                email="liam.one@example.com",
                phone_number="+1 (555) 101-2020",
            ),
        )
        Student.objects.create(prospect=first_prospect)

        duplicate_contact = Contact(
            first_name="Liam",
            last_name="Mensah",
            email="liam.two@example.com",
            phone_number="5551012020",
        )
        with self.assertRaises(ValidationError):
            duplicate_contact.save()

        # Simulate a legacy/imported duplicate that bypassed model validation;
        # conversion remains a second line of defence.
        Contact.objects.bulk_create([duplicate_contact])
        duplicate_candidate = Prospect.objects.create(contact=duplicate_contact)

        self.assertEqual(
            duplicate_candidate.find_potential_duplicate_student().pk,
            first_prospect.student_record.pk,
        )

        self.assertEqual(
            Student.objects.filter(
                prospect__contact__first_name__iexact="Liam",
                prospect__contact__last_name__iexact="Mensah",
            ).count(),
            1,
        )

    def test_convert_allows_same_name_when_identity_signals_differ(self):
        first_prospect = Prospect.objects.create(
            contact=Contact.objects.create(
                first_name="Ava",
                last_name="Johnson",
                email="ava.one@example.com",
                phone_number="+1 (555) 000-1111",
            ),
        )
        Student.objects.create(prospect=first_prospect)

        coincidental_name_match = Prospect.objects.create(
            contact=Contact.objects.create(
                first_name="Ava",
                last_name="Johnson",
                email="ava.two@example.com",
                phone_number="+1 (555) 999-8888",
            ),
        )

        expected_student, _ = make_prospect_financially_eligible(coincidental_name_match)
        student, created = coincidental_name_match.convert_to_student()
        self.assertFalse(created)
        self.assertEqual(student.pk, expected_student.pk)
        self.assertEqual(
            Student.objects.filter(
                prospect__contact__first_name__iexact="Ava",
                prospect__contact__last_name__iexact="Johnson",
            ).count(),
            2,
        )


class ContactListSearchTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="contact_viewer",
            password="safe-password-123",
        )
        self.client.force_login(self.user)

        self.amara = Contact.objects.create(
            first_name="Amara",
            last_name="Anderson",
            email="amara@example.com",
            phone_number="+1-555-111-2222",
        )
        self.other = Contact.objects.create(
            first_name="Kojo",
            last_name="Mensah",
            email="kojo@example.com",
            phone_number="+1-555-333-4444",
        )

    def test_contact_list_search_filters_by_name(self):
        response = self.client.get(reverse("core:contact-list"), {"q": "amara"})
        self.assertEqual(response.status_code, 200)
        object_list = list(response.context["object_list"])
        self.assertEqual(object_list, [self.amara])

    def test_contact_list_search_filters_by_id(self):
        response = self.client.get(reverse("core:contact-list"), {"q": str(self.other.pk)})
        self.assertEqual(response.status_code, 200)
        object_list = list(response.context["object_list"])
        self.assertEqual(object_list, [self.other])

    def test_contact_list_search_empty_state_message(self):
        response = self.client.get(reverse("core:contact-list"), {"q": "does-not-exist"})
        self.assertContains(response, "No contacts found for this search.")

    def test_contact_list_is_paginated(self):
        for index in range(30):
            Contact.objects.create(
                first_name=f"Bulk{index}",
                last_name="Contact",
                email=f"bulk{index}@example.com",
            )
        response = self.client.get(reverse("core:contact-list"), {"page": 2})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["is_paginated"])
        self.assertEqual(response.context["page_obj"].number, 2)


class ContactProspectConversionWorkflowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="contact_converter",
            password="safe-password-123",
        )
        self.client.force_login(self.user)
        self.contact = Contact.objects.create(
            first_name="Efua",
            last_name="Agyeman",
            email="efua@example.com",
            phone_number="+1-555-771-0099",
        )

    def test_contact_list_shows_convert_to_prospect_action(self):
        response = self.client.get(reverse("core:contact-list"))
        self.assertContains(
            response,
            reverse("core:contact-convert-to-prospect", kwargs={"pk": self.contact.pk}),
        )
        self.assertContains(response, "Convert to Prospect")

    def test_contact_detail_shows_convert_to_prospect_action(self):
        response = self.client.get(reverse("core:contact-detail", kwargs={"pk": self.contact.pk}))
        self.assertContains(
            response,
            reverse("core:contact-convert-to-prospect", kwargs={"pk": self.contact.pk}),
        )

    def test_conversion_form_is_prefilled_from_contact(self):
        response = self.client.get(
            reverse("core:contact-convert-to-prospect", kwargs={"pk": self.contact.pk}),
        )
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(form.initial.get("contact_first_name"), "Efua")
        self.assertEqual(form.initial.get("contact_last_name"), "Agyeman")
        self.assertEqual(form.initial.get("contact_email"), "efua@example.com")
        self.assertEqual(form.initial.get("contact_phone_number"), "+1-555-771-0099")

    def test_convert_contact_to_prospect_creates_linked_record(self):
        response = self.client.post(
            reverse("core:contact-convert-to-prospect", kwargs={"pk": self.contact.pk}),
            data={
                "contact_first_name": "Efua",
                "contact_last_name": "Agyeman",
                "contact_email": "efua@example.com",
                "contact_phone_number": "+1-555-771-0099",
                "preferred_contact_method": "email",
                "source": "Referral",
                "status": ProspectStatus.NEW,
                "teacher": "",
                "interest_level": "medium",
                "notes": "Interested in starter course",
            },
        )
        self.assertEqual(response.status_code, 302)
        prospect = Prospect.objects.get(contact=self.contact)
        self.assertRedirects(response, reverse("core:prospect-detail", kwargs={"pk": prospect.pk}))
        self.assertEqual(prospect.owner, self.user)
        self.assertEqual(prospect.status, ProspectStatus.NEW)
        self.assertIsNotNone(prospect.created_at)
        self.assertEqual(prospect.source, "Referral")
        self.assertEqual(prospect.notes, "Interested in starter course")
        self.contact.refresh_from_db()
        self.assertTrue(self.contact.has_converted_prospect)
        self.assertEqual(self.contact.prospect.pk, prospect.pk)

    def test_convert_contact_to_prospect_is_idempotent(self):
        Prospect.objects.create(owner=self.user, contact=self.contact, status=ProspectStatus.NEW)
        response = self.client.get(
            reverse("core:contact-convert-to-prospect", kwargs={"pk": self.contact.pk}),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Prospect.objects.filter(contact=self.contact).count(), 1)
        self.contact.refresh_from_db()
        self.assertTrue(self.contact.has_converted_prospect)

    def test_converted_contact_shows_open_prospect_action(self):
        prospect = Prospect.objects.create(owner=self.user, contact=self.contact, status=ProspectStatus.NEW)
        response = self.client.get(reverse("core:contact-list"))
        self.assertContains(response, str(self.contact))
        self.assertContains(response, reverse("core:contact-detail", kwargs={"pk": self.contact.pk}))
        self.assertNotContains(response, "Converted to Prospect")
        self.assertNotContains(response, "Open Prospect")
        self.assertNotContains(response, "Convert to Prospect")

    def test_unauthorized_user_cannot_convert_contact(self):
        self.client.logout()
        response = self.client.get(reverse("core:contact-convert-to-prospect", kwargs={"pk": self.contact.pk}))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)


class ProspectQuickMessageWorkflowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="prospect_ops",
            password="safe-password-123",
        )
        self.client.force_login(self.user)
        self.prospect = Prospect.objects.create(
            owner=self.user,
            contact=Contact.objects.create(
                first_name="Amara",
                last_name="Boateng",
                email="amara.bo@example.com",
                phone_number="+1-555-212-0000",
            ),
        )
        self.student, self.invoice = make_prospect_financially_eligible(
            self.prospect,
            user=self.user,
        )

    def test_prospect_list_shows_send_message_action(self):
        response = self.client.get(reverse("core:prospect-list"))
        self.assertContains(
            response,
            f'{reverse("core:communication-create")}?recipient_type=prospect&prospect={self.prospect.pk}',
        )

    def test_communication_create_prefills_selected_prospect(self):
        response = self.client.get(
            reverse("core:communication-create"),
            {"prospect": self.prospect.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].initial.get("prospect"), self.prospect.pk)
        self.assertEqual(response.context["form"].initial.get("recipient_type"), "prospect")
        self.assertContains(response, "Recipient Preselected")
        self.assertContains(response, "amara.bo@example.com")

    def test_contact_attempt_count_updates_after_message(self):
        self.assertEqual(self.prospect.contact_attempt_count, 0)
        Communication.objects.create(
            owner=self.user,
            recipient_type="prospect",
            prospect=self.prospect,
            channel="email",
            communication_type="follow_up",
            subject="Follow up",
            body="Checking in.",
            delivery_status="sent",
        )
        self.prospect.refresh_from_db()
        self.assertEqual(self.prospect.contact_attempt_count, 1)

    def test_prospect_list_shows_convert_to_student_action(self):
        response = self.client.get(reverse("core:prospect-list"))
        self.assertContains(
            response,
            reverse("core:prospect-convert-to-student", kwargs={"pk": self.prospect.pk}),
        )

    def test_prospect_list_convert_creates_student(self):
        response = self.client.post(
            reverse("core:prospect-convert-to-student", kwargs={"pk": self.prospect.pk}),
            data={"next": reverse("core:prospect-list")},
        )
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(
            response,
            reverse("core:student-detail", kwargs={"pk": self.prospect.student_record.pk}),
        )
        self.prospect.refresh_from_db()
        self.assertTrue(hasattr(self.prospect, "student_record"))
        self.assertEqual(self.prospect.status, ProspectStatus.CONVERTED)
        self.assertTrue(self.prospect.converted_to_student)
        self.assertIsNotNone(self.prospect.converted_at)
        self.assertEqual(self.prospect.converted_student_id, self.prospect.student_record.pk)


class ProspectConversionFinancialRequirementTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="financial_gate_user",
            password="safe-password-123",
        )
        self.client.force_login(self.user)
        self.prospect = Prospect.objects.create(
            owner=self.user,
            contact=Contact.objects.create(
                first_name="Financial",
                last_name="Prospect",
                email="financial.prospect@example.com",
                phone_number="+233-555-0101",
            ),
            status=ProspectStatus.NEW,
        )
        self.teacher = Teacher.objects.create(
            first_name="Finance",
            last_name="Teacher",
            email="finance.teacher@example.com",
        )
        self.location = Location.objects.create(name="Financial Gate Center")
        self.course = Course.objects.create(
            name="Financial Gate TM Course",
            standard_fee=Decimal("500.00"),
        )
        self.session = CourseSession.objects.create(
            owner=self.user,
            course=self.course,
            teacher=self.teacher,
            session_name="Financial Gate Session",
            start_date=timezone.now() + timedelta(days=2),
            end_date=timezone.now() + timedelta(days=3),
            location=self.location,
            status=SessionStatus.SCHEDULED,
        )

    def _issue_invoice(self, *, status=InvoiceStatus.SENT, total=Decimal("500.00")):
        student, _ = Student.objects.get_or_create(
            prospect=self.prospect,
            defaults={"owner": self.user},
        )
        enrollment, _ = Enrollment.objects.get_or_create(
            student=student,
            session=self.session,
            defaults={
                "course": self.course,
                "enrollment_date": timezone.now(),
                "fee_amount": total,
                "discount_amount": Decimal("0.00"),
            },
        )
        invoice, _ = Invoice.objects.get_or_create(
            enrollment=enrollment,
            defaults={
                "owner": self.user,
                "invoice_number": f"FIN-GATE-{self.prospect.pk}",
                "issue_date": timezone.localdate(),
                "due_date": timezone.localdate() + timedelta(days=14),
                "subtotal": total,
                "discount_amount": Decimal("0.00"),
                "tax_amount": Decimal("0.00"),
                "total_amount": total,
                "status": status,
            },
        )
        if invoice.status != status:
            invoice.status = status
            invoice.save(update_fields=["status", "updated_at"])
        return invoice, student

    def _record_payment(self, invoice, amount, *, confirmation_status=PaymentConfirmationStatus.CONFIRMED):
        return Payment.objects.create(
            owner=self.user,
            invoice=invoice,
            payment_date=timezone.now(),
            amount_paid=amount,
            payment_method="transfer",
            confirmation_status=confirmation_status,
        )

    def _assert_not_converted(self, *, expected_student_count):
        self.prospect.refresh_from_db()
        self.assertEqual(self.prospect.status, ProspectStatus.NEW)
        self.assertFalse(self.prospect.converted_to_student)
        self.assertIsNone(self.prospect.converted_student_id)
        self.assertIsNone(self.prospect.converted_at)
        self.assertEqual(Student.objects.filter(prospect=self.prospect).count(), expected_student_count)

    def test_prospect_with_no_invoice_is_rejected(self):
        response = self.client.post(
            reverse("core:prospect-convert-to-student", kwargs={"pk": self.prospect.pk}),
            data={"next": reverse("core:prospect-list")},
            follow=True,
        )

        self.assertContains(response, "Cannot convert this prospect: no donation statement has been issued.")
        self._assert_not_converted(expected_student_count=0)

    @patch("core.services.invoicing.send_invoice_email", return_value=True)
    @patch("core.services.invoicing._generate_and_save_invoice_pdf", return_value=True)
    def test_enrollment_creates_invoice_shell_without_premature_conversion(
        self,
        _pdf_mock,
        _email_mock,
    ):
        response = self.client.post(
            reverse("core:enrollment-create"),
            data={
                "person_type": "prospect",
                "prospect": self.prospect.pk,
                "student": "",
                "contact": "",
                "course": self.course.pk,
                "session": self.session.pk,
                "enrollment_date": timezone.localdate().isoformat(),
                "status": EnrollmentStatus.ENROLLED,
                "fee_amount": "500.00",
                "discount_amount": "0.00",
                "number_of_children_under_18": "0",
                "balance_due": "500.00",
                "notes": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        student = Student.objects.get(prospect=self.prospect)
        invoice = Invoice.objects.get(enrollment__student=student)
        self.prospect.refresh_from_db()
        self.assertEqual(self.prospect.status, ProspectStatus.NEW)
        self.assertFalse(self.prospect.converted_to_student)
        self.assertIsNone(self.prospect.converted_student_id)
        self.assertIsNone(self.prospect.converted_at)
        self.assertEqual(invoice.status, InvoiceStatus.SENT)

        self._record_payment(invoice, Decimal("500.00"))
        converted_student, created = self.prospect.convert_to_student()

        self.assertFalse(created)
        self.assertEqual(converted_student.pk, student.pk)
        self.prospect.refresh_from_db()
        self.assertEqual(self.prospect.status, ProspectStatus.CONVERTED)
        self.assertTrue(self.prospect.converted_to_student)

    def test_unissued_invoice_is_rejected(self):
        self._issue_invoice(status=InvoiceStatus.DRAFT)
        response = self.client.post(
            reverse("core:prospect-convert-to-student", kwargs={"pk": self.prospect.pk}),
            data={"next": reverse("core:prospect-list")},
            follow=True,
        )

        self.assertContains(response, "Cannot convert this prospect: the donation statement has not been issued.")
        self._assert_not_converted(expected_student_count=1)

    def test_issued_invoice_without_payment_is_rejected(self):
        self._issue_invoice()
        response = self.client.post(
            reverse("core:prospect-convert-to-student", kwargs={"pk": self.prospect.pk}),
            data={"next": reverse("core:prospect-list")},
            follow=True,
        )

        self.assertContains(response, "Cannot convert this prospect: payment has not been received.")
        self._assert_not_converted(expected_student_count=1)

    def test_pending_payment_does_not_count_as_received(self):
        invoice, _ = self._issue_invoice()
        self._record_payment(
            invoice,
            Decimal("500.00"),
            confirmation_status=PaymentConfirmationStatus.PENDING,
        )
        response = self.client.post(
            reverse("core:prospect-convert-to-student", kwargs={"pk": self.prospect.pk}),
            follow=True,
        )

        self.assertContains(response, "Cannot convert this prospect: payment has not been received.")
        self._assert_not_converted(expected_student_count=1)

    def test_partial_payment_is_rejected_with_outstanding_balance(self):
        invoice, _ = self._issue_invoice()
        self._record_payment(invoice, Decimal("250.00"))
        response = self.client.post(
            reverse("core:prospect-convert-to-student", kwargs={"pk": self.prospect.pk}),
            data={"next": reverse("core:prospect-list")},
            follow=True,
        )

        self.assertContains(
            response,
            "Cannot convert this prospect: the donation statement has an outstanding balance of ₦250.00.",
        )
        self._assert_not_converted(expected_student_count=1)

    def test_fully_paid_invoice_allows_conversion(self):
        invoice, student = self._issue_invoice()
        self._record_payment(invoice, Decimal("500.00"))
        response = self.client.post(
            reverse("core:prospect-convert-to-student", kwargs={"pk": self.prospect.pk}),
            data={"next": reverse("core:prospect-list")},
        )

        self.assertRedirects(response, reverse("core:student-detail", kwargs={"pk": student.pk}))
        self.prospect.refresh_from_db()
        self.assertEqual(self.prospect.status, ProspectStatus.CONVERTED)
        self.assertTrue(self.prospect.converted_to_student)
        self.assertEqual(self.prospect.converted_student_id, student.pk)
        self.assertEqual(Student.objects.filter(prospect=self.prospect).count(), 1)

    def test_fully_paid_reconversion_reactivates_historical_student(self):
        invoice, student = self._issue_invoice()
        Student.objects.filter(pk=student.pk).update(
            enrollment_status=EnrollmentStatus.INACTIVE
        )
        self._record_payment(invoice, Decimal("500.00"))

        self.prospect.convert_to_student()

        student.refresh_from_db()
        self.assertEqual(student.enrollment_status, EnrollmentStatus.PENDING)
        self.assertEqual(Student.objects.filter(prospect=self.prospect).count(), 1)

    def test_direct_pipeline_post_without_payment_is_rejected(self):
        self._issue_invoice()
        response = self.client.post(
            reverse("core:prospect-pipeline-convert", kwargs={"pk": self.prospect.pk}),
            follow=True,
        )

        self.assertContains(response, "Cannot convert this prospect: payment has not been received.")
        self._assert_not_converted(expected_student_count=1)

    def test_repeated_failed_attempt_does_not_convert_or_duplicate(self):
        for _ in range(2):
            response = self.client.post(
                reverse("core:prospect-convert-to-student", kwargs={"pk": self.prospect.pk}),
                data={"next": reverse("core:prospect-list")},
            )
            self.assertEqual(response.status_code, 302)

        self._assert_not_converted(expected_student_count=0)

    def test_prospect_list_disables_conversion_when_payment_is_required(self):
        response = self.client.get(reverse("core:prospect-list"))
        self.assertContains(response, "Invoice/payment required")
        self.assertNotContains(
            response,
            f'action="{reverse("core:prospect-convert-to-student", kwargs={"pk": self.prospect.pk})}"',
        )

    def test_prospect_list_shows_conversion_action_when_fully_paid(self):
        invoice, _ = self._issue_invoice()
        self._record_payment(invoice, Decimal("500.00"))
        response = self.client.get(reverse("core:prospect-list"))
        self.assertContains(
            response,
            f'action="{reverse("core:prospect-convert-to-student", kwargs={"pk": self.prospect.pk})}"',
        )


class CleanupInvalidStudentsCommandTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="cleanup_command_user",
            password="safe-password-123",
        )

    def _invalid_student_without_dependencies(self, suffix="one"):
        prospect = Prospect.objects.create(
            owner=self.user,
            contact=Contact.objects.create(
                first_name="Cleanup",
                last_name=suffix.title(),
                email=f"cleanup.{suffix}@example.com",
            ),
            status=ProspectStatus.CONVERTED,
            converted_to_student=True,
        )
        student = Student.objects.create(owner=self.user, prospect=prospect)
        prospect.converted_student = student
        prospect.converted_at = timezone.now()
        prospect.save(
            update_fields=["converted_student", "converted_at", "updated_at"]
        )
        return prospect, student

    def test_dry_run_reports_identifiers_financials_reason_and_does_not_modify(self):
        prospect, student = self._invalid_student_without_dependencies()
        output = StringIO()

        call_command(
            "cleanup_invalid_students",
            "--dry-run",
            "--student-id",
            str(student.pk),
            stdout=output,
        )

        rendered = output.getvalue()
        self.assertIn(f"Student ID={student.pk}", rendered)
        self.assertIn(f"UUID={student.uuid}", rendered)
        self.assertIn(f"Prospect ID={prospect.pk}", rendered)
        self.assertIn(f"Contact ID={prospect.contact_id}", rendered)
        self.assertIn("Invoice ID=-", rendered)
        self.assertIn("Amount=₦0.00", rendered)
        self.assertIn("Total payment received at conversion=₦0.00", rendered)
        self.assertIn("Outstanding at conversion=₦0.00", rendered)
        self.assertIn("Violation=no_invoice", rendered)
        self.assertIn("Dry run only: no database records were modified.", rendered)
        self.assertTrue(Student.objects.filter(pk=student.pk).exists())
        prospect.refresh_from_db()
        self.assertTrue(prospect.converted_to_student)

    def test_command_defaults_to_dry_run(self):
        _, student = self._invalid_student_without_dependencies("default")
        output = StringIO()

        call_command(
            "cleanup_invalid_students",
            "--student-id",
            str(student.pk),
            stdout=output,
        )

        self.assertIn("Mode: DRY RUN", output.getvalue())
        self.assertTrue(Student.objects.filter(pk=student.pk).exists())

    def test_verify_passes_when_active_converted_student_is_fully_paid(self):
        prospect, student = self._invalid_student_without_dependencies("verified")
        make_prospect_financially_eligible(prospect, user=self.user)
        output = StringIO()

        call_command("cleanup_invalid_students", "--verify", stdout=output)

        rendered = output.getvalue()
        self.assertIn("Active converted Students: 1", rendered)
        self.assertIn("Financially eligible active Students: 1", rendered)
        self.assertIn("Missing required invoice: 0", rendered)
        self.assertIn("Unpaid/outstanding invoice: 0", rendered)
        self.assertIn("VERIFICATION PASSED", rendered)
        self.assertTrue(Student.objects.filter(pk=student.pk).exists())

    def test_verify_fails_read_only_for_active_student_without_invoice(self):
        prospect, student = self._invalid_student_without_dependencies(
            "verify-failure"
        )
        output = StringIO()

        with self.assertRaisesMessage(CommandError, "VERIFICATION FAILED"):
            call_command("cleanup_invalid_students", "--verify", stdout=output)

        rendered = output.getvalue()
        self.assertIn("Missing required invoice: 1", rendered)
        self.assertIn(f"Student ID={student.pk}", rendered)
        self.assertTrue(Student.objects.filter(pk=student.pk).exists())
        prospect.refresh_from_db()
        self.assertTrue(prospect.converted_to_student)

    def test_dry_run_does_not_target_preconversion_student_shell(self):
        prospect = Prospect.objects.create(
            owner=self.user,
            contact=Contact.objects.create(
                first_name="Preconversion",
                last_name="Student",
                email="preconversion.student@example.com",
            ),
            status=ProspectStatus.QUALIFIED,
        )
        student = Student.objects.create(owner=self.user, prospect=prospect)
        output = StringIO()

        call_command(
            "cleanup_invalid_students",
            "--dry-run",
            "--student-id",
            str(student.pk),
            stdout=output,
        )

        rendered = output.getvalue()
        self.assertIn("Not marked as converted: 1", rendered)
        self.assertIn("Violating: 0", rendered)
        self.assertNotIn(f"Student ID={student.pk}", rendered)
        self.assertTrue(Student.objects.filter(pk=student.pk).exists())

    def test_dry_run_reports_invoice_payment_balance_and_violation_reason(self):
        prospect, _ = self._invalid_student_without_dependencies("partial")
        student, invoice = make_prospect_financially_eligible(
            prospect,
            user=self.user,
        )
        payment = invoice.payments.get()
        payment.amount_paid = Decimal("125.00")
        payment.save(update_fields=["amount_paid", "updated_at"])
        prospect.converted_at = timezone.now()
        prospect.save(update_fields=["converted_at", "updated_at"])
        output = StringIO()

        call_command(
            "cleanup_invalid_students",
            "--dry-run",
            "--student-id",
            str(student.pk),
            stdout=output,
        )

        rendered = output.getvalue()
        self.assertIn(f"Invoice ID={invoice.pk}", rendered)
        self.assertIn("Amount=₦500.00", rendered)
        self.assertIn("Total payment received at conversion=₦125.00", rendered)
        self.assertIn("Outstanding at conversion=₦375.00", rendered)
        self.assertIn("Violation=partial_or_outstanding", rendered)
        self.assertIn("outstanding balance at conversion was ₦375.00", rendered)
        self.assertTrue(Student.objects.filter(pk=student.pk).exists())

    def test_execute_requires_explicit_student_ids(self):
        self._invalid_student_without_dependencies("guarded")
        with self.assertRaises(CommandError):
            call_command(
                "cleanup_invalid_students",
                "--execute",
                "--confirm-count",
                "1",
            )

    def test_execute_requires_exact_confirmation_count(self):
        _, student = self._invalid_student_without_dependencies("count")
        with self.assertRaises(CommandError):
            call_command(
                "cleanup_invalid_students",
                "--execute",
                "--student-id",
                str(student.pk),
                "--confirm-count",
                "2",
            )

    def test_integrity_error_rolls_back_entire_selected_cleanup(self):
        first_prospect, first_student = self._invalid_student_without_dependencies(
            "rollback-first"
        )
        second_prospect, second_student = self._invalid_student_without_dependencies(
            "rollback-second"
        )
        from core.management.commands import cleanup_invalid_students as command_module

        real_cleanup = command_module.safely_revert_invalid_student
        calls = 0

        def fail_on_second(student_id, *, revert_status):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise IntegrityError("simulated dependent integrity failure")
            return real_cleanup(student_id, revert_status=revert_status)

        with patch.object(
            command_module,
            "safely_revert_invalid_student",
            side_effect=fail_on_second,
        ):
            output = StringIO()
            with self.assertRaisesMessage(
                CommandError,
                "entire cleanup transaction was rolled back",
            ):
                call_command(
                    "cleanup_invalid_students",
                    "--execute",
                    "--student-id",
                    str(first_student.pk),
                    "--student-id",
                    str(second_student.pk),
                    "--confirm-count",
                    "2",
                    stdout=output,
                )

        self.assertTrue(Student.objects.filter(pk=first_student.pk).exists())
        self.assertTrue(Student.objects.filter(pk=second_student.pk).exists())
        first_prospect.refresh_from_db()
        second_prospect.refresh_from_db()
        self.assertTrue(first_prospect.converted_to_student)
        self.assertTrue(second_prospect.converted_to_student)
        self.assertEqual(first_prospect.status, ProspectStatus.CONVERTED)
        self.assertEqual(second_prospect.status, ProspectStatus.CONVERTED)

    def test_execute_reverts_reviewed_dependency_free_student(self):
        prospect, student = self._invalid_student_without_dependencies("safe")
        contact_id = prospect.contact_id
        Prospect.objects.filter(pk=prospect.pk).update(is_archived=True)
        output = StringIO()

        call_command(
            "cleanup_invalid_students",
            "--execute",
            "--student-id",
            str(student.pk),
            "--confirm-count",
            "1",
            stdout=output,
        )

        self.assertFalse(Student.objects.filter(pk=student.pk).exists())
        self.assertTrue(Contact.objects.filter(pk=contact_id).exists())
        prospect.refresh_from_db()
        self.assertEqual(prospect.status, ProspectStatus.QUALIFIED)
        self.assertFalse(prospect.is_archived)
        self.assertFalse(prospect.converted_to_student)
        self.assertIsNone(prospect.converted_student_id)
        self.assertIsNone(prospect.converted_at)
        self.assertEqual(Contact.objects.filter(pk=contact_id).count(), 1)
        self.assertEqual(Prospect.objects.filter(contact_id=contact_id).count(), 1)
        rendered = output.getvalue()
        self.assertIn(f"REUSE Contact #{contact_id}", rendered)
        self.assertIn(f"REACTIVATE existing Prospect #{prospect.pk}", rendered)
        self.assertIn("empty Student shell removed", rendered)
        self.assertIn("no Contact or Prospect created", rendered)
        self.assertIn("Contact, Prospect, donation statement/invoice", rendered)

    def test_execute_restores_prospect_and_preserves_student_financial_history(self):
        prospect, _ = self._invalid_student_without_dependencies("dependent")
        student, invoice = make_prospect_financially_eligible(
            prospect,
            user=self.user,
        )
        payment = invoice.payments.get()
        payment.amount_paid = Decimal("250.00")
        payment.save(update_fields=["amount_paid", "updated_at"])
        student.notes = "Legitimate historical Student note."
        student.save(update_fields=["notes", "updated_at"])
        communication = Communication.objects.create(
            owner=self.user,
            recipient_type="student",
            student=student,
            enrollment=invoice.enrollment,
            channel="email",
            communication_type="follow_up",
            subject="Day 3 check-in",
            body="Historical check-in communication.",
            sent_at=timezone.now(),
            delivery_status="sent",
            notes="Preserve this communication note.",
        )
        prospect.converted_at = timezone.now()
        prospect.save(update_fields=["converted_at", "updated_at"])
        output = StringIO()

        call_command(
            "cleanup_invalid_students",
            "--execute",
            "--student-id",
            str(student.pk),
            "--confirm-count",
            "1",
            stdout=output,
        )

        self.assertTrue(Student.objects.filter(pk=student.pk).exists())
        student.refresh_from_db()
        self.assertEqual(student.enrollment_status, EnrollmentStatus.INACTIVE)
        self.assertTrue(Enrollment.objects.filter(student=student).exists())
        self.assertTrue(Invoice.objects.filter(pk=invoice.pk).exists())
        self.assertTrue(Payment.objects.filter(pk=payment.pk).exists())
        self.assertTrue(CourseSession.objects.filter(pk=invoice.enrollment.session_id).exists())
        communication.refresh_from_db()
        self.assertEqual(communication.student_id, student.pk)
        self.assertIsNone(communication.prospect_id)
        self.assertEqual(communication.recipient_type, "student")
        self.assertEqual(communication.enrollment_id, invoice.enrollment_id)
        prospect.refresh_from_db()
        self.assertEqual(prospect.status, ProspectStatus.QUALIFIED)
        self.assertFalse(prospect.is_archived)
        self.assertFalse(prospect.converted_to_student)
        self.assertIsNone(prospect.converted_student_id)
        self.assertIsNone(prospect.converted_at)
        self.assertIn("Legitimate historical Student note.", prospect.notes)
        self.assertEqual(Contact.objects.filter(pk=prospect.contact_id).count(), 1)
        self.assertEqual(Prospect.objects.filter(contact_id=prospect.contact_id).count(), 1)
        self.assertIn(f"Existing Prospect #{prospect.pk} restored", output.getvalue())
        self.assertIn("no Contact or Prospect created", output.getvalue())
        self.assertIn("'donation_statements_invoices': 1", output.getvalue())
        self.assertIn("'payments': 1", output.getvalue())
        self.assertIn("'attendance_check_ins': 1", output.getvalue())
        self.assertIn("retain the Student as inactive historical parent", output.getvalue())
        self.assertIn(
            f"DonationStatement/Invoice ID={invoice.pk} | "
            "Action=PRESERVE as accounting history",
            output.getvalue(),
        )
        self.assertIn(
            f"Payment ID={payment.pk} | Action=PRESERVE as accounting history",
            output.getvalue(),
        )
        self.assertIn(
            f"Attendance/Check-in Communication ID={communication.pk} | "
            "Action=PRESERVE original Student link",
            output.getvalue(),
        )

        second_output = StringIO()
        call_command(
            "cleanup_invalid_students",
            "--dry-run",
            "--student-id",
            str(student.pk),
            stdout=second_output,
        )
        self.assertIn("Violating: 0", second_output.getvalue())
        self.assertIn("Already restored to Prospect: 1", second_output.getvalue())

    def test_execute_reassigns_communication_before_removing_empty_student_shell(self):
        prospect, student = self._invalid_student_without_dependencies("communication")
        communication = Communication.objects.create(
            owner=self.user,
            recipient_type="student",
            student=student,
            channel="email",
            communication_type="general",
            subject="Conversion-only Student communication",
            body="Preserve this on the restored Prospect.",
            delivery_status="sent",
        )
        output = StringIO()

        call_command(
            "cleanup_invalid_students",
            "--execute",
            "--student-id",
            str(student.pk),
            "--confirm-count",
            "1",
            stdout=output,
        )

        self.assertFalse(Student.objects.filter(pk=student.pk).exists())
        communication.refresh_from_db()
        self.assertIsNone(communication.student_id)
        self.assertEqual(communication.prospect_id, prospect.pk)
        self.assertEqual(communication.recipient_type, "prospect")
        self.assertIn("preserve and reassign communications", output.getvalue())
        self.assertIn(
            f"Communication ID={communication.pk} | Action=REASSIGN to "
            "existing Prospect before Student removal",
            output.getvalue(),
        )

    def test_meditator_lifecycle_is_flagged_invalidated_and_preserved_for_audit(self):
        prospect, student = self._invalid_student_without_dependencies("meditator")
        meditator = Meditator.objects.create(
            student=student,
            metadata={"original_transition_evidence": "preserve"},
        )
        event = MeditatorTransitionEvent.objects.create(
            student=student,
            meditator=meditator,
            metadata={"event_evidence": "preserve"},
        )
        dry_run = StringIO()

        call_command(
            "cleanup_invalid_students",
            "--dry-run",
            "--student-id",
            str(student.pk),
            stdout=dry_run,
        )

        rendered = dry_run.getvalue()
        self.assertIn("SERIOUS INTEGRITY VIOLATION", rendered)
        self.assertIn(f"Meditator ID={meditator.pk}", rendered)
        self.assertIn("INVALIDATE active lifecycle", rendered)
        self.assertTrue(Meditator.objects.get(pk=meditator.pk).is_active)

        executed = StringIO()
        call_command(
            "cleanup_invalid_students",
            "--execute",
            "--student-id",
            str(student.pk),
            "--confirm-count",
            "1",
            stdout=executed,
        )

        student.refresh_from_db()
        self.assertEqual(student.enrollment_status, EnrollmentStatus.INACTIVE)
        meditator.refresh_from_db()
        self.assertFalse(meditator.is_active)
        self.assertIsNotNone(meditator.invalidated_at)
        self.assertIn("Student lifecycle was invalid", meditator.invalidation_reason)
        self.assertEqual(
            meditator.metadata["original_transition_evidence"],
            "preserve",
        )
        self.assertEqual(len(meditator.metadata["integrity_invalidations"]), 1)
        self.assertTrue(MeditatorTransitionEvent.objects.filter(pk=event.pk).exists())
        self.assertEqual(
            MeditatorTransitionEvent.objects.get(pk=event.pk).metadata["event_evidence"],
            "preserve",
        )
        prospect.refresh_from_db()
        self.assertEqual(prospect.status, ProspectStatus.QUALIFIED)
        self.assertIn("Active Meditator lifecycle invalidated", executed.getvalue())

        self.client.force_login(self.user)
        response = self.client.get(reverse("core:meditator-list"))
        self.assertNotContains(response, meditator.public_id)

class ProspectConversionStateVisibilityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="prospect_conversion_state_user",
            password="safe-password-123",
        )
        self.client.force_login(self.user)
        self.prospect = Prospect.objects.create(
            owner=self.user,
            contact=Contact.objects.create(
                first_name="Yaw",
                last_name="Boateng",
                email="yaw.boateng@example.com",
                phone_number="+1-555-771-2222",
            ),
            status=ProspectStatus.NEW,
            notes="Ready for conversion",
        )
        self.student, self.invoice = make_prospect_financially_eligible(
            self.prospect,
            user=self.user,
        )

    def test_default_prospect_list_hides_converted(self):
        self.client.post(reverse("core:prospect-convert-to-student", kwargs={"pk": self.prospect.pk}))
        response = self.client.get(reverse("core:prospect-list"))
        self.assertNotContains(response, "Yaw Boateng")

    def test_staff_can_view_converted_filter(self):
        self.client.post(reverse("core:prospect-convert-to-student", kwargs={"pk": self.prospect.pk}))
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        response = self.client.get(reverse("core:prospect-list"), {"state": "converted"})
        self.assertContains(response, "Yaw Boateng")

    def test_duplicate_conversion_returns_existing_student(self):
        first_response = self.client.post(
            reverse("core:prospect-convert-to-student", kwargs={"pk": self.prospect.pk})
        )
        self.assertEqual(first_response.status_code, 302)
        first_student_id = self.prospect.student_record.pk

        second_response = self.client.post(
            reverse("core:prospect-convert-to-student", kwargs={"pk": self.prospect.pk})
        )
        self.assertEqual(second_response.status_code, 302)
        self.prospect.refresh_from_db()
        self.assertEqual(self.prospect.student_record.pk, first_student_id)
        self.assertEqual(Student.objects.filter(prospect=self.prospect).count(), 1)

    def test_converted_state_hides_convert_button_and_shows_open_student(self):
        self.client.post(reverse("core:prospect-convert-to-student", kwargs={"pk": self.prospect.pk}))
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        response = self.client.get(reverse("core:prospect-list"), {"state": "converted"})
        self.assertNotContains(response, "Convert to Student")
        self.assertContains(response, "Open Student")

    def test_active_filter_excludes_status_converted_even_if_flags_missing(self):
        self.prospect.status = ProspectStatus.CONVERTED
        self.prospect.converted_to_student = False
        self.prospect.converted_student = None
        self.prospect.save(
            update_fields=["status", "converted_to_student", "converted_student", "updated_at"]
        )
        response = self.client.get(reverse("core:prospect-list"))
        self.assertNotContains(response, "Yaw Boateng")

    def test_converted_filter_includes_status_converted_even_if_flags_missing(self):
        self.prospect.status = ProspectStatus.CONVERTED
        self.prospect.converted_to_student = False
        self.prospect.converted_student = None
        self.prospect.save(
            update_fields=["status", "converted_to_student", "converted_student", "updated_at"]
        )
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        response = self.client.get(reverse("core:prospect-list"), {"state": "converted"})
        self.assertContains(response, "Yaw Boateng")


class ProspectEditPreservesCanonicalContactTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="prospect_editor",
            password="safe-password-123",
        )
        self.client.force_login(self.user)
        self.contact = Contact.objects.create(
            first_name="Kofi",
            last_name="Asare",
            email="kofi.old@example.com",
            phone_number="+1-555-444-0000",
        )
        self.prospect = Prospect.objects.create(
            owner=self.user,
            contact=self.contact,
            status=ProspectStatus.NEW,
        )

    def test_prospect_edit_cannot_update_linked_contact_email(self):
        contact_uuid = self.contact.uuid
        prospect_uuid = self.prospect.uuid
        response = self.client.post(
            reverse("core:prospect-update", kwargs={"pk": self.prospect.pk}),
            data={
                "contact": self.contact.pk,
                "contact_first_name": "Kofi",
                "contact_last_name": "Asare",
                "contact_email": "kofi.new@example.com",
                "contact_phone_number": "+1-555-444-0000",
                "preferred_contact_method": "email",
                "source": "Referral",
                "status": ProspectStatus.NEW,
                "teacher": "",
                "interest_level": "",
                "notes": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.contact.refresh_from_db()
        self.prospect.refresh_from_db()
        self.assertEqual(self.contact.email, "kofi.old@example.com")
        self.assertEqual(self.contact.uuid, contact_uuid)
        self.assertEqual(self.prospect.uuid, prospect_uuid)
        self.assertEqual(self.prospect.source, "Referral")


class ProspectCreatedAtBehaviorTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="prospect_created_at_user",
            password="safe-password-123",
        )
        self.client.force_login(self.user)

    def test_create_form_does_not_render_created_at_input(self):
        response = self.client.get(reverse("core:prospect-create"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="created_at"')

    def test_update_form_does_not_render_created_at_input(self):
        prospect = Prospect.objects.create(
            owner=self.user,
            contact=Contact.objects.create(first_name="Ama", last_name="Serwaa"),
        )
        response = self.client.get(reverse("core:prospect-update", kwargs={"pk": prospect.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="created_at"')

    def test_created_at_is_system_generated_on_create(self):
        created = Prospect.objects.create(
            owner=self.user,
            contact=Contact.objects.create(
                first_name="Kojo",
                last_name="Mensah",
                email="kojo@example.com",
                phone_number="+1-555-123-1212",
            ),
            source="Website",
            status=ProspectStatus.NEW,
            interest_level="high",
            notes="Interested in intro class.",
        )
        self.assertIsNotNone(created.created_at)

    def test_prospect_create_auto_creates_contact_when_missing(self):
        response = self.client.post(
            reverse("core:prospect-create"),
            data={
                "existing_contact": "",
                "first_name": "Ama",
                "last_name": "Boateng",
                "email": "ama.boateng@example.com",
                "phone_number": "+1-555-998-1000",
                "preferred_contact_method": "email",
                "source": "Website",
                "status": ProspectStatus.NEW,
                "teacher": "",
                "interest_level": "medium",
                "notes": "Direct prospect creation",
            },
        )
        self.assertEqual(response.status_code, 302)
        prospect = Prospect.objects.get(contact__email="ama.boateng@example.com")
        self.assertEqual(prospect.contact.first_name, "Ama")
        self.assertEqual(prospect.contact.last_name, "Boateng")

    def test_prospect_create_links_existing_contact_by_email(self):
        contact = Contact.objects.create(
            first_name="Kwame",
            last_name="Asare",
            email="kwame.asare@example.com",
            phone_number="+1-555-888-1111",
        )
        response = self.client.post(
            reverse("core:prospect-create"),
            data={
                "existing_contact": "",
                "first_name": "Kwame",
                "last_name": "Asare",
                "email": "kwame.asare@example.com",
                "phone_number": "+1-555-777-2222",
                "preferred_contact_method": "email",
                "source": "Referral",
                "status": ProspectStatus.NEW,
                "teacher": "",
                "interest_level": "high",
                "notes": "Should link existing contact",
            },
        )
        self.assertEqual(response.status_code, 302)
        prospect = Prospect.objects.get(contact=contact)
        self.assertEqual(prospect.contact_id, contact.pk)
        self.assertEqual(Contact.objects.filter(email="kwame.asare@example.com").count(), 1)


class ContactAutocompleteEndpointTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="contact_autocomplete_user",
            password="safe-password-123",
        )
        self.client.force_login(self.user)
        Contact.objects.create(
            first_name="Amara",
            last_name="Anderson",
            email="amara@example.com",
            phone_number="+1-555-221-1000",
        )

    def test_contact_autocomplete_returns_expected_keys(self):
        response = self.client.get(reverse("core:contact-autocomplete"), {"q": "Am"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("results", payload)
        self.assertTrue(payload["results"])
        row = payload["results"][0]
        self.assertIn("id", row)
        self.assertIn("name", row)
        self.assertIn("email", row)
        self.assertIn("phone", row)

    def test_existing_contact_mode_requires_selected_contact(self):
        response = self.client.post(
            reverse("core:prospect-create"),
            data={
                "prospect_is_existing_contact": "on",
                "selected_contact": "",
                "preferred_contact_method": "email",
                "source": "Referral",
                "status": ProspectStatus.NEW,
                "interest_level": "high",
                "notes": "Existing contact mode",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select an existing contact.")

    def test_existing_contact_mode_does_not_overwrite_contact_details(self):
        contact = Contact.objects.create(
            first_name="Akosua",
            last_name="Mensah",
            email="akosua@example.com",
            phone_number="+1-555-987-0000",
        )
        response = self.client.post(
            reverse("core:prospect-create"),
            data={
                "prospect_is_existing_contact": "on",
                "selected_contact": str(contact.pk),
                "first_name": "",
                "last_name": "",
                "email": "",
                "phone_number": "",
                "preferred_contact_method": "email",
                "source": "Referral",
                "status": ProspectStatus.NEW,
                "interest_level": "medium",
                "notes": "Use existing contact",
            },
        )
        self.assertEqual(response.status_code, 302)
        contact.refresh_from_db()
        self.assertEqual(contact.email, "akosua@example.com")
        self.assertEqual(contact.phone_number, "+1-555-987-0000")
        prospect = Prospect.objects.get(contact=contact)
        self.assertEqual(prospect.contact_id, contact.pk)

    def test_new_contact_mode_ignores_stale_selected_contact(self):
        existing_contact = Contact.objects.create(
            first_name="James",
            last_name="Baldwin",
            email="james@example.com",
        )
        Prospect.objects.create(owner=self.user, contact=existing_contact)

        response = self.client.post(
            reverse("core:prospect-create"),
            data={
                "prospect_is_existing_contact": "",
                "selected_contact": str(existing_contact.pk),
                "first_name": "Bruce",
                "last_name": "Willis",
                "email": "bruce@example.com",
                "phone_number": "+1-555-123-4567",
                "preferred_contact_method": "email",
                "source": "Referral",
                "status": ProspectStatus.NEW,
                "interest_level": "medium",
                "notes": "New prospect after changing contact mode",
            },
        )

        self.assertEqual(response.status_code, 302)
        prospect = Prospect.objects.get(contact__email="bruce@example.com")
        self.assertEqual(prospect.contact.first_name, "Bruce")
        self.assertEqual(prospect.contact.last_name, "Willis")
        self.assertNotEqual(prospect.contact_id, existing_contact.pk)

    def test_shared_phone_number_does_not_merge_different_people(self):
        shared_phone = "+1-555-123-4567"
        james_contact = Contact.objects.create(
            first_name="James",
            last_name="Baldwin",
            email="james@example.com",
            phone_number=shared_phone,
        )
        Prospect.objects.create(owner=self.user, contact=james_contact)

        response = self.client.post(
            reverse("core:prospect-create"),
            data={
                "first_name": "Bruce",
                "last_name": "Willis",
                "email": "bruce@example.com",
                "phone_number": shared_phone,
                "preferred_contact_method": "phone",
                "source": "Referral",
                "status": ProspectStatus.NEW,
                "interest_level": "medium",
                "notes": "Shares a household phone number",
            },
        )

        self.assertEqual(response.status_code, 302)
        bruce_prospect = Prospect.objects.get(contact__email="bruce@example.com")
        self.assertEqual(bruce_prospect.contact.first_name, "Bruce")
        self.assertEqual(bruce_prospect.contact.phone_number, shared_phone)
        self.assertNotEqual(bruce_prospect.contact_id, james_contact.pk)


class ProspectBadLeadRuleTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="prospect_rule_user",
            password="safe-password-123",
        )
        self.client.force_login(self.user)
        self.prospect = Prospect.objects.create(
            owner=self.user,
            contact=Contact.objects.create(
                first_name="Nana",
                last_name="Owusu",
                email="nana@example.com",
                phone_number="+1-555-600-0000",
            ),
        )

    def _log_attempt(self, *, subject):
        return Communication.objects.create(
            owner=self.user,
            recipient_type="prospect",
            prospect=self.prospect,
            channel="email",
            communication_type="follow_up",
            subject=subject,
            body="Attempt",
            sent_at=None,
            delivery_status="sent",
        )

    def test_first_three_attempts_keep_prospect_active(self):
        for index in range(1, 4):
            self._log_attempt(subject=f"Attempt {index}")
        self.prospect.refresh_from_db()
        self.assertEqual(self.prospect.contact_attempt_count, 3)
        self.assertFalse(self.prospect.is_archived)
        self.assertNotEqual(self.prospect.status, ProspectStatus.BAD_LEAD)

    def test_fourth_attempt_marks_bad_lead_and_archives(self):
        for index in range(1, 5):
            self._log_attempt(subject=f"Attempt {index}")
        self.prospect.refresh_from_db()
        self.assertEqual(self.prospect.contact_attempt_count, 4)
        self.assertEqual(self.prospect.status, ProspectStatus.BAD_LEAD)
        self.assertTrue(self.prospect.is_archived)

    def test_archived_bad_lead_hidden_from_active_list(self):
        for index in range(1, 5):
            self._log_attempt(subject=f"Attempt {index}")
        response = self.client.get(reverse("core:prospect-list"))
        self.assertNotContains(response, f">{self.prospect}<")
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        archived_response = self.client.get(
            reverse("core:prospect-list"),
            {"state": "archived"},
        )
        self.assertContains(archived_response, str(self.prospect))

    def test_communication_history_visible_on_prospect_detail(self):
        self._log_attempt(subject="Attempt 1")
        self._log_attempt(subject="Attempt 2")
        response = self.client.get(reverse("core:prospect-detail", kwargs={"pk": self.prospect.pk}))
        self.assertContains(response, "Communication History")
        self.assertContains(response, "Attempt 1")
        self.assertContains(response, "Attempt 2")


class EnrollmentFormCalculationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="enrollment_editor",
            password="safe-password-123",
        )
        self.client.force_login(self.user)

        self.teacher = Teacher.objects.create(
            first_name="Mina",
            last_name="Clark",
            email="mina.clark@example.com",
        )
        self.location = Location.objects.create(name="Toronto Center")
        self.course = Course.objects.create(
            name="TM - adult",
            format=CourseFormat.IN_PERSON,
            status=CourseStatus.ACTIVE,
        )
        self.session = CourseSession.objects.create(
            owner=self.user,
            course=self.course,
            teacher=self.teacher,
            session_name="Spring Cohort",
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=5),
            location=self.location,
            status=SessionStatus.SCHEDULED,
        )
        self.prospect = Prospect.objects.create(
            owner=self.user,
            contact=Contact.objects.create(first_name="Lena", last_name="Hart"),
        )
        self.student = Student.objects.create(owner=self.user, prospect=self.prospect)

    def _payload(self, *, fee="100.00", discount="10.00"):
        return {
            "student": self.student.pk,
            "session": self.session.pk,
            "enrollment_date": timezone.localdate().isoformat(),
            "status": EnrollmentStatus.ENROLLED,
            "fee_amount": fee,
            "discount_amount": discount,
            "number_of_children_under_18": 0,
            "balance_due": "9999.99",
            "notes": "",
        }

    def test_enrollment_form_uses_date_picker_and_readonly_balance(self):
        response = self.client.get(reverse("core:enrollment-create"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="id_enrollment_date"')
        self.assertContains(response, 'type="date"')
        self.assertContains(response, 'id="id_balance_due"')
        self.assertContains(response, "disabled")

    def test_balance_due_recalculated_server_side(self):
        response = self.client.post(reverse("core:enrollment-create"), data=self._payload())
        self.assertEqual(response.status_code, 302)
        enrollment = Enrollment.objects.latest("id")
        self.assertEqual(str(enrollment.balance_due), "90.00")

    def test_enrollment_submission_creates_invoice_and_uses_existing_numbering_convention(self):
        response = self.client.post(
            reverse("core:enrollment-create"),
            data=self._payload(),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        redirect_chain = response.redirect_chain
        self.assertTrue(redirect_chain)
        self.assertEqual(redirect_chain[-1][1], 302)
        enrollment = Enrollment.objects.latest("id")
        invoice = Invoice.objects.get(enrollment=enrollment)
        year = timezone.localdate().year
        self.assertRegex(invoice.invoice_number, rf"^TMa-{year}-\d{{4}}$")
        self.assertIn(
            f"Enrollment completed and invoice {invoice.invoice_number} generated successfully.",
            response.content.decode(),
        )
        self.assertEqual(response.request["PATH_INFO"], reverse("core:invoice-detail", kwargs={"pk": invoice.pk}))

    def test_invoice_list_displays_generated_invoices(self):
        self.client.post(reverse("core:enrollment-create"), data=self._payload())
        invoice = Invoice.objects.latest("id")
        response = self.client.get(reverse("core:invoice-list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, invoice.invoice_number)

    def test_manual_invoice_create_redirects_to_enrollment_create(self):
        response = self.client.get(reverse("core:invoice-create"))
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("core:enrollment-create"))

    def test_discount_cannot_exceed_fee(self):
        response = self.client.post(
            reverse("core:enrollment-create"),
            data=self._payload(fee="100.00", discount="120.00"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Discount amount cannot exceed fee amount.")

    def test_tm_family_course_fee_calculation_applies_children_surcharge(self):
        family_course, _ = Course.objects.update_or_create(
            code="TM-FM",
            defaults={
                "name": "TM - family",
                "category": "TM",
                "variant": "Family",
                "base_fee": Decimal("4500.00"),
                "standard_fee": Decimal("4500.00"),
                "is_active": True,
                "status": CourseStatus.ACTIVE,
            },
        )
        family_session = CourseSession.objects.create(
            owner=self.user,
            course=family_course,
            teacher=self.teacher,
            session_name="Family Cohort",
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=5),
            location=self.location,
            status=SessionStatus.SCHEDULED,
        )
        payload = self._payload(fee="0.00", discount="500.00")
        payload["session"] = family_session.pk
        payload["number_of_children_under_18"] = 2
        response = self.client.post(
            reverse("core:enrollment-create"),
            data=payload,
        )
        self.assertEqual(response.status_code, 302)
        enrollment = Enrollment.objects.latest("id")
        self.assertEqual(enrollment.fee_amount, Decimal("6000.00"))
        self.assertEqual(enrollment.balance_due, Decimal("5500.00"))


class InvoiceNumberingByCourseCodeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="invoice_numbering_user",
            password="safe-password-123",
        )
        self.teacher = Teacher.objects.create(
            first_name="Ivy",
            last_name="Lane",
            email="ivy.lane@example.com",
        )
        self.location = Location.objects.create(name="Accra Center")
        self.course, _ = Course.objects.update_or_create(
            code="TM-AD",
            defaults={
                "name": "TM - adult",
                "category": "TM",
                "variant": "Adult",
                "base_fee": Decimal("3000.00"),
                "standard_fee": Decimal("3000.00"),
                "is_active": True,
                "status": CourseStatus.ACTIVE,
            },
        )
        self.session = CourseSession.objects.create(
            owner=self.user,
            course=self.course,
            teacher=self.teacher,
            session_name="Adult Cohort",
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=5),
            location=self.location,
            status=SessionStatus.SCHEDULED,
        )
        self.student = Student.objects.create(
            owner=self.user,
            prospect=Prospect.objects.create(
                owner=self.user,
                contact=Contact.objects.create(first_name="Kofi", last_name="Mensah"),
            ),
        )

    def _create_enrollment(self):
        return Enrollment.objects.create(
            student=self.student,
            session=self.session,
            enrollment_date=timezone.now(),
            status=EnrollmentStatus.ENROLLED,
            fee_amount=Decimal("3000.00"),
            discount_amount=Decimal("0.00"),
            number_of_children_under_18=0,
            balance_due=Decimal("3000.00"),
        )

    def test_invoice_number_uses_course_code_year_and_sequence(self):
        enrollment_one = self._create_enrollment()
        invoice_one, _ = generate_invoice_for_enrollment(enrollment_one)
        year = timezone.localdate().year
        self.assertRegex(invoice_one.invoice_number, rf"^TM-AD-{year}-\d{{4}}$")

        second_student = Student.objects.create(
            owner=self.user,
            prospect=Prospect.objects.create(
                owner=self.user,
                contact=Contact.objects.create(first_name="Ama", last_name="Boateng"),
            ),
        )
        enrollment_two = Enrollment.objects.create(
            student=second_student,
            session=self.session,
            enrollment_date=timezone.now(),
            status=EnrollmentStatus.ENROLLED,
            fee_amount=Decimal("3000.00"),
            discount_amount=Decimal("0.00"),
            number_of_children_under_18=0,
            balance_due=Decimal("3000.00"),
        )
        invoice_two, _ = generate_invoice_for_enrollment(enrollment_two)
        self.assertTrue(invoice_two.invoice_number.startswith(f"TM-AD-{year}-"))
        seq_one = int(invoice_one.invoice_number.rsplit("-", 1)[-1])
        seq_two = int(invoice_two.invoice_number.rsplit("-", 1)[-1])
        self.assertEqual(seq_two, seq_one + 1)


class PaymentCreateInvoiceFilteringTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="payment_creator",
            password="safe-password-123",
        )
        self.client.force_login(self.user)
        self.teacher = Teacher.objects.create(
            first_name="Noah",
            last_name="Gray",
            email="noah.gray@example.com",
        )
        self.location = Location.objects.create(name="Montreal Center")
        self.course = Course.objects.create(
            name="TM Mastery",
            format=CourseFormat.IN_PERSON,
            status=CourseStatus.ACTIVE,
        )
        self.session = CourseSession.objects.create(
            owner=self.user,
            course=self.course,
            teacher=self.teacher,
            session_name="Summer",
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=4),
            location=self.location,
            status=SessionStatus.SCHEDULED,
        )
        self.student_a = Student.objects.create(
            owner=self.user,
            prospect=Prospect.objects.create(
                owner=self.user,
                contact=Contact.objects.create(first_name="Ari", last_name="Khan"),
            ),
        )
        self.student_b = Student.objects.create(
            owner=self.user,
            prospect=Prospect.objects.create(
                owner=self.user,
                contact=Contact.objects.create(first_name="Bea", last_name="Stone"),
            ),
        )
        enrollment_a = Enrollment.objects.create(
            student=self.student_a,
            session=self.session,
            enrollment_date=timezone.now(),
            status=EnrollmentStatus.ENROLLED,
            fee_amount=Decimal("200.00"),
            discount_amount=Decimal("0.00"),
            balance_due=Decimal("200.00"),
        )
        enrollment_b = Enrollment.objects.create(
            student=self.student_b,
            session=self.session,
            enrollment_date=timezone.now(),
            status=EnrollmentStatus.ENROLLED,
            fee_amount=Decimal("150.00"),
            discount_amount=Decimal("0.00"),
            balance_due=Decimal("150.00"),
        )
        self.invoice_a = Invoice.objects.create(
            owner=self.user,
            enrollment=enrollment_a,
            invoice_number="INV-A-1001",
            issue_date=timezone.localdate(),
            subtotal="200.00",
            discount_amount="0.00",
            tax_amount="0.00",
            total_amount="200.00",
            status="sent",
        )
        self.invoice_b = Invoice.objects.create(
            owner=self.user,
            enrollment=enrollment_b,
            invoice_number="INV-B-1002",
            issue_date=timezone.localdate(),
            subtotal="150.00",
            discount_amount="0.00",
            tax_amount="0.00",
            total_amount="150.00",
            status="sent",
        )

    def test_student_query_param_prefills_and_filters_invoices(self):
        response = self.client.get(reverse("core:payment-create"), {"student": self.student_a.pk})
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(form.initial.get("student"), self.student_a.pk)
        queryset_ids = list(form.fields["invoice"].queryset.values_list("id", flat=True))
        self.assertIn(self.invoice_a.pk, queryset_ids)
        self.assertNotIn(self.invoice_b.pk, queryset_ids)

    def test_single_open_invoice_is_auto_selected(self):
        response = self.client.get(reverse("core:payment-create"), {"student": self.student_a.pk})
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(form.initial.get("invoice"), self.invoice_a.pk)

    def test_no_open_invoice_disables_creation(self):
        Payment.objects.create(
            owner=self.user,
            invoice=self.invoice_a,
            payment_date=timezone.now(),
            amount_paid="200.00",
            payment_method="cash",
            confirmation_status="confirmed",
        )
        response = self.client.get(reverse("core:payment-create"), {"student": self.student_a.pk})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["no_open_invoices"])
        self.assertContains(response, "Payment creation is disabled")

    def test_no_student_selected_keeps_invoice_dropdown_empty(self):
        response = self.client.get(reverse("core:payment-create"))
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(form.fields["invoice"].queryset.count(), 0)
        self.assertTrue(form.fields["invoice"].disabled)
        self.assertContains(response, "Select a student to see available invoices.")

    def test_payment_page_uses_ajax_student_search_without_global_dropdown(self):
        response = self.client.get(reverse("core:payment-create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="payment-student-search"')
        self.assertContains(response, 'placeholder="Search for a student..."')
        self.assertContains(response, 'id="id_student"')
        self.assertNotContains(response, '<select name="student"')
        self.assertNotContains(response, "Ari Khan")
        self.assertNotContains(response, "Bea Stone")

    def test_payment_student_search_matches_identity_and_only_returns_open_invoices(self):
        contact = self.student_a.prospect.contact
        contact.email = "ari.khan@example.com"
        contact.phone_number = "+1 514 555 0188"
        contact.save(update_fields=["email", "phone_number"])

        for query in ("Ari Khan", "ari.khan@example.com", "555 0188"):
            with self.subTest(query=query):
                response = self.client.get(
                    reverse("core:payment-student-search"),
                    {"q": query},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    [item["id"] for item in response.json()["results"]],
                    [self.student_a.pk],
                )

        Payment.objects.create(
            owner=self.user,
            invoice=self.invoice_a,
            payment_date=timezone.now(),
            amount_paid="200.00",
            payment_method="cash",
            confirmation_status="confirmed",
        )
        paid_response = self.client.get(
            reverse("core:payment-student-search"),
            {"q": "Ari Khan"},
        )
        self.assertEqual(paid_response.json()["results"], [])

    def test_ajax_invoice_lookup_returns_only_selected_student_open_invoices(self):
        response = self.client.get(
            reverse("core:payment-invoices-for-student", kwargs={"student_id": self.student_a.pk})
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        invoice_ids = [item["id"] for item in payload["invoices"]]
        self.assertIn(self.invoice_a.pk, invoice_ids)
        self.assertNotIn(self.invoice_b.pk, invoice_ids)

    def test_server_blocks_invoice_not_matching_selected_student(self):
        response = self.client.post(
            reverse("core:payment-create"),
            data={
                "student": self.student_a.pk,
                "invoice": self.invoice_b.pk,
                "payment_date": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
                "amount_paid": "25.00",
                "payment_method": "cash",
                "reference_number": "",
                "confirmation_status": "pending",
                "notes": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select a valid choice")

    def test_server_blocks_payment_exceeding_outstanding(self):
        response = self.client.post(
            reverse("core:payment-create"),
            data={
                "student": self.student_a.pk,
                "invoice": self.invoice_a.pk,
                "payment_date": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
                "amount_paid": "250.00",
                "payment_method": "cash",
                "reference_number": "",
                "confirmation_status": "pending",
                "notes": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Amount cannot exceed outstanding balance")

    def test_server_blocks_invoice_not_owned_by_user(self):
        outsider = get_user_model().objects.create_user(
            username="outsider_payment_owner",
            password="safe-password-123",
        )
        outsider_student = Student.objects.create(
            owner=outsider,
            prospect=Prospect.objects.create(
                owner=outsider,
                contact=Contact.objects.create(first_name="Out", last_name="Side"),
            ),
        )
        outsider_enrollment = Enrollment.objects.create(
            student=outsider_student,
            session=self.session,
            enrollment_date=timezone.now(),
            status=EnrollmentStatus.ENROLLED,
            fee_amount=Decimal("175.00"),
            discount_amount=Decimal("0.00"),
            balance_due=Decimal("175.00"),
        )
        outsider_invoice = Invoice.objects.create(
            owner=outsider,
            enrollment=outsider_enrollment,
            invoice_number="INV-OUT-1003",
            issue_date=timezone.localdate(),
            subtotal="175.00",
            discount_amount="0.00",
            tax_amount="0.00",
            total_amount="175.00",
            status="sent",
        )
        response = self.client.post(
            reverse("core:payment-create"),
            data={
                "student": outsider_student.pk,
                "invoice": outsider_invoice.pk,
                "payment_date": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
                "amount_paid": "50.00",
                "payment_method": "cash",
                "reference_number": "",
                "confirmation_status": "pending",
                "notes": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select a valid choice")


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@tmis.local",
)
class CommunicationEmailSendTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="comm_sender",
            email="sender@example.com",
            password="safe-password-123",
        )
        self.client.force_login(self.user)
        self.prospect = Prospect.objects.create(
            owner=self.user,
            contact=Contact.objects.create(
                first_name="Akua",
                last_name="Mensima",
                email="akua@example.com",
                phone_number="+1-555-123-0000",
            ),
        )
        self.student = Student.objects.create(
            owner=self.user,
            prospect=Prospect.objects.create(
                owner=self.user,
                contact=Contact.objects.create(
                    first_name="Yaw",
                    last_name="Amo",
                    email="yaw@example.com",
                    phone_number="+1-555-999-0000",
                ),
            ),
        )

    def _send(self, *, recipient_type, prospect_id="", student_id="", subject="Hello"):
        return self.client.post(
            reverse("core:communication-create"),
            data={
                "recipient_type": recipient_type,
                "prospect": prospect_id,
                "student": student_id,
                "enrollment": "",
                "channel": "email",
                "communication_type": "follow_up",
                "subject": subject,
                "body": "Test body",
                "sent_at": "",
                "delivery_status": "queued",
                "provider_status": "",
                "related_entity_type": "",
                "related_entity_id": "",
                "notes": "",
            },
        )

    def test_sending_email_to_prospect(self):
        response = self._send(recipient_type="prospect", prospect_id=self.prospect.pk)
        self.assertEqual(response.status_code, 302)
        comm = Communication.objects.latest("id")
        self.assertEqual(comm.owner, self.user)
        self.assertEqual(comm.prospect, self.prospect)
        self.assertEqual(comm.delivery_status, "sent")
        self.assertIsNotNone(comm.sent_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["akua@example.com"])
        self.assertEqual(mail.outbox[0].from_email, "noreply@tmis.local")
        self.assertEqual(mail.outbox[0].reply_to, ["sender@example.com"])

    def test_sending_email_to_student(self):
        response = self._send(recipient_type="student", student_id=self.student.pk)
        self.assertEqual(response.status_code, 302)
        comm = Communication.objects.latest("id")
        self.assertEqual(comm.owner, self.user)
        self.assertEqual(comm.student, self.student)
        self.assertEqual(comm.delivery_status, "sent")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["yaw@example.com"])

    def test_student_list_shows_send_message_action(self):
        response = self.client.get(reverse("core:student-list"))
        self.assertContains(
            response,
            f'{reverse("core:communication-create")}?recipient_type=student&student={self.student.pk}',
        )

    def test_fourth_prospect_attempt_marks_bad_lead_and_archives(self):
        for idx in range(4):
            response = self._send(
                recipient_type="prospect",
                prospect_id=self.prospect.pk,
                subject=f"Attempt {idx + 1}",
            )
            self.assertEqual(response.status_code, 302)
        self.prospect.refresh_from_db()
        self.assertEqual(self.prospect.contact_attempt_count, 4)
        self.assertEqual(self.prospect.status, ProspectStatus.BAD_LEAD)
        self.assertTrue(self.prospect.is_archived)

    def test_missing_recipient_email_shows_clear_error(self):
        no_email_prospect = Prospect.objects.create(
            owner=self.user,
            contact=Contact.objects.create(first_name="No", last_name="Email"),
        )
        response = self._send(recipient_type="prospect", prospect_id=no_email_prospect.pk)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Selected recipient does not have an email address.")


class EnrollmentEligibilityProgressionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="eligibility_user",
            password="safe-password-123",
        )
        self.client.force_login(self.user)
        self.teacher = Teacher.objects.create(
            first_name="Eli",
            last_name="Guardian",
            email="eli.guardian@example.com",
        )
        self.location = Location.objects.create(name="Eligibility Center")
        self.student = Student.objects.create(
            owner=self.user,
            prospect=Prospect.objects.create(
                owner=self.user,
                contact=Contact.objects.create(first_name="Nii", last_name="K.", email="nii@example.com"),
            ),
        )
        self.tm = SimpleNamespace(name="TM - adult", code="TMa")
        self.at1 = SimpleNamespace(name="Advanced Technique 1", code="AT1")
        self.at2 = SimpleNamespace(name="Advanced Technique 2", code="AT2")
        self.at3 = SimpleNamespace(name="Advanced Technique 3", code="AT3")
        self.at4 = SimpleNamespace(name="Advanced Technique 4", code="AT4")
        self.al = SimpleNamespace(name="TM-Sidhi", code="AL")

    def test_person_with_no_enrollment_cannot_enroll_in_at1(self):
        with patch("core.services.enrollment_eligibility.get_person_enrolled_course_codes", return_value=set()):
            self.assertFalse(is_eligible_for_course(self.student, self.at1))

    def test_person_with_tm_can_enroll_in_at1(self):
        with patch("core.services.enrollment_eligibility.get_person_enrolled_course_codes", return_value={"TM"}):
            self.assertTrue(is_eligible_for_course(self.student, self.at1))

    def test_person_with_only_tm_cannot_enroll_in_at2(self):
        with patch("core.services.enrollment_eligibility.get_person_enrolled_course_codes", return_value={"TM"}):
            self.assertFalse(is_eligible_for_course(self.student, self.at2))

    def test_person_with_tm_and_at1_can_enroll_in_at2(self):
        with patch("core.services.enrollment_eligibility.get_person_enrolled_course_codes", return_value={"TM", "AT1"}):
            self.assertTrue(is_eligible_for_course(self.student, self.at2))

    def test_person_with_tm_at1_at2_can_enroll_in_at3(self):
        with patch("core.services.enrollment_eligibility.get_person_enrolled_course_codes", return_value={"TM", "AT1", "AT2"}):
            self.assertTrue(is_eligible_for_course(self.student, self.at3))

    def test_person_with_tm_at1_at2_at3_can_enroll_in_at4(self):
        with patch("core.services.enrollment_eligibility.get_person_enrolled_course_codes", return_value={"TM", "AT1", "AT2", "AT3"}):
            self.assertTrue(is_eligible_for_course(self.student, self.at4))

    def test_person_with_full_chain_can_enroll_in_al(self):
        with patch("core.services.enrollment_eligibility.get_person_enrolled_course_codes", return_value={"TM", "AT1", "AT2", "AT3", "AT4"}):
            self.assertTrue(is_eligible_for_course(self.student, self.al))

    def _integration_setup_for_at1(self):
        tm_course = Course.objects.filter(name__icontains="TM").first()
        at_course = Course.objects.filter(name__icontains="Advanced Technique").first()
        if not tm_course or not at_course:
            self.skipTest("Required seeded courses not available in this environment.")
        tm_session = CourseSession.objects.create(
            owner=self.user,
            course=tm_course,
            teacher=self.teacher,
            session_name="TM Session",
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=2),
            location=self.location,
            status=SessionStatus.SCHEDULED,
        )
        at_session = CourseSession.objects.create(
            owner=self.user,
            course=at_course,
            teacher=self.teacher,
            session_name="AT Session",
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=2),
            location=self.location,
            status=SessionStatus.SCHEDULED,
        )
        return tm_course, at_course, tm_session, at_session

    def _payload(self, course, session):
        return {
            "person_type": "student",
            "student": self.student.pk,
            "prospect": "",
            "contact": "",
            "course": course.pk,
            "session": session.pk,
            "enrollment_date": timezone.localdate().isoformat(),
            "status": EnrollmentStatus.ENROLLED,
            "fee_amount": "100.00",
            "discount_amount": "0.00",
            "number_of_children_under_18": 0,
            "balance_due": "100.00",
            "new_first_name": "",
            "new_last_name": "",
            "new_email": "",
            "new_phone_number": "",
            "new_source": "",
            "new_notes": "",
        }

    def test_invalid_submission_does_not_create_enrollment_or_invoice(self):
        _, at_course, _, at_session = self._integration_setup_for_at1()
        before_enrollments = Enrollment.objects.count()
        before_invoices = Invoice.objects.count()
        response = self.client.post(reverse("core:enrollment-create"), data=self._payload(at_course, at_session))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "must first enroll")
        self.assertEqual(Enrollment.objects.count(), before_enrollments)
        self.assertEqual(Invoice.objects.count(), before_invoices)

    def test_eligibility_endpoint_returns_missing(self):
        _, at_course, _, _ = self._integration_setup_for_at1()
        response = self.client.get(
            reverse("core:enrollment-check-eligibility"),
            {"person_type": "student", "person_id": self.student.pk, "course_id": at_course.pk},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["eligible"])
        self.assertIn("TM", payload["missing"])
