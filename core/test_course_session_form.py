from datetime import datetime

from django.test import SimpleTestCase

from core.forms import CourseSessionForm
from core.views import CourseSessionCreateView, CourseSessionUpdateView


class CourseSessionFormWidgetTests(SimpleTestCase):
    def test_start_and_end_dates_use_native_date_inputs(self):
        form = CourseSessionForm()

        for field_name in ("start_date", "end_date"):
            self.assertEqual(form.fields[field_name].widget.input_type, "date")
            self.assertIn('type="date"', str(form[field_name]))

    def test_date_inputs_are_parsed_as_session_datetimes(self):
        form = CourseSessionForm()

        parsed = form.fields["start_date"].clean("2026-08-25")

        self.assertIsInstance(parsed, datetime)
        self.assertEqual(parsed.date().isoformat(), "2026-08-25")

    def test_create_and_update_views_use_course_session_form(self):
        self.assertIs(CourseSessionCreateView.form_class, CourseSessionForm)
        self.assertIs(CourseSessionUpdateView.form_class, CourseSessionForm)
