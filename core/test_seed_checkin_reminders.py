from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.test import RequestFactory
from django.utils import timezone

from .management.commands.seed_checkin_reminders import ENROLLMENT_MARKER, SESSION_NAME
from .models import Contact, Course, CourseSession, Enrollment, Location, Prospect, Student, Teacher
from .services.home_dashboard import _build_student_check_in_reminders
from .views import HomeView


@override_settings(DEBUG=True)
class SeedCheckinRemindersCommandTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="safe-password-123",
        )
        Course.objects.create(name="TM Introductory Program")
        Teacher.objects.create(
            first_name="Demo",
            last_name="Teacher",
            email="demo.checkins.teacher@example.com",
        )
        Location.objects.create(name="Demo Check-in Center")
        for index in range(18):
            Student.objects.create(
                owner=self.owner,
                enrollment_status="active" if index % 2 else "enrolled",
                prospect=Prospect.objects.create(
                    owner=self.owner,
                    contact=Contact.objects.create(
                        first_name=f"Demo{index:02d}",
                        last_name="Checkin",
                    ),
                ),
            )

    def test_default_is_dry_run_and_execute_is_idempotent(self):
        dry_output = StringIO()
        call_command("seed_checkin_reminders", stdout=dry_output)
        self.assertIn("Dry run complete", dry_output.getvalue())
        self.assertEqual(Enrollment.objects.filter(notes__startswith=ENROLLMENT_MARKER).count(), 0)

        execute_output = StringIO()
        call_command("seed_checkin_reminders", "--execute", stdout=execute_output)
        self.assertIn("Dashboard verification passed", execute_output.getvalue())
        self.assertEqual(Enrollment.objects.filter(notes__startswith=ENROLLMENT_MARKER).count(), 15)
        self.assertEqual(CourseSession.objects.filter(session_name=SESSION_NAME).count(), 1)

        call_command("seed_checkin_reminders", "--execute", stdout=StringIO())
        self.assertEqual(Enrollment.objects.filter(notes__startswith=ENROLLMENT_MARKER).count(), 15)
        self.assertEqual(CourseSession.objects.filter(session_name=SESSION_NAME).count(), 1)

        reminders = _build_student_check_in_reminders(
            user=self.owner,
            today=timezone.localdate(),
        )
        due_counts = {
            day: sum(row["day_label"] == f"Day {day}" for row in reminders["due_today"])
            for day in (3, 10, 20)
        }
        overdue_counts = {
            day: sum(row["day_label"] == f"Day {day}" for row in reminders["overdue"])
            for day in (3, 10, 20)
        }
        self.assertEqual(due_counts, {3: 3, 10: 3, 20: 3})
        self.assertEqual(overdue_counts, {3: 2, 10: 2, 20: 2})
        reminder_ids = [
            row["student"].pk
            for row in reminders["due_today"] + reminders["overdue"]
        ]
        self.assertEqual(len(reminder_ids), len(set(reminder_ids)))

        request = RequestFactory().get("/")
        request.user = self.owner
        response = HomeView.as_view()(request)
        response.render()
        html = response.content.decode()
        self.assertContains(response, 'id="student-check-in-reminders"')
        self.assertContains(response, 'aria-label="9 reminders due today"')
        self.assertContains(response, 'aria-label="6 missed reminders"')
        self.assertContains(response, 'data-reminder-toggle="due-reminders"')
        self.assertContains(response, 'data-reminder-toggle="overdue-reminders"')
        self.assertIn("reminder-day day-3", html)
        self.assertIn("reminder-day day-10", html)
        self.assertIn("reminder-day day-20", html)
        self.assertIn("reminder-status is-today", html)
        self.assertIn("reminder-status is-overdue", html)
        self.assertIn("d-none reminder-extra", html)
