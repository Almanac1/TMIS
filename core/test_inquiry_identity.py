import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.models.deletion import ProtectedError
from django.test import TestCase, TransactionTestCase
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from core.forms import CommunicationForm, InquiryForm
from core.models import (
    Communication,
    CommunicationChannel,
    CommunicationType,
    Contact,
    Inquiry,
    InquiryStatus,
    Prospect,
    RecipientType,
    Student,
)
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

    def test_one_contact_with_one_inquiry_retains_canonical_identity(self):
        inquiry = Inquiry.objects.create(
            contact=self.contact,
            inquiry_date=timezone.now(),
            subject="Single inquiry",
            message="One event for one person",
        )

        self.assertEqual(self.contact.inquiries.get(), inquiry)
        self.assertEqual(inquiry.contact, self.contact)

    def test_inquiry_numbers_are_unique_sequential_and_separate_from_uuid(self):
        first = self._create_inquiry("Numbered first")
        second = self._create_inquiry("Numbered second")
        year = timezone.localdate().year

        self.assertRegex(first.inquiry_number, rf"^INQ-{year}-\d{{4,}}$")
        self.assertRegex(second.inquiry_number, rf"^INQ-{year}-\d{{4,}}$")
        self.assertNotEqual(first.inquiry_number, second.inquiry_number)
        self.assertEqual(
            int(second.inquiry_number.rsplit("-", 1)[1]),
            int(first.inquiry_number.rsplit("-", 1)[1]) + 1,
        )
        self.assertNotEqual(first.inquiry_number, str(first.pk))

    def test_inquiry_number_is_immutable(self):
        inquiry = self._create_inquiry("Immutable number")
        original_number = inquiry.inquiry_number
        inquiry.inquiry_number = "INQ-1900-9999"

        with self.assertRaises(ValidationError):
            inquiry.save()

        inquiry.refresh_from_db()
        self.assertEqual(inquiry.inquiry_number, original_number)

    def test_inquiry_list_displays_and_searches_human_reference(self):
        inquiry = self._create_inquiry("Reference search")

        response = self.client.get(
            reverse("core:inquiry-list"),
            {"q": inquiry.inquiry_number},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, inquiry.inquiry_number)
        self.assertContains(response, inquiry.subject)

    def test_inquiry_search_matches_full_contact_name_and_details(self):
        self.contact.first_name = "Nathan"
        self.contact.last_name = "Boateng"
        self.contact.phone_number = "+233 24 555 0482"
        self.contact.save()
        inquiry = self._create_inquiry("TM course details")

        for query in (
            "Nathan Boateng",
            self.contact.email,
            "555 0482",
            "course details",
            InquiryStatus.OPEN,
            inquiry.inquiry_number,
        ):
            response = self.client.get(reverse("core:inquiry-list"), {"q": query})
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, inquiry.inquiry_number)

    def test_communication_can_link_to_matching_inquiry(self):
        inquiry = self._create_inquiry("Communication context")
        form = CommunicationForm(
            request_user=self.user,
            data={
                "recipient_type": RecipientType.PROSPECT,
                "prospect": self.prospect.pk,
                "student": "",
                "enrollment": "",
                "inquiry": inquiry.pk,
                "channel": CommunicationChannel.EMAIL,
                "communication_type": CommunicationType.GENERAL,
                "subject": "Inquiry response",
                "body": "Here are the requested details.",
                "provider_status": "",
                "related_entity_type": "",
                "related_entity_id": "",
                "notes": "",
                "owner": self.user.pk,
            },
        )

        self.assertTrue(form.is_valid(), form.errors)
        communication = form.save()
        self.assertEqual(communication.inquiry, inquiry)
        self.assertEqual(inquiry.communications.get(), communication)

    def test_communication_create_prefills_inquiry_context(self):
        inquiry = self._create_inquiry("Prefilled communication")

        response = self.client.get(
            reverse("core:communication-create"),
            {"inquiry": inquiry.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].initial["inquiry"], inquiry)
        self.assertEqual(
            response.context["form"].initial["recipient_type"],
            RecipientType.STUDENT,
        )
        self.assertContains(response, inquiry.inquiry_number)

    def test_sending_response_from_inquiry_preserves_relationship(self):
        inquiry = self._create_inquiry("Send linked response")

        response = self.client.post(
            reverse("core:communication-create") + f"?inquiry={inquiry.pk}",
            data={
                "recipient_type": RecipientType.STUDENT,
                "prospect": "",
                "student": self.student.pk,
                "enrollment": "",
                "inquiry": inquiry.pk,
                "channel": CommunicationChannel.EMAIL,
                "communication_type": CommunicationType.GENERAL,
                "subject": "Linked response",
                "body": "Response to the selected inquiry.",
                "provider_status": "",
                "related_entity_type": "",
                "related_entity_id": "",
                "notes": "",
                "owner": self.user.pk,
            },
        )

        self.assertEqual(response.status_code, 302)
        communication = Communication.objects.get(subject="Linked response")
        self.assertEqual(communication.inquiry, inquiry)

    def test_inquiry_autocomplete_searches_reference_and_contact_name(self):
        self.contact.first_name = "Nathan"
        self.contact.last_name = "Boateng"
        self.contact.save()
        inquiry = self._create_inquiry("Autocomplete result")

        for query in (inquiry.inquiry_number, "Nathan Boateng"):
            response = self.client.get(
                reverse("core:inquiry-autocomplete"),
                {"q": query},
            )
            self.assertEqual(response.status_code, 200)
            result_ids = {item["id"] for item in response.json()["results"]}
            self.assertIn(str(inquiry.pk), result_ids)

    def test_communication_rejects_inquiry_for_different_contact(self):
        inquiry = self._create_inquiry("Wrong recipient guard")
        other_contact = Contact.objects.create(
            first_name="Other",
            last_name="Recipient",
            email="other.recipient@example.com",
        )
        other_prospect = Prospect.objects.create(contact=other_contact, owner=self.user)
        form = CommunicationForm(
            request_user=self.user,
            data={
                "recipient_type": RecipientType.PROSPECT,
                "prospect": other_prospect.pk,
                "student": "",
                "enrollment": "",
                "inquiry": inquiry.pk,
                "channel": CommunicationChannel.EMAIL,
                "communication_type": CommunicationType.GENERAL,
                "subject": "Mismatch",
                "body": "Must not link.",
                "provider_status": "",
                "related_entity_type": "",
                "related_entity_id": "",
                "notes": "",
                "owner": self.user.pk,
            },
        )

        self.assertFalse(form.is_valid())
        self.assertIn("inquiry", form.errors)

    def test_deleting_inquiry_preserves_linked_communication(self):
        inquiry = self._create_inquiry("Historical communication")
        communication = Communication.objects.create(
            owner=self.user,
            recipient_type=RecipientType.PROSPECT,
            prospect=self.prospect,
            inquiry=inquiry,
            channel=CommunicationChannel.EMAIL,
            communication_type=CommunicationType.GENERAL,
            subject="Historical response",
            body="Preserve this message.",
        )

        inquiry.delete()
        communication.refresh_from_db()
        self.assertIsNone(communication.inquiry)
        self.assertTrue(Contact.objects.filter(pk=self.contact.pk).exists())

    def test_inquiry_archive_preserves_person_history_and_can_be_restored(self):
        inquiry = self._create_inquiry("Archive safely")
        communication = Communication.objects.create(
            owner=self.user,
            recipient_type=RecipientType.PROSPECT,
            prospect=self.prospect,
            inquiry=inquiry,
            channel=CommunicationChannel.EMAIL,
            communication_type=CommunicationType.GENERAL,
            subject="Preserved communication",
            body="Keep this history.",
        )

        archive_response = self.client.post(
            reverse("core:inquiry-archive", kwargs={"pk": inquiry.pk})
        )
        inquiry.refresh_from_db()
        communication.refresh_from_db()

        self.assertEqual(archive_response.status_code, 302)
        self.assertTrue(inquiry.is_archived)
        self.assertEqual(communication.inquiry, inquiry)
        self.assertTrue(Contact.objects.filter(pk=self.contact.pk).exists())
        self.assertNotContains(
            self.client.get(reverse("core:inquiry-list")),
            inquiry.inquiry_number,
        )
        self.assertContains(
            self.client.get(reverse("core:inquiry-list"), {"state": "archived"}),
            inquiry.inquiry_number,
        )

        restore_response = self.client.post(
            reverse("core:inquiry-restore", kwargs={"pk": inquiry.pk})
        )
        inquiry.refresh_from_db()
        self.assertEqual(restore_response.status_code, 302)
        self.assertFalse(inquiry.is_archived)

    def test_inquiry_hard_delete_route_is_not_exposed(self):
        inquiry = self._create_inquiry("No hard delete route")

        with self.assertRaises(NoReverseMatch):
            reverse("core:inquiry-delete", kwargs={"pk": inquiry.pk})

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

    def test_inquiry_form_creates_one_new_contact_and_links_new_inquiry(self):
        before_contacts = Contact.objects.count()
        form = InquiryForm(
            data={
                "contact_mode": "new",
                "contact": "",
                "new_first_name": "New",
                "new_last_name": "Caller",
                "new_email": "new.caller@example.com",
                "new_phone_number": "+1 416 555 0102",
                "prospect": "",
                "student": "",
                "inquiry_date": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
                "channel": "phone",
                "subject": "First inquiry",
                "message": "Course dates",
                "status": InquiryStatus.OPEN,
                "assigned_to": "",
                "owner": self.user.pk,
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        inquiry = form.save()

        self.assertEqual(Contact.objects.count(), before_contacts + 1)
        self.assertEqual(inquiry.contact.email, "new.caller@example.com")
        self.assertIsNotNone(inquiry.uuid)
        self.assertRegex(inquiry.inquiry_number, r"^INQ-\d{4}-\d{4,}$")

    def test_inquiry_form_reuses_duplicate_contact_identity(self):
        before_contacts = Contact.objects.count()
        form = InquiryForm(
            data={
                "contact_mode": "new",
                "contact": "",
                "new_first_name": "Akua",
                "new_last_name": "Inquiry",
                "new_email": "AKUA.INQUIRY@example.com",
                "new_phone_number": "",
                "prospect": "",
                "student": "",
                "inquiry_date": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
                "channel": "email",
                "subject": "Another inquiry",
                "message": "Advanced techniques",
                "status": InquiryStatus.OPEN,
                "assigned_to": "",
                "owner": self.user.pk,
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        inquiry = form.save()

        self.assertEqual(Contact.objects.count(), before_contacts)
        self.assertEqual(inquiry.contact, self.contact)

    def test_selecting_existing_contact_for_another_inquiry_does_not_create_contact(self):
        before_contacts = Contact.objects.count()
        first = self._create_inquiry("Existing person first")
        second = Inquiry.objects.create(
            contact=self.contact,
            inquiry_date=timezone.now(),
            subject="Existing person second",
            message="Another event",
        )

        self.assertEqual(Contact.objects.count(), before_contacts)
        self.assertEqual(first.contact, second.contact)
        self.assertNotEqual(first.uuid, second.uuid)
        self.assertNotEqual(first.inquiry_number, second.inquiry_number)

    def test_contact_to_prospect_conversion_preserves_previous_inquiries(self):
        contact = Contact.objects.create(
            first_name="Prospect",
            last_name="Journey",
            email="prospect.journey@example.com",
        )
        inquiry = Inquiry.objects.create(
            contact=contact,
            inquiry_date=timezone.now(),
            subject="Before prospect",
            message="Initial question",
        )
        original_uuid = inquiry.uuid

        prospect, created = contact.convert_to_prospect(owner=self.user)
        inquiry.refresh_from_db()

        self.assertTrue(created)
        self.assertEqual(prospect.contact, contact)
        self.assertEqual(inquiry.contact, contact)
        self.assertEqual(inquiry.uuid, original_uuid)
        self.assertTrue(Inquiry.objects.filter(pk=inquiry.pk).exists())

    def test_contact_to_student_conversion_preserves_previous_inquiries(self):
        contact = Contact.objects.create(
            first_name="Student",
            last_name="Journey",
            email="student.journey@example.com",
        )
        prospect, _ = contact.convert_to_prospect(owner=self.user)
        inquiry = Inquiry.objects.create(
            contact=contact,
            prospect=prospect,
            inquiry_date=timezone.now(),
            subject="Before student",
            message="Course question",
        )
        original_uuid = inquiry.uuid

        with patch(
            "core.services.prospect_conversion.validate_prospect_conversion_financial_eligibility"
        ):
            student, created = prospect.convert_to_student()
        inquiry.refresh_from_db()

        self.assertTrue(created)
        self.assertEqual(student.contact, contact)
        self.assertEqual(inquiry.contact, contact)
        self.assertEqual(inquiry.prospect, prospect)
        self.assertEqual(inquiry.uuid, original_uuid)
        self.assertTrue(Inquiry.objects.filter(pk=inquiry.pk).exists())

    def test_inquiry_create_page_prefills_searchable_existing_contact(self):
        response = self.client.get(
            reverse("core:inquiry-create"),
            {"contact": self.contact.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Search existing Contacts")
        self.assertContains(response, self.contact.full_name)

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
        archived = self._create_inquiry("Archived open event")
        archived.is_archived = True
        archived.save(update_fields=["is_archived", "updated_at"])

        dashboard = get_home_dashboard_data(user=self.user)

        self.assertEqual(dashboard["kpis"]["open_inquiries"], 2)


class InquiryArchivingMigrationTests(TransactionTestCase):
    """Existing Inquiry identity and history survive the additive archive migration."""

    migrate_from = [("core", "0043_communication_inquiry_relationship")]
    # End at the current migration leaf so later tests see the complete schema.
    migrate_to = [("core", "0047_enforce_lifecycle_crm_references")]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        Contact = old_apps.get_model("core", "Contact")
        OldInquiry = old_apps.get_model("core", "Inquiry")

        contact = Contact.objects.create(
            first_name="Migration",
            last_name="Survivor",
            email="migration.survivor@example.com",
        )
        self.contact_pk = contact.pk
        self.inquiry_uuid = uuid.uuid4()
        self.inquiry_number = "INQ-2026-9999"
        inquiry = OldInquiry.objects.create(
            uuid=self.inquiry_uuid,
            legacy_int_id=999999,
            inquiry_number=self.inquiry_number,
            contact=contact,
            inquiry_date=timezone.now(),
            channel="email",
            subject="Preserve migration record",
            message="Historical details must survive.",
            status="open",
        )
        self.created_at = inquiry.created_at

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def test_existing_inquiry_survives_archive_migration(self):
        MigratedInquiry = self.apps.get_model("core", "Inquiry")
        inquiry = MigratedInquiry.objects.get(pk=self.inquiry_uuid)

        self.assertEqual(inquiry.inquiry_number, self.inquiry_number)
        self.assertEqual(inquiry.contact_id, self.contact_pk)
        self.assertEqual(inquiry.subject, "Preserve migration record")
        self.assertEqual(inquiry.message, "Historical details must survive.")
        self.assertEqual(inquiry.status, "open")
        self.assertEqual(inquiry.created_at, self.created_at)
        self.assertFalse(inquiry.is_archived)
