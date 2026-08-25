from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from core.models import Location


class LocationAdminManagementTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin_user = user_model.objects.create_superuser(
            username="location-admin",
            email="location-admin@example.com",
            password="safe-password-123",
        )
        self.regular_user = user_model.objects.create_user(
            username="location-user",
            password="safe-password-123",
        )
        self.location = Location.objects.create(name="Admin Managed Centre")

    def test_location_addition_and_removal_are_available_in_admin(self):
        self.assertTrue(admin.site.is_registered(Location))
        self.client.force_login(self.admin_user)

        add_response = self.client.get(reverse("admin:core_location_add"))
        delete_response = self.client.get(
            reverse("admin:core_location_delete", args=[self.location.pk])
        )

        self.assertEqual(add_response.status_code, 200)
        self.assertEqual(delete_response.status_code, 200)

    def test_location_create_and_delete_routes_are_not_exposed_in_crm(self):
        with self.assertRaises(NoReverseMatch):
            reverse("core:location-create")
        with self.assertRaises(NoReverseMatch):
            reverse("core:location-delete", args=[self.location.pk])

    def test_staff_location_list_links_to_admin_management(self):
        self.client.force_login(self.admin_user)

        response = self.client.get(reverse("core:location-list"))

        self.assertContains(response, "Manage Locations")
        self.assertContains(response, reverse("admin:core_location_changelist"))
        self.assertNotContains(response, "Create Location")
        self.assertNotContains(response, ">Delete</a>", html=False)

    def test_regular_user_can_view_locations_without_admin_controls(self):
        self.client.force_login(self.regular_user)

        response = self.client.get(reverse("core:location-list"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Manage Locations")
        self.assertNotContains(response, "Create Location")

