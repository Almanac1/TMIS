from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    Contact,
    Course,
    CourseSession,
    Enrollment,
    EnrollmentStatus,
    Location,
    Prospect,
    SessionStatus,
    Student,
    Teacher,
)


class EnrollmentStudentAutocompleteTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="enrollment_search_user",
            password="safe-password-123",
        )
        self.other_user = get_user_model().objects.create_user(
            username="other_enrollment_search_user",
            password="safe-password-123",
        )
        self.client.force_login(self.user)
        self.teacher = Teacher.objects.create(
            first_name="Search",
            last_name="Teacher",
            email="search.teacher@example.com",
        )
        self.location = Location.objects.create(name="Autocomplete Test Center")
        self.course = Course.objects.create(
            name="TM Autocomplete Test",
            standard_fee=Decimal("500.00"),
        )
        self.session = self._make_session(self.course, "Autocomplete Session")
        self.student = self._make_student(
            self.user,
            first_name="Amara",
            last_name="Coleman",
            email="amara@example.com",
            phone="+1 416 555 0199",
        )
        self.other_student = self._make_student(
            self.other_user,
            first_name="Amara",
            last_name="Coleman Other Tenant",
            email="other-amara@example.com",
        )

    def _make_student(self, owner, *, first_name, last_name, email="", phone=""):
        contact = Contact.objects.create(
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone_number=phone,
        )
        prospect = Prospect.objects.create(owner=owner, contact=contact)
        return Student.objects.create(owner=owner, prospect=prospect)

    def _make_session(self, course, name):
        return CourseSession.objects.create(
            owner=self.user,
            course=course,
            teacher=self.teacher,
            session_name=name,
            start_date=timezone.now() + timedelta(days=2),
            end_date=timezone.now() + timedelta(days=3),
            location=self.location,
            status=SessionStatus.SCHEDULED,
        )

    def _search(self, query, *, course=None, session=None):
        return self.client.get(
            reverse("core:enrollment-person-search"),
            {
                "type": "student",
                "q": query,
                "course_id": (course or self.course).pk,
                "session_id": (session or self.session).pk,
            },
        )

    def _payload(self, student, *, course=None, session=None):
        selected_course = course or self.course
        selected_session = session or self.session
        return {
            "person_type": "student",
            "student": student.pk,
            "prospect": "",
            "contact": "",
            "course": selected_course.pk,
            "session": selected_session.pk,
            "enrollment_date": timezone.localdate().isoformat(),
            "status": EnrollmentStatus.ENROLLED,
            "fee_amount": "500.00",
            "discount_amount": "0.00",
            "balance_due": "500.00",
            "number_of_children_under_18": 0,
        }

    def test_create_page_renders_hidden_student_id_without_global_options(self):
        response = self.client.get(reverse("core:enrollment-create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="id_student"')
        self.assertContains(response, 'type="hidden"')
        self.assertContains(response, "Search for a student...")
        self.assertNotContains(response, '<select name="student"')
        self.assertNotContains(response, "Amara Coleman")

    def test_search_requires_two_characters_and_a_course(self):
        short_response = self._search("A")
        missing_course_response = self.client.get(
            reverse("core:enrollment-person-search"),
            {"type": "student", "q": "Amara"},
        )

        self.assertEqual(short_response.json()["results"], [])
        self.assertEqual(missing_course_response.json()["results"], [])
        self.assertIn("Select a course", missing_course_response.json()["message"])

    def test_search_matches_full_name_email_and_phone_with_context(self):
        for query in ("Amara Coleman", "amara@example.com", "555 0199"):
            with self.subTest(query=query):
                payload = self._search(query).json()
                self.assertEqual([item["id"] for item in payload["results"]], [self.student.pk])
                self.assertIn("Prospect #", payload["results"][0]["badge"])
                self.assertIn("amara@example.com", payload["results"][0]["meta"])

    def test_search_is_owner_scoped_and_limited(self):
        for index in range(18):
            self._make_student(
                self.user,
                first_name="Limit",
                last_name=f"Candidate {index:02d}",
                email=f"limit{index}@example.com",
            )

        owner_payload = self._search("Other Tenant").json()
        limited_payload = self._search("Limit Candidate").json()

        self.assertEqual(owner_payload["results"], [])
        self.assertEqual(len(limited_payload["results"]), 15)

    def test_search_reuses_course_eligibility_and_excludes_duplicate_session(self):
        target_course = Course.objects.create(name="Advanced Technique 1 Search Test")
        target_session = self._make_session(target_course, "AT1 Search Session")
        eligible = self._make_student(
            self.user,
            first_name="Eligible",
            last_name="Coleman",
            email="eligible@example.com",
        )
        ineligible = self._make_student(
            self.user,
            first_name="Ineligible",
            last_name="Coleman",
            email="ineligible@example.com",
        )
        tm_course = Course.objects.create(name="TM - adult Search Prerequisite")
        tm_session = self._make_session(tm_course, "TM Prerequisite Session")
        Enrollment.objects.create(
            student=eligible,
            course=tm_course,
            session=tm_session,
            enrollment_date=timezone.now(),
        )

        eligible_ids = [
            item["id"]
            for item in self._search("Coleman", course=target_course, session=target_session).json()["results"]
        ]
        self.assertIn(eligible.pk, eligible_ids)
        self.assertNotIn(ineligible.pk, eligible_ids)

        Enrollment.objects.create(
            student=eligible,
            course=target_course,
            session=target_session,
            enrollment_date=timezone.now(),
        )
        duplicate_ids = [
            item["id"]
            for item in self._search("Coleman", course=target_course, session=target_session).json()["results"]
        ]
        self.assertNotIn(eligible.pk, duplicate_ids)

    def test_post_rejects_ineligible_or_other_tenant_student(self):
        target_course = Course.objects.create(name="Advanced Technique 1 POST Test")
        target_session = self._make_session(target_course, "AT1 POST Session")

        ineligible_response = self.client.post(
            reverse("core:enrollment-create"),
            self._payload(self.student, course=target_course, session=target_session),
        )
        other_tenant_response = self.client.post(
            reverse("core:enrollment-create"),
            self._payload(self.other_student),
        )

        self.assertEqual(ineligible_response.status_code, 200)
        self.assertContains(ineligible_response, "must first enroll")
        self.assertEqual(other_tenant_response.status_code, 200)
        self.assertContains(other_tenant_response, "unavailable or invalid")
        self.assertFalse(Enrollment.objects.filter(student__in=[self.student, self.other_student]).exists())

    def test_selected_student_primary_key_is_saved(self):
        response = self.client.post(
            reverse("core:enrollment-create"),
            self._payload(self.student),
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Enrollment.objects.filter(
                student_id=self.student.pk,
                session=self.session,
            ).exists()
        )
