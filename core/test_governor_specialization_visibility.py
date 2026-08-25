from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from core.models import Teacher


class GovernorSpecializationVisibilityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="governor-ui-admin",
            email="governor-ui-admin@example.com",
            password="safe-password-123",
        )
        self.client.force_login(self.user)
        self.teacher = Teacher.objects.create(
            first_name="Ama",
            last_name="Mensah",
            email="ama.mensah@example.com",
        )

    def test_specialization_catalog_is_not_exposed_as_product_crud(self):
        with self.assertRaises(NoReverseMatch):
            reverse("core:teacherspecialization-list")

        response = self.client.get(reverse("core:teacher-list"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Governor Specializations")

    def test_governor_forms_and_detail_hide_specializations(self):
        create_response = self.client.get(reverse("core:teacher-create"))
        update_response = self.client.get(
            reverse("core:teacher-update", kwargs={"pk": self.teacher.pk})
        )
        detail_response = self.client.get(
            reverse("core:teacher-detail", kwargs={"pk": self.teacher.pk})
        )

        self.assertNotIn("specializations", create_response.context["form"].fields)
        self.assertNotIn("specializations", update_response.context["form"].fields)
        self.assertNotContains(detail_response, "Specializations")
