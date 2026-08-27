from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    Communication,
    Contact,
    Inquiry,
    Meditator,
    Prospect,
    Student,
    Teacher,
)


class FullNameSearchRegressionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="name_search_staff",
            password="safe-password-123",
            is_staff=True,
        )
        self.client.force_login(self.user)
        self.contact = Contact.objects.create(
            first_name="Ama",
            last_name="Boateng",
            email="ama.boateng@example.com",
        )
        self.prospect = Prospect.objects.create(
            owner=self.user,
            contact=self.contact,
        )
        self.student = Student.objects.create(
            owner=self.user,
            prospect=self.prospect,
        )
        self.meditator = Meditator.objects.create(student=self.student)
        self.inquiry = Inquiry.objects.create(
            owner=self.user,
            contact=self.contact,
            prospect=self.prospect,
            student=self.student,
            inquiry_date=timezone.now(),
            subject="Course information",
            message="Please send details.",
        )
        self.communication = Communication.objects.create(
            owner=self.user,
            recipient_type="prospect",
            prospect=self.prospect,
            channel="email",
            subject="Course follow-up",
            body="Requested details.",
        )
        self.governor = Teacher.objects.create(
            first_name="Nana",
            last_name="Governor",
            email="nana.governor@example.com",
        )
        self.other_contact = Contact.objects.create(
            first_name="Kojo",
            last_name="Mensah",
            email="kojo.mensah.reset@example.com",
        )
        self.other_prospect = Prospect.objects.create(
            owner=self.user,
            contact=self.other_contact,
        )
        self.other_student = Student.objects.create(
            owner=self.user,
            prospect=self.other_prospect,
        )
        self.other_meditator = Meditator.objects.create(student=self.other_student)

    def assert_list_search_finds(self, url_name, query, expected):
        response = self.client.get(reverse(url_name), {"q": query})
        self.assertEqual(response.status_code, 200)
        self.assertIn(expected, list(response.context["object_list"]))

    def test_contact_prospect_student_and_meditator_lists_match_full_name(self):
        for url_name, expected in (
            ("core:contact-list", self.contact),
            ("core:prospect-list", self.prospect),
            ("core:student-list", self.student),
            ("core:meditator-list", self.meditator),
        ):
            with self.subTest(url_name=url_name):
                self.assert_list_search_finds(url_name, "Ama Boateng", expected)

    def test_inquiry_and_communication_lists_match_recipient_full_name(self):
        for url_name, expected in (
            ("core:inquiry-list", self.inquiry),
            ("core:communication-list", self.communication),
        ):
            with self.subTest(url_name=url_name):
                self.assert_list_search_finds(url_name, "Ama Boateng", expected)

    def test_governor_list_matches_full_name(self):
        self.assert_list_search_finds(
            "core:teacher-list",
            "Nana Governor",
            self.governor,
        )

    def test_prospect_pipeline_matches_full_name(self):
        response = self.client.get(
            reverse("core:prospect-pipeline-list"),
            {"q": "Ama Boateng"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.prospect, list(response.context["object_list"]))

    def test_contact_autocomplete_matches_full_name(self):
        response = self.client.get(
            reverse("core:contact-autocomplete"),
            {"q": "Ama Boateng"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [result["id"] for result in response.json()["results"]],
            [self.contact.pk],
        )

    def test_each_name_term_may_match_a_different_field(self):
        response = self.client.get(
            reverse("core:contact-list"),
            {"q": "Boateng Ama"},
        )
        self.assertIn(self.contact, list(response.context["object_list"]))

    def test_all_name_terms_are_required(self):
        response = self.client.get(
            reverse("core:contact-list"),
            {"q": "Ama Missing"},
        )
        self.assertNotIn(self.contact, list(response.context["object_list"]))

    def test_empty_and_whitespace_search_return_the_full_contact_queryset(self):
        expected = {self.contact, self.other_contact}
        for query in ("", "   "):
            with self.subTest(query=query):
                response = self.client.get(reverse("core:contact-list"), {"q": query})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(set(response.context["object_list"]), expected)
                self.assertNotIn("q=", response.context["querystring_without_page"])

    def test_empty_and_whitespace_search_return_all_meditators(self):
        expected = {self.meditator, self.other_meditator}
        for query in ("", "   "):
            with self.subTest(query=query):
                response = self.client.get(reverse("core:meditator-list"), {"q": query})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(set(response.context["object_list"]), expected)
                self.assertNotIn("q=", response.context["querystring_without_page"])

    def test_empty_and_whitespace_search_return_the_full_pipeline(self):
        expected = {self.prospect, self.other_prospect}
        for query in ("", "   "):
            with self.subTest(query=query):
                response = self.client.get(
                    reverse("core:prospect-pipeline-list"),
                    {"q": query},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(set(response.context["object_list"]), expected)

    def test_clear_filters_uses_canonical_urls_and_returns_full_queryset(self):
        filtered = self.client.get(
            reverse("core:contact-list"),
            {"q": "Ama", "has_email": "yes", "page": "1"},
        )
        self.assertEqual(list(filtered.context["object_list"]), [self.contact])
        self.assertContains(
            filtered,
            f'href="{reverse("core:contact-list")}">Clear filters</a>',
            html=False,
        )

        cleared = self.client.get(reverse("core:contact-list"))
        self.assertEqual(
            set(cleared.context["object_list"]),
            {self.contact, self.other_contact},
        )
        self.assertEqual(cleared.context["page_obj"].number, 1)

    def test_searchable_lists_use_the_shared_reset_behavior(self):
        url_names = (
            "core:prospect-list",
            "core:contact-list",
            "core:student-list",
            "core:teacher-list",
            "core:location-list",
            "core:course-list",
            "core:coursesession-list",
            "core:inquiry-list",
            "core:enrollment-list",
            "core:invoice-list",
            "core:payment-list",
            "core:communication-list",
            "core:interviewform-list",
            "core:disbursement-list",
            "core:meditator-list",
            "core:prospect-pipeline-list",
        )
        for url_name in url_names:
            with self.subTest(url_name=url_name):
                canonical_url = reverse(url_name)
                response = self.client.get(canonical_url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "js-list-search-form")
                self.assertContains(response, f'data-clear-url="{canonical_url}"')

    def test_prospect_clear_filters_does_not_preserve_state_or_page(self):
        response = self.client.get(
            reverse("core:prospect-list"),
            {"q": "Ama", "state": "all", "page": "1"},
        )
        canonical_clear_link = (
            f'href="{reverse("core:prospect-list")}">Clear filters</a>'
        )
        self.assertContains(response, canonical_clear_link, html=False)
        self.assertNotContains(
            response,
            f'{reverse("core:prospect-list")}?state=all">Clear filters</a>',
            html=False,
        )
