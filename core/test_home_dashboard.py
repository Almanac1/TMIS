from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import (
    Communication,
    CommunicationChannel,
    CommunicationType,
    Contact,
    Course,
    CourseSession,
    DeliveryStatus,
    Enrollment,
    EnrollmentStatus,
    Invoice,
    InvoiceStatus,
    Location,
    Payment,
    PaymentConfirmationStatus,
    Prospect,
    RecipientType,
    Student,
    Teacher,
)
from core.services.home_dashboard import get_home_dashboard_data


class HomeDashboardLayoutTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="dashboard-governor", password="safe-password-123"
        )
        self.other_user = user_model.objects.create_user(username="other-dashboard-governor")
        self.teacher = Teacher.objects.create(
            user=self.user,
            first_name="Ama",
            last_name="Dashboard",
            email="ama.dashboard@example.com",
        )
        self.other_teacher = Teacher.objects.create(
            user=self.other_user,
            first_name="Kojo",
            last_name="Elsewhere",
            email="kojo.elsewhere@example.com",
        )
        self.location = Location.objects.create(name="Dashboard Test Centre")
        self.course = Course.objects.create(
            name="Dashboard Analytics Course", standard_fee=Decimal("1000.00")
        )
        self.intro_course, _ = Course.objects.get_or_create(
            name="TM Introductory Program",
            defaults={"standard_fee": Decimal("1000.00")},
        )
        self.enrollment = self._create_enrollment(
            teacher=self.teacher,
            course=self.course,
            email="visible.student@example.com",
            first_name="Visible",
            fee=Decimal("1000.00"),
            paid=Decimal("400.00"),
        )
        self.other_enrollment = self._create_enrollment(
            teacher=self.other_teacher,
            course=self.course,
            email="hidden.student@example.com",
            first_name="Hidden",
            fee=Decimal("800.00"),
            paid=Decimal("800.00"),
        )

    def _create_enrollment(self, *, teacher, course, email, first_name, fee, paid=None):
        owner = teacher.user
        prospect = Prospect.objects.create(
            owner=owner,
            teacher=teacher,
            contact=Contact.objects.create(
                first_name=first_name,
                last_name="Student",
                email=email,
            ),
        )
        student = Student.objects.create(
            owner=owner,
            prospect=prospect,
            teacher=teacher,
            enrollment_status=EnrollmentStatus.ENROLLED,
        )
        session = CourseSession.objects.create(
            owner=owner,
            course=course,
            teacher=teacher,
            session_name=f"{first_name} Session",
            start_date=timezone.now() + timedelta(days=3),
            end_date=timezone.now() + timedelta(days=4),
            location=self.location,
        )
        enrollment = Enrollment.objects.create(
            student=student,
            course=course,
            session=session,
            enrollment_date=timezone.now() - timedelta(days=1),
            status=EnrollmentStatus.ENROLLED,
            fee_amount=fee,
        )
        invoice = Invoice.objects.create(
            owner=owner,
            enrollment=enrollment,
            invoice_number=f"DASH-{first_name.upper()}",
            issue_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=14),
            subtotal=fee,
            total_amount=fee,
            status=InvoiceStatus.SENT,
        )
        if paid is not None:
            Payment.objects.create(
                owner=owner,
                invoice=invoice,
                payment_date=timezone.now(),
                amount_paid=paid,
                payment_method="transfer",
                confirmation_status=PaymentConfirmationStatus.CONFIRMED,
            )
        return enrollment

    def test_dashboard_data_uses_real_scoped_enrollments_and_payments(self):
        data = get_home_dashboard_data(user=self.user)

        course_chart = data["charts"]["enrollments_by_course"]
        course_index = course_chart["labels"].index(self.course.name)
        self.assertEqual(course_chart["values"][course_index], 1)
        self.assertEqual(data["kpis"]["invoiced_total"], 1000.0)
        self.assertEqual(data["kpis"]["collected_total"], 400.0)
        self.assertEqual(
            set(data["charts"]), {"enrollments_by_course", "revenue_trend"}
        )

    def test_checkin_rows_include_real_course_and_governor_context(self):
        intro = self._create_enrollment(
            teacher=self.teacher,
            course=self.intro_course,
            email="checkin.student@example.com",
            first_name="Checkin",
            fee=Decimal("1000.00"),
        )
        Enrollment.objects.filter(pk=intro.pk).update(
            enrollment_date=timezone.now() - timedelta(days=3)
        )

        reminders = get_home_dashboard_data(user=self.user)["check_in_reminders"]

        due = next(
            item for item in reminders["due_today"] if item["student"].pk == intro.student_id
        )
        self.assertEqual(due["course"], self.intro_course)
        self.assertEqual(due["governor"], self.teacher)
        self.assertEqual(due["day_label"], "Day 3")

    def test_attention_widget_uses_last_contact_attempts_and_next_action(self):
        prospect = self.enrollment.student.prospect
        Communication.objects.create(
            owner=self.user,
            recipient_type=RecipientType.PROSPECT,
            prospect=prospect,
            channel=CommunicationChannel.EMAIL,
            communication_type=CommunicationType.FOLLOW_UP,
            subject="Follow up",
            body="Checking in",
            sent_at=timezone.now() - timedelta(days=2),
            delivery_status=DeliveryStatus.SENT,
        )

        attention = get_home_dashboard_data(user=self.user)["operations"]["attention_prospects"]
        row = next(item for item in attention if item.pk == prospect.pk)

        self.assertEqual(row.contact_attempts, 1)
        self.assertIsNotNone(row.last_contact)
        self.assertEqual(row.dashboard_next_action, "Follow up")

    def test_home_renders_two_column_widgets_charts_and_live_links(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("core:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="dashboard-layout"')
        self.assertEqual(response.content.decode().count("dashboard-kpi-card"), 4)
        self.assertContains(response, "Enrollment Analytics")
        self.assertContains(response, "Revenue &amp; Payments Overview")
        self.assertContains(response, "Student Check-ins Due")
        self.assertContains(response, "Upcoming Sessions / Meetings")
        self.assertContains(response, "Follow-ups Requiring Attention")
        self.assertContains(response, 'id="enrollmentCourseChart"')
        self.assertContains(response, 'id="revenueTrendChart"')
        self.assertNotContains(response, "prospectFunnelChart")
        self.assertNotContains(response, "followUpHealthChart")
        self.assertNotContains(response, "governorCompensationChart")
        self.assertContains(response, "Total Compensation Due")
