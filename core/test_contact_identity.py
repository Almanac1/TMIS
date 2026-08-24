from datetime import date

from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from unittest.mock import patch

from core.forms import StudentForm
from core.models import (
    Communication,
    Disbursement,
    Enrollment,
    Inquiry,
    Invoice,
    Meditator,
    Payment,
    Prospect,
    ProspectStatus,
    Student,
    Contact,
)
from core.services.meditator_transitions import (
    MeditatorEligibility,
    ensure_meditator_transition_for_student,
)


class CanonicalContactIdentityTests(TestCase):
    def setUp(self):
        self.contact = Contact.objects.create(
            first_name="Ama",
            last_name="Mensah",
            email="ama.identity@example.com",
            phone_number="+233 24 555 0101",
            date_of_birth=date(1990, 5, 12),
            address="10 Independence Avenue",
            city="Accra",
            province_state="Greater Accra",
            country="Ghana",
        )
        self.prospect = Prospect.objects.create(contact=self.contact, source="Referral")
        self.student = Student.objects.create(prospect=self.prospect)

    def test_lifecycle_records_resolve_one_contact_without_duplicate_relation(self):
        meditator = Meditator.objects.create(student=self.student)

        self.assertEqual(self.prospect.contact, self.contact)
        self.assertEqual(self.student.contact, self.contact)
        self.assertEqual(meditator.contact, self.contact)
        with self.assertRaises(FieldDoesNotExist):
            Student._meta.get_field("contact")

    def test_student_identity_and_profile_values_are_contact_backed(self):
        self.assertEqual(self.student.first_name, "Ama")
        self.assertEqual(self.student.email, "ama.identity@example.com")
        self.assertEqual(self.student.date_of_birth.isoformat(), "1990-05-12")
        self.assertEqual(self.student.address, "10 Independence Avenue")
        self.assertEqual(self.student.city, "Accra")
        with self.assertRaises(FieldDoesNotExist):
            Student._meta.get_field("address")

    def test_student_edit_updates_contact_profile_without_reassigning_identity(self):
        form = StudentForm(
            data={
                "prospect": self.prospect.pk,
                "teacher": "",
                "enrollment_status": "pending",
                "notes": "Student lifecycle note",
                "owner": "",
                "date_of_birth": "1991-06-13",
                "address": "20 Liberation Road",
                "city": "Tema",
                "province_state": "Greater Accra",
                "country": "Ghana",
            },
            instance=self.student,
        )

        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.contact.refresh_from_db()
        self.assertEqual(saved.prospect_id, self.prospect.pk)
        self.assertEqual(self.contact.address, "20 Liberation Road")
        self.assertEqual(self.contact.city, "Tema")
        self.assertEqual(self.contact.date_of_birth.isoformat(), "1991-06-13")

    @patch(
        "core.services.prospect_conversion.validate_prospect_conversion_financial_eligibility"
    )
    def test_conversion_reuses_contact_and_is_idempotent(self, validate_financials):
        contact_count = Contact.objects.count()

        first_student, first_created = self.prospect.convert_to_student()
        second_student, second_created = self.prospect.convert_to_student()

        self.prospect.refresh_from_db()
        self.assertFalse(first_created)  # Existing pre-conversion enrollment shell.
        self.assertFalse(second_created)
        self.assertEqual(first_student, self.student)
        self.assertEqual(second_student, self.student)
        self.assertEqual(first_student.contact, self.contact)
        self.assertEqual(Contact.objects.count(), contact_count)
        self.assertEqual(Student.objects.filter(prospect__contact=self.contact).count(), 1)
        self.assertEqual(self.prospect.status, ProspectStatus.CONVERTED)
        self.assertTrue(self.prospect.converted_to_student)
        self.assertEqual(self.prospect.converted_student, self.student)
        self.assertIsNotNone(self.prospect.converted_at)
        validate_financials.assert_called_once_with(self.prospect)

    @patch("core.services.meditator_transitions.evaluate_student_meditator_eligibility")
    def test_meditator_progression_reuses_contact_and_retains_student(self, evaluate):
        self.prospect.status = ProspectStatus.CONVERTED
        self.prospect.converted_to_student = True
        self.prospect.converted_student = self.student
        self.prospect.converted_at = self.prospect.created_at
        self.prospect.save(
            update_fields=[
                "status",
                "converted_to_student",
                "converted_student",
                "converted_at",
                "updated_at",
            ]
        )
        evaluate.return_value = MeditatorEligibility(
            eligible=True,
            anchor_date=date(2026, 1, 1),
            intro_completed_on=date(2026, 1, 1),
            day20_completed_on=date(2026, 1, 21),
            missing_reasons=(),
        )
        contact_count = Contact.objects.count()

        first = ensure_meditator_transition_for_student(self.student)
        second = ensure_meditator_transition_for_student(self.student)

        self.assertEqual(first, second)
        self.assertEqual(first.contact, self.contact)
        self.assertTrue(Student.objects.filter(pk=self.student.pk).exists())
        self.assertEqual(Meditator.objects.filter(student=self.student).count(), 1)
        self.assertEqual(Contact.objects.count(), contact_count)


