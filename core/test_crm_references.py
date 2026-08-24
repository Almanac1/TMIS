import re
import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Contact, Meditator, Prospect, Student


class CRMReferenceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="crm-reference-admin",
            email="crm-reference-admin@example.com",
            password="safe-password-123",
        )
        self.client.force_login(self.user)
        self.contact = Contact.objects.create(
            first_name="Abena",
            last_name="Reference",
            email="abena.reference@example.com",
        )
        self.prospect = Prospect.objects.create(contact=self.contact, owner=self.user)
        self.student = Student.objects.create(prospect=self.prospect, owner=self.user)
        self.meditator = Meditator.objects.create(student=self.student)

    def test_lifecycle_records_receive_distinct_human_readable_references(self):
        year = timezone.localdate().year
        expected = (
            (self.contact, "CNT"),
            (self.prospect, "PRO"),
            (self.student, "STU"),
            (self.meditator, "MED"),
        )

        for record, prefix in expected:
            self.assertRegex(record.crm_reference, rf"^{prefix}-{year}-\d{{4,}}$")
            self.assertEqual(record.public_id, record.crm_reference)
            self.assertNotEqual(record.crm_reference, str(record.pk))
            self.assertNotEqual(record.crm_reference, str(record.uuid))

        self.assertEqual(len({record.crm_reference for record, _prefix in expected}), 4)
        self.assertEqual(self.student.contact, self.contact)
        self.assertEqual(self.meditator.contact, self.contact)

    def test_references_are_sequential_and_unique_within_record_type(self):
        second = Contact.objects.create(
            first_name="Kojo",
            last_name="Reference",
            email="kojo.reference@example.com",
        )

        first_number = int(self.contact.crm_reference.rsplit("-", 1)[1])
        second_number = int(second.crm_reference.rsplit("-", 1)[1])
        self.assertEqual(second_number, first_number + 1)
        self.assertNotEqual(second.crm_reference, self.contact.crm_reference)

    def test_references_are_immutable(self):
        for record in (self.contact, self.prospect, self.student, self.meditator):
            original_reference = record.crm_reference
            record.crm_reference = f"BAD-{uuid.uuid4()}"
            with self.assertRaises(ValidationError):
                record.save()
            record.refresh_from_db()
            self.assertEqual(record.crm_reference, original_reference)

    def test_pk_uuid_and_crm_reference_urls_resolve_the_same_records(self):
        targets = (
            ("core:contact-detail", self.contact),
            ("core:prospect-detail", self.prospect),
            ("core:student-detail", self.student),
            ("core:meditator-detail", self.meditator),
        )

        for route_name, record in targets:
            for identifier in (record.pk, record.uuid, record.crm_reference):
                response = self.client.get(
                    reverse(route_name, kwargs={"pk": str(identifier)})
                )
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, record.crm_reference)

    def test_lists_search_and_display_crm_references(self):
        targets = (
            ("core:contact-list", self.contact),
            ("core:prospect-list", self.prospect),
            ("core:student-list", self.student),
            ("core:meditator-list", self.meditator),
        )

        for route_name, record in targets:
            response = self.client.get(
                reverse(route_name),
                {"q": record.crm_reference, "state": "all"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, record.crm_reference)

    def test_reference_fields_are_internal_and_not_user_editable(self):
        for model in (Contact, Prospect, Student, Meditator):
            field = model._meta.get_field("crm_reference")
            self.assertFalse(field.editable)
            self.assertTrue(field.unique)


class CRMReferenceBackfillMigrationTests(TransactionTestCase):
    migrate_from = [("core", "0045_add_lifecycle_crm_reference_fields")]
    migrate_to = [("core", "0047_enforce_lifecycle_crm_references")]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps

        ContactModel = old_apps.get_model("core", "Contact")
        ProspectModel = old_apps.get_model("core", "Prospect")
        StudentModel = old_apps.get_model("core", "Student")
        MeditatorModel = old_apps.get_model("core", "Meditator")

        contact = ContactModel.objects.create(
            first_name="Migration",
            last_name="Contact",
            email="migration.crm.reference@example.com",
        )
        prospect = ProspectModel.objects.create(contact=contact)
        student = StudentModel.objects.create(prospect=prospect)
        meditator = MeditatorModel.objects.create(student=student)

        self.identities = {
            "Contact": (contact.pk, contact.uuid, "CNT"),
            "Prospect": (prospect.pk, prospect.uuid, "PRO"),
            "Student": (student.pk, student.uuid, "STU"),
            "Meditator": (meditator.pk, meditator.uuid, "MED"),
        }

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def test_backfill_preserves_pk_uuid_and_relationships(self):
        references = set()
        for model_name, (pk, stable_uuid, prefix) in self.identities.items():
            Model = self.apps.get_model("core", model_name)
            record = Model.objects.get(pk=pk)
            self.assertEqual(record.uuid, stable_uuid)
            self.assertTrue(
                re.fullmatch(rf"{prefix}-\d{{4}}-\d{{4,}}", record.crm_reference)
            )
            references.add(record.crm_reference)

        self.assertEqual(len(references), 4)
        ProspectModel = self.apps.get_model("core", "Prospect")
        StudentModel = self.apps.get_model("core", "Student")
        MeditatorModel = self.apps.get_model("core", "Meditator")
        self.assertEqual(
            ProspectModel.objects.get(pk=self.identities["Prospect"][0]).contact_id,
            self.identities["Contact"][0],
        )
        self.assertEqual(
            StudentModel.objects.get(pk=self.identities["Student"][0]).prospect_id,
            self.identities["Prospect"][0],
        )
        self.assertEqual(
            MeditatorModel.objects.get(pk=self.identities["Meditator"][0]).student_id,
            self.identities["Student"][0],
        )
