import uuid

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.forms import InquiryForm
from core.models import Contact, Inquiry, InquiryStatus, Prospect, Student
from core.services.home_dashboard import get_home_dashboard_data


class InquiryIdentityWorkflowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="inquiry-admin",
            email="inquiry-admin@example.com",
            password="safe-password-123",
        )
        self.client.force_login(self.user)
        self.contact = Contact.objects.create(
            first_name="Akua",
            last_name="Inquiry",
            email="akua.inquiry@example.com",
        )
        self.prospect = Prospect.objects.create(contact=self.contact, owner=self.user)
        self.student = Student.objects.create(prospect=self.prospect, owner=self.user)

    def _create_inquiry(self, subject):
        return Inquiry.objects.create(
            owner=self.user,
            contact=self.contact,
            prospect=self.prospect,
            student=self.student,
            inquiry_date=timezone.now(),
            subject=subject,
            message=f"Message for {subject}",
            status=InquiryStatus.OPEN,
        )

    def test_one_contact_can_have_multiple_independent_inquiry_uuids(self):
        first = self._create_inquiry("First")
        second = self._create_inquiry("Second")

        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(first.contact, self.contact)
        self.assertEqual(second.contact, self.contact)
        self.assertEqual(self.contact.inquiries.count(), 2)
        self.assertIs(Inquiry._meta.pk, Inquiry._meta.get_field("uuid"))

    def test_explicit_uuid_is_stable_and_is_database_primary_key(self):
        stable_uuid = uuid.uuid4()
        inquiry = Inquiry.objects.create(
            uuid=stable_uuid,
            contact=self.contact,
            inquiry_date=timezone.now(),
            message="Stable UUID",
        )
        inquiry.subject = "Updated event"
        inquiry.save()
        inquiry.refresh_from_db()
        self.assertEqual(inquiry.uuid, stable_uuid)
        constraints = connection.introspection.get_constraints(
            connection.cursor(), "core_inquiry"
        )
        primary_columns = [
            details["columns"]
            for details in constraints.values()
            if details.get("primary_key")
        ]
        self.assertIn(["uuid"], primary_columns)

    def test_contact_only_inquiry_is_valid(self):
        inquiry = Inquiry.objects.create(
            contact=self.contact,
            inquiry_date=timezone.now(),
            message="Contact-only event",
        )

        self.assertEqual(inquiry.contact, self.contact)
        self.assertIsNone(inquiry.prospect)
        self.assertIsNone(inquiry.student)

    def test_lifecycle_contact_mismatch_is_rejected_server_side(self):
        other = Contact.objects.create(
            first_name="Different",
            last_name="Person",
            email="different.inquiry@example.com",
        )
        form = InquiryForm(
            data={
                "contact": other.pk,
                "prospect": self.prospect.pk,
                "student": self.student.pk,
                "inquiry_date": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
                "channel": "website",
                "subject": "Mismatch",
                "message": "Must fail",
                "status": InquiryStatus.OPEN,
                "assigned_to": "",
                "owner": self.user.pk,
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("prospect", form.errors)
        self.assertIn("student", form.errors)

    def test_uuid_and_legacy_detail_urls_resolve_same_inquiry(self):
        inquiry = self._create_inquiry("Stable URL")
        Inquiry.objects.filter(pk=inquiry.pk).update(legacy_int_id=987654)
        inquiry.refresh_from_db()

        uuid_response = self.client.get(
            reverse("core:inquiry-detail", kwargs={"pk": inquiry.pk})
        )
        legacy_response = self.client.get(
            reverse("core:inquiry-detail", kwargs={"pk": inquiry.legacy_int_id})
        )
        self.assertEqual(uuid_response.status_code, 200)
        self.assertEqual(legacy_response.status_code, 200)
        self.assertContains(uuid_response, inquiry.public_id)
        self.assertContains(legacy_response, inquiry.public_id)

    def test_inquiry_history_survives_lifecycle_deletion_and_protects_contact(self):
        inquiry = Inquiry.objects.create(
            contact=self.contact,
            prospect=self.prospect,
            inquiry_date=timezone.now(),
            message="Historical event",
        )
        self.student.delete()
        self.prospect.delete()
        inquiry.refresh_from_db()
        self.assertIsNone(inquiry.prospect)
        self.assertEqual(inquiry.contact, self.contact)
        with self.assertRaises(ProtectedError):
            self.contact.delete()

    def test_dashboard_counts_inquiry_events_not_people(self):
        self._create_inquiry("Open one")
        self._create_inquiry("Open two")

        dashboard = get_home_dashboard_data(user=self.user)

        self.assertEqual(dashboard["kpis"]["open_inquiries"], 2)
