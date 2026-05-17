from __future__ import annotations

import random
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import (
    Communication,
    CommunicationChannel,
    CommunicationType,
    Contact,
    ContactMethod,
    Course,
    CourseFormat,
    CourseSession,
    CourseStatus,
    DeliveryStatus,
    Disbursement,
    DisbursementStatus,
    Enrollment,
    EnrollmentStatus,
    Inquiry,
    InquiryChannel,
    InquiryStatus,
    InterestLevel,
    InterviewForm,
    InterviewStatus,
    Invoice,
    InvoiceStatus,
    Location,
    Payment,
    PaymentConfirmationStatus,
    PaymentMethod,
    Prospect,
    ProspectStatus,
    RecipientType,
    SessionStatus,
    Student,
    Teacher,
    TeacherSpecialization,
    TeacherSpecializationName,
    TeacherStatus,
)
from core.services.meditator_transitions import backfill_fictitious_meditator_transitions


class Command(BaseCommand):
    help = "Seed the CRM with realistic fictional data."

    FIRST_NAMES = [
        "Ava",
        "Noah",
        "Liam",
        "Olivia",
        "Maya",
        "Ethan",
        "Sophia",
        "Lucas",
        "Aria",
        "Mason",
        "Amara",
        "Henry",
        "Elena",
        "Daniel",
        "Grace",
        "Nathan",
        "Iris",
        "Samuel",
        "Nora",
        "Julian",
    ]

    LAST_NAMES = [
        "Mensah",
        "Boateng",
        "Owusu",
        "Bennett",
        "Taylor",
        "Miller",
        "Johnson",
        "Anderson",
        "Clark",
        "Parker",
        "Reid",
        "Coleman",
        "Grant",
        "Rivers",
        "Carter",
        "Khan",
        "Singh",
        "Patel",
        "Nguyen",
        "Lopez",
    ]

    LOCATIONS = [
        ("Accra East", "Accra", "Greater Accra", "Ghana"),
        ("Accra North", "Accra", "Greater Accra", "Ghana"),
        ("Orgle Lodge", "Accra", "Greater Accra", "Ghana"),
        ("Damatonu", "Accra", "Greater Accra", "Ghana"),
    ]

    COURSE_NAMES = [
        "TM Introductory Program",
        "TM Core 4-Day Course",
        "Advanced Technique I",
        "Advanced Technique II",
    ]

    def add_arguments(self, parser):
        parser.add_argument(
            "--count",
            type=int,
            default=1000,
            help="Number of fictional prospects to create (default: 1000).",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=20260412,
            help="Random seed for repeatable generation.",
        )
        parser.add_argument(
            "--owners",
            type=int,
            default=5,
            help="Number of fictitious non-admin owners to ensure (default: 5).",
        )

    def handle(self, *args, **options):
        count = options["count"]
        seed = options["seed"]
        owner_count = options["owners"]

        if count <= 0:
            self.stdout.write(self.style.ERROR("--count must be greater than 0."))
            return

        rng = random.Random(seed)
        run_tag = timezone.now().strftime("%Y%m%d%H%M%S")

        self.stdout.write(
            f"Seeding fictional CRM data: count={count}, seed={seed}, run_tag={run_tag}"
        )

        with transaction.atomic():
            owner_users = self._ensure_fictitious_users(owner_count)
            teachers = self._ensure_teachers(rng, run_tag)
            locations = self._ensure_locations()
            courses = self._ensure_courses(rng)
            sessions = self._create_sessions(
                rng, teachers, locations, courses, run_tag, owner_users
            )
            users = list(get_user_model().objects.order_by("id")[:10])

            created = {
                "prospects": 0,
                "students": 0,
                "inquiries": 0,
                "enrollments": 0,
                "interviews": 0,
                "invoices": 0,
                "payments": 0,
                "communications": 0,
                "disbursements": 0,
                "sessions": len(sessions),
                "teachers": len(teachers),
                "locations": len(locations),
                "courses": len(courses),
            }

            for i in range(1, count + 1):
                owner = self._pick_owner(rng, owner_users)
                prospect = self._create_prospect(rng, run_tag, i, owner)
                created["prospects"] += 1

                if rng.random() < 0.82:
                    student = self._create_student_from_prospect(rng, prospect, owner)
                    created["students"] += 1

                    enrollment_count = 1 if rng.random() < 0.85 else 0
                    if rng.random() < 0.18:
                        enrollment_count = 2

                    selected_sessions = rng.sample(
                        sessions,
                        k=min(enrollment_count, len(sessions)),
                    )

                    for session in selected_sessions:
                        enrollment = self._create_enrollment(rng, student, session)
                        created["enrollments"] += 1

                        if rng.random() < 0.78:
                            invoice = self._create_invoice(rng, enrollment)
                            created["invoices"] += 1

                            payment_count = 0
                            roll = rng.random()
                            if roll < 0.45:
                                payment_count = 1
                            elif roll < 0.70:
                                payment_count = 2

                            payment_total = Decimal("0.00")
                            for payment_index in range(payment_count):
                                payment = self._create_payment(
                                    rng,
                                    invoice,
                                    payment_total,
                                    payment_index,
                                )
                                if payment is None:
                                    break
                                payment_total += payment.amount_paid
                                created["payments"] += 1

                            self._update_invoice_status(invoice, payment_total)

                        if rng.random() < 0.66:
                            Disbursement.objects.create(
                                enrollment=enrollment,
                                teacher=session.teacher,
                                location=session.location,
                                balance_due_snapshot=enrollment.balance_due,
                                teacher_amount=Decimal("0.00"),
                                location_amount=Decimal("0.00"),
                                ico_amount=Decimal("0.00"),
                                disbursement_date=(
                                    enrollment.enrollment_date + timedelta(days=7)
                                ).date(),
                                status=rng.choice(list(DisbursementStatus.values)),
                                notes="Auto-generated fictional payout record.",
                            )
                            created["disbursements"] += 1

                        if rng.random() < 0.34:
                            InterviewForm.objects.create(
                                student=student,
                                teacher=session.teacher,
                                session=session,
                                submitted_at=enrollment.enrollment_date + timedelta(days=2),
                                summary="Fictional interview summary for demo environment.",
                                recommendation="Continue with daily practice and follow-up sessions.",
                                status=rng.choice(list(InterviewStatus.values)),
                                notes="Generated by seed_fake_crm command.",
                            )
                            created["interviews"] += 1

                        if rng.random() < 0.58:
                            Communication.objects.create(
                                owner=owner,
                                recipient_type=RecipientType.STUDENT,
                                student=student,
                                enrollment=enrollment,
                                channel=rng.choice(list(CommunicationChannel.values)),
                                communication_type=rng.choice(list(CommunicationType.values)),
                                subject="TM Program Update",
                                body="This is a fictional communication generated for CRM testing.",
                                sent_at=timezone.now() - timedelta(days=rng.randint(0, 60)),
                                delivery_status=rng.choice(list(DeliveryStatus.values)),
                                provider_status="simulated",
                                related_entity_type="Enrollment",
                                related_entity_id=enrollment.id,
                                notes="Generated by seed command.",
                            )
                            created["communications"] += 1

                if rng.random() < 0.72:
                    Inquiry.objects.create(
                        owner=owner,
                        prospect=prospect,
                        inquiry_date=timezone.now() - timedelta(days=rng.randint(0, 120)),
                        channel=rng.choice(list(InquiryChannel.values)),
                        subject="TM course details",
                        message="Fictional prospect inquiry about schedules and fees.",
                        status=rng.choice(list(InquiryStatus.values)),
                        assigned_to=rng.choice(users) if users and rng.random() < 0.40 else None,
                    )
                    created["inquiries"] += 1

                if rng.random() < 0.60:
                    Communication.objects.create(
                        owner=owner,
                        recipient_type=RecipientType.PROSPECT,
                        prospect=prospect,
                        channel=rng.choice(list(CommunicationChannel.values)),
                        communication_type=rng.choice(list(CommunicationType.values)),
                        subject="Welcome to TMIS",
                        body="Fictional outreach generated for demo and QA use.",
                        sent_at=timezone.now() - timedelta(days=rng.randint(0, 120)),
                        delivery_status=rng.choice(list(DeliveryStatus.values)),
                        provider_status="simulated",
                        notes="Generated by seed command.",
                    )
                    created["communications"] += 1

            meditator_backfill = backfill_fictitious_meditator_transitions(target_ratio=0.30)
            created["meditators"] = meditator_backfill["already_eligible"] + meditator_backfill["promoted_for_target"]

        self.stdout.write(self.style.SUCCESS("Fictional CRM data seeded successfully."))
        for key, value in created.items():
            self.stdout.write(f"- {key}: {value}")

    def _ensure_teachers(self, rng: random.Random, run_tag: str) -> list[Teacher]:
        for specialization in TeacherSpecializationName.values:
            TeacherSpecialization.objects.get_or_create(name=specialization)

        specializations = list(TeacherSpecialization.objects.all())
        teachers: list[Teacher] = []
        for i in range(1, 13):
            first_name = rng.choice(self.FIRST_NAMES)
            last_name = rng.choice(self.LAST_NAMES)
            teacher = Teacher.objects.create(
                first_name=first_name,
                last_name=last_name,
                email=f"teacher.{run_tag}.{i}@example.com",
                phone=f"+23320{rng.randint(1000000, 9999999)}",
                qualification="Certified TM Instructor",
                availability="Weekdays and selected weekends",
                status=rng.choice(list(TeacherStatus.values)),
            )
            teacher.specializations.set(rng.sample(specializations, k=rng.randint(1, 2)))
            teachers.append(teacher)
        return teachers

    def _ensure_fictitious_users(self, owner_count: int):
        User = get_user_model()
        # Reuse existing fictitious product users first so accounts like
        # teacher_user_1 get data ownership in subsequent seed runs.
        existing_fictitious_users = list(
            User.objects.filter(
                is_superuser=False,
                is_staff=False,
                username__regex=r"^(teacher_user_|demo_owner_)",
            ).order_by("id")
        )
        owners = existing_fictitious_users[:]

        # Ensure we have at least owner_count users in the pool by creating
        # additional demo_owner_* accounts if needed.
        for i in range(1, owner_count + 1):
            if len(owners) >= owner_count:
                break
            user, _ = User.objects.get_or_create(
                username=f"demo_owner_{i}",
                defaults={
                    "email": f"demo.owner.{i}@example.com",
                    "is_staff": False,
                    "is_superuser": False,
                },
            )
            if not user.has_usable_password():
                user.set_password("demo-owner-123")
                user.save(update_fields=["password"])
            if user not in owners:
                owners.append(user)
        if not owners:
            fallback = (
                User.objects.filter(is_superuser=False)
                .order_by("id")
                .first()
            )
            if fallback:
                owners.append(fallback)
        return owners

    def _pick_owner(self, rng: random.Random, owners):
        # Weighted random gives realistic, non-uniform workload distribution.
        weights = [max(len(owners) - idx, 1) for idx in range(len(owners))]
        return rng.choices(owners, weights=weights, k=1)[0]

    def _ensure_locations(self) -> list[Location]:
        locations: list[Location] = []
        for name, city, province_state, country in self.LOCATIONS:
            location, _ = Location.objects.get_or_create(
                name=name,
                defaults={
                    "address_line1": f"{city} Main Road",
                    "city": city,
                    "province_state": province_state,
                    "country": country,
                    "is_active": True,
                    "notes": "Auto-seeded location.",
                },
            )
            locations.append(location)
        return locations

    def _ensure_courses(self, rng: random.Random) -> list[Course]:
        courses: list[Course] = []
        for idx, name in enumerate(self.COURSE_NAMES, start=1):
            course, _ = Course.objects.get_or_create(
                name=name,
                defaults={
                    "description": "Fictional course generated for demo data.",
                    "format": rng.choice(list(CourseFormat.values)),
                    "duration_weeks": rng.choice([1, 2, 4, 6, 8]),
                    "standard_fee": Decimal(str(rng.choice([450, 550, 650, 750, 900]))),
                    "status": CourseStatus.ACTIVE,
                },
            )
            if course.standard_fee <= Decimal("0.00"):
                course.standard_fee = Decimal(str(rng.choice([450, 550, 650, 750, 900])))
                course.save(update_fields=["standard_fee"])
            courses.append(course)
        return courses

    def _create_sessions(
        self,
        rng: random.Random,
        teachers: list[Teacher],
        locations: list[Location],
        courses: list[Course],
        run_tag: str,
        owner_users,
    ) -> list[CourseSession]:
        sessions: list[CourseSession] = []
        now = timezone.now()
        for i in range(1, 31):
            start_date = now - timedelta(days=rng.randint(0, 120)) + timedelta(hours=rng.randint(0, 23))
            end_date = start_date + timedelta(hours=rng.choice([2, 3, 4]))
            course = rng.choice(courses)
            session = CourseSession.objects.create(
                owner=self._pick_owner(rng, owner_users),
                course=course,
                teacher=rng.choice(teachers),
                session_name=f"{course.name} Session {run_tag}-{i}",
                start_date=start_date,
                end_date=end_date,
                location=rng.choice(locations),
                delivery_mode=course.format,
                capacity=rng.choice([15, 20, 25, 30]),
                status=rng.choice(list(SessionStatus.values)),
            )
            sessions.append(session)
        return sessions

    def _create_prospect(self, rng: random.Random, run_tag: str, i: int, owner) -> Prospect:
        first_name = rng.choice(self.FIRST_NAMES)
        last_name = rng.choice(self.LAST_NAMES)
        unique_phone = f"+23324{run_tag[-4:]}{i:04d}"
        contact, _ = Contact.get_or_create_from_identity(
            first_name=first_name,
            last_name=last_name,
            email=f"{first_name.lower()}.{last_name.lower()}.{run_tag}.{i}@example.com",
            phone_number=unique_phone,
        )
        return Prospect.objects.create(
            owner=owner,
            contact=contact,
            preferred_contact_method=rng.choice(list(ContactMethod.values)),
            source=rng.choice(["Website", "Referral", "Walk-in", "Instagram", "Phone"]),
            status=rng.choice(list(ProspectStatus.values)),
            interest_level=rng.choice(list(InterestLevel.values)),
            notes="Generated by seed_fake_crm.",
        )

    def _create_student_from_prospect(
        self, rng: random.Random, prospect: Prospect, owner
    ) -> Student:
        student, _ = prospect.convert_to_student()
        student.owner = owner
        _, city, province_state, country = rng.choice(self.LOCATIONS)
        student.date_of_birth = (
            timezone.now().date() - timedelta(days=rng.randint(19 * 365, 62 * 365))
        )
        student.address = f"{rng.randint(10, 900)} Example Street"
        student.city = city
        student.province_state = province_state
        student.country = country
        student.enrollment_status = rng.choice(list(EnrollmentStatus.values))
        student.notes = "Fictional student profile."
        student.save()
        return student

    def _create_enrollment(
        self,
        rng: random.Random,
        student: Student,
        session: CourseSession,
    ) -> Enrollment:
        fee = Decimal(str(rng.choice([450, 550, 650, 750, 900, 1100]))).quantize(
            Decimal("0.01")
        )
        max_discount = int(fee)
        discount = Decimal(str(rng.choice([0, 25, 50, 75, 100, 150, 200]))).quantize(
            Decimal("0.01")
        )
        if discount > fee:
            discount = Decimal(max_discount).quantize(Decimal("0.01"))

        enrollment, _ = Enrollment.objects.update_or_create(
            student=student,
            session=session,
            defaults={
                "enrollment_date": timezone.now() - timedelta(days=rng.randint(0, 120)),
                "status": rng.choice(list(EnrollmentStatus.values)),
                "fee_amount": fee,
                "discount_amount": discount,
                "notes": "Fictional enrollment.",
            },
        )
        return enrollment

    def _create_invoice(
        self,
        rng: random.Random,
        enrollment: Enrollment,
    ) -> Invoice:
        issue_date = (enrollment.enrollment_date - timedelta(days=rng.randint(0, 2))).date()
        due_date = issue_date + timedelta(days=rng.choice([7, 14, 21]))
        subtotal = (enrollment.fee_amount or Decimal("0.00")).quantize(Decimal("0.01"))
        discount = (enrollment.discount_amount or Decimal("0.00")).quantize(Decimal("0.01"))
        taxable_base = max(Decimal("0.00"), subtotal - discount)
        tax_amount = (taxable_base * Decimal("0.05")).quantize(Decimal("0.01"))
        total_amount = (taxable_base + tax_amount).quantize(Decimal("0.01"))
        year = issue_date.year if issue_date else timezone.localdate().year
        course_code = (enrollment.course.code or "GEN").upper()
        invoice_number = self._generate_course_invoice_number(course_code=course_code, year=year)

        invoice = Invoice.objects.create(
            owner=enrollment.student.owner,
            enrollment=enrollment,
            invoice_number=invoice_number,
            issue_date=issue_date,
            due_date=due_date,
            subtotal=subtotal,
            discount_amount=discount,
            tax_amount=tax_amount,
            total_amount=total_amount,
            status=rng.choice(
                [
                    InvoiceStatus.DRAFT,
                    InvoiceStatus.SENT,
                    InvoiceStatus.PARTIAL,
                    InvoiceStatus.PAID,
                    InvoiceStatus.OVERDUE,
                ]
            ),
            notes="Fictional invoice.",
        )
        return invoice

    def _generate_course_invoice_number(self, *, course_code: str, year: int) -> str:
        prefix = f"{course_code}-{year}-"
        latest = (
            Invoice.objects.filter(invoice_number__startswith=prefix)
            .order_by("-invoice_number")
            .values_list("invoice_number", flat=True)
            .first()
        )
        sequence = 0
        if latest:
            try:
                sequence = int(str(latest).rsplit("-", 1)[-1])
            except (ValueError, TypeError):
                sequence = 0
        for _ in range(200):
            sequence += 1
            candidate = f"{prefix}{sequence:04d}"
            if not Invoice.objects.filter(invoice_number=candidate).exists():
                return candidate
        fallback = timezone.now().strftime("%H%M%S")
        return f"{course_code}-{year}-{fallback}"

    def _create_payment(
        self,
        rng: random.Random,
        invoice: Invoice,
        paid_total: Decimal,
        payment_index: int,
    ) -> Payment | None:
        remaining = (invoice.total_amount - paid_total).quantize(Decimal("0.01"))
        if remaining <= Decimal("0.00"):
            return None

        if payment_index == 0:
            amount = min(
                remaining,
                Decimal(str(rng.choice([100, 150, 200, 250, 300, 400, 500]))),
            )
        else:
            amount = remaining

        payment = Payment.objects.create(
            owner=invoice.owner,
            invoice=invoice,
            payment_date=timezone.now() - timedelta(days=rng.randint(0, 90)),
            amount_paid=amount.quantize(Decimal("0.01")),
            payment_method=rng.choice(list(PaymentMethod.values)),
            reference_number=f"PAY-{invoice.invoice_number}-{payment_index + 1}",
            confirmation_status=rng.choice(list(PaymentConfirmationStatus.values)),
            notes="Fictional payment transaction.",
        )
        return payment

    def _update_invoice_status(self, invoice: Invoice, paid_total: Decimal) -> None:
        total = (invoice.total_amount or Decimal("0.00")).quantize(Decimal("0.01"))
        paid_total = paid_total.quantize(Decimal("0.01"))

        if paid_total <= Decimal("0.00"):
            status = InvoiceStatus.SENT
        elif paid_total < total:
            status = InvoiceStatus.PARTIAL
        else:
            status = InvoiceStatus.PAID

        if status != InvoiceStatus.PAID and invoice.due_date and invoice.due_date < timezone.now().date():
            status = InvoiceStatus.OVERDUE

        invoice.status = status
        invoice.save(update_fields=["status", "updated_at"])