class DomainRelationshipPlacementTests(TestCase):
    def test_operational_and_financial_models_remain_domain_scoped(self):
        self.assertIs(Enrollment._meta.get_field("student").remote_field.model, Student)
        self.assertIs(Invoice._meta.get_field("enrollment").remote_field.model, Enrollment)
        self.assertIs(Payment._meta.get_field("invoice").remote_field.model, Invoice)
        self.assertIs(Disbursement._meta.get_field("enrollment").remote_field.model, Enrollment)
        self.assertIs(Inquiry._meta.get_field("prospect").remote_field.model, Prospect)
        self.assertIs(Inquiry._meta.get_field("student").remote_field.model, Student)
        self.assertIs(Communication._meta.get_field("prospect").remote_field.model, Prospect)
        self.assertIs(Communication._meta.get_field("student").remote_field.model, Student)

        for model in (Enrollment, Invoice, Payment, Disbursement, Inquiry, Communication):
            with self.assertRaises(FieldDoesNotExist):
                model._meta.get_field("contact")


class ContactDuplicateDetectionTests(TestCase):
    def test_email_match_reuses_contact_even_when_name_changes(self):
        existing = Contact.objects.create(
            first_name="Akosua",
            last_name="Mensah",
            email="akosua@example.com",
        )

        matched, created = Contact.get_or_create_from_identity(
            first_name="Akosua-Married",
            last_name="Owusu",
            email="AKOSUA@example.com",
        )

        self.assertFalse(created)
        self.assertEqual(matched, existing)
        self.assertEqual(Contact.objects.count(), 1)

    def test_same_name_and_normalized_phone_is_rejected(self):
        Contact.objects.create(
            first_name="Kwame",
            last_name="Asare",
            phone_number="+233 (24) 555-0101",
        )

        with self.assertRaisesMessage(
            ValidationError,
            "A contact with this name and phone number already exists.",
        ):
            Contact.objects.create(
                first_name="kwame",
                last_name="ASARE",
                phone_number="233245550101",
            )

    def test_shared_phone_is_allowed_for_different_people(self):
        Contact.objects.create(
            first_name="James",
            last_name="Baldwin",
            phone_number="+233245550202",
        )
        Contact.objects.create(
            first_name="Bruce",
            last_name="Willis",
            phone_number="+233245550202",
        )

        self.assertEqual(Contact.objects.filter(phone_number="+233245550202").count(), 2)

    def test_database_constraint_blocks_case_insensitive_duplicate_email(self):
        Contact.objects.create(
            first_name="Ama",
            last_name="One",
            email="duplicate@example.com",
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            Contact.objects.bulk_create(
                [
                    Contact(
                        first_name="Different",
                        last_name="Name",
                        email="DUPLICATE@example.com",
                    )
                ]
            )
