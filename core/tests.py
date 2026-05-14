from django.contrib.auth import get_user_model
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.core import mail
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from .models import (
    Communication,
    Contact,
    Course,
    CourseFormat,
    CourseSession,
    CourseStatus,
    Enrollment,
    EnrollmentStatus,
    Location,
    Prospect,
    ProspectStatus,
    SessionStatus,
    Student,
    Teacher,
)


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

        duplicate_candidate = Prospect.objects.create(
            contact=Contact.objects.create(
                first_name="Liam",
                last_name="Mensah",
                email="liam.two@example.com",
                phone_number="5551012020",
            ),
        )

        with self.assertRaises(ValidationError):
            duplicate_candidate.convert_to_student()

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

        student, created = coincidental_name_match.convert_to_student()
        self.assertTrue(created)
        self.assertIsNotNone(student.pk)
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
        self.assertTrue(self.contact.converted_to_prospect)
        self.assertEqual(self.contact.converted_prospect_id, prospect.pk)
        self.assertIsNotNone(self.contact.converted_at)

    def test_convert_contact_to_prospect_is_idempotent(self):
        Prospect.objects.create(owner=self.user, contact=self.contact, status=ProspectStatus.NEW)
        response = self.client.get(
            reverse("core:contact-convert-to-prospect", kwargs={"pk": self.contact.pk}),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Prospect.objects.filter(contact=self.contact).count(), 1)
        self.contact.refresh_from_db()
        self.assertTrue(self.contact.converted_to_prospect)
        self.assertIsNotNone(self.contact.converted_prospect_id)

    def test_converted_contact_shows_open_prospect_action(self):
        prospect = Prospect.objects.create(owner=self.user, contact=self.contact, status=ProspectStatus.NEW)
        response = self.client.get(reverse("core:contact-list"))
        self.assertContains(response, reverse("core:prospect-detail", kwargs={"pk": prospect.pk}))
        self.assertContains(response, "Converted to Prospect")
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


class ProspectEditPersistsContactTests(TestCase):
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

    def test_prospect_edit_updates_linked_contact_email(self):
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
        self.assertEqual(self.contact.email, "kofi.new@example.com")


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
            name="TM Intro Program",
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

    def test_discount_cannot_exceed_fee(self):
        response = self.client.post(
            reverse("core:enrollment-create"),
            data=self._payload(fee="100.00", discount="120.00"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Discount amount cannot exceed fee amount.")


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
