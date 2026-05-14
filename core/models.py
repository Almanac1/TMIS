import re
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import IntegrityError, models, transaction
from django.db.models import Q
from django.utils.text import slugify
from django.utils import timezone


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ProspectStatus(models.TextChoices):
    NEW = "new", "New"
    CONTACTED = "contacted", "Contacted"
    QUALIFIED = "qualified", "Qualified"
    CONVERTED = "converted", "Converted"
    BAD_LEAD = "bad_lead", "Bad Lead"
    INACTIVE = "inactive", "Inactive"


class ContactMethod(models.TextChoices):
    EMAIL = "email", "Email"
    PHONE = "phone", "Phone"
    WHATSAPP = "whatsapp", "WhatsApp"
    SMS = "sms", "SMS"


class InterestLevel(models.TextChoices):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"


class InquiryChannel(models.TextChoices):
    WEBSITE = "website", "Website"
    PHONE = "phone", "Phone"
    EMAIL = "email", "Email"
    REFERRAL = "referral", "Referral"
    WALK_IN = "walk_in", "Walk-in"


class InquiryStatus(models.TextChoices):
    OPEN = "open", "Open"
    IN_PROGRESS = "in_progress", "In Progress"
    CLOSED = "closed", "Closed"


class EnrollmentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    ENROLLED = "enrolled", "Enrolled"
    ACTIVE = "active", "Active"
    COMPLETED = "completed", "Completed"
    WITHDRAWN = "withdrawn", "Withdrawn"
    CANCELLED = "cancelled", "Cancelled"
    INACTIVE = "inactive", "Inactive"


class TeacherStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    INACTIVE = "inactive", "Inactive"
    ON_LEAVE = "on_leave", "On Leave"


class TeacherSpecializationName(models.TextChoices):
    TM_TEACHER = "TM Teacher", "TM Teacher"
    ADVANCE_TECHNIQUE_1 = "Advance Technique 1", "Advance Technique 1"
    ADVANCE_TECHNIQUE_2 = "Advance Technique 2", "Advance Technique 2"
    ADVANCE_TECHNIQUE_3 = "Advance Technique 3", "Advance Technique 3"
    ADVANCE_TECHNIQUE_4 = "Advance Technique 4", "Advance Technique 4"
    SIDHI_ADMINISTRATOR = "Sidhi Administrator", "Sidhi Administrator"


class CourseFormat(models.TextChoices):
    ONLINE = "online", "Online"
    IN_PERSON = "in_person", "In Person"
    HYBRID = "hybrid", "Hybrid"


class CourseStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    INACTIVE = "inactive", "Inactive"
    ARCHIVED = "archived", "Archived"


class SessionStatus(models.TextChoices):
    SCHEDULED = "scheduled", "Scheduled"
    OPEN = "open", "Open"
    FULL = "full", "Full"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class InterviewStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    REVIEWED = "reviewed", "Reviewed"


class InvoiceStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SENT = "sent", "Sent"
    PARTIAL = "partial", "Partial"
    PAID = "paid", "Paid"
    OVERDUE = "overdue", "Overdue"
    CANCELLED = "cancelled", "Cancelled"


class PaymentMethod(models.TextChoices):
    CASH = "cash", "Cash"
    TRANSFER = "transfer", "Bank Transfer"
    CARD = "card", "Card"
    CHEQUE = "cheque", "Cheque"
    ONLINE = "online", "Online"


class PaymentConfirmationStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    CONFIRMED = "confirmed", "Confirmed"
    FAILED = "failed", "Failed"
    REVERSED = "reversed", "Reversed"


class RecipientType(models.TextChoices):
    PROSPECT = "prospect", "Prospect"
    STUDENT = "student", "Student"


class CommunicationChannel(models.TextChoices):
    EMAIL = "email", "Email"
    SMS = "sms", "SMS"


class CommunicationType(models.TextChoices):
    INTRO_INVITATION = "intro_invitation", "Introductory Session Invitation"
    FOLLOW_UP = "follow_up", "Follow-up"
    REMINDER = "reminder", "Reminder"
    PAYMENT_REQUEST = "payment_request", "Payment Request"
    GENERAL = "general", "General"


class DeliveryStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    SENT = "sent", "Sent"
    DELIVERED = "delivered", "Delivered"
    FAILED = "failed", "Failed"
    BOUNCED = "bounced", "Bounced"


class DisbursementStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    PAID = "paid", "Paid"
    CANCELLED = "cancelled", "Cancelled"


class MeditatorTransitionTrigger(models.TextChoices):
    INTRO_AND_DAY20_COMPLETED = (
        "intro_and_day20_completed",
        "Intro training completed and Day 20 check-in completed",
    )


class MeditatorTransitionEventType(models.TextChoices):
    TRANSITIONED = "transitioned", "Transitioned to Meditator"


class Prospect(TimeStampedModel):
    BAD_LEAD_ATTEMPT_THRESHOLD = 4
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_prospects",
        null=True,
        blank=True,
    )
    contact = models.OneToOneField(
        "Contact",
        on_delete=models.PROTECT,
        related_name="prospect",
        null=True,
        blank=True,
    )
    preferred_contact_method = models.CharField(
        max_length=30,
        choices=ContactMethod.choices,
        blank=True,
    )
    source = models.CharField(max_length=100, blank=True)
    status = models.CharField(
        max_length=20,
        choices=ProspectStatus.choices,
        default=ProspectStatus.NEW,
    )
    teacher = models.ForeignKey(
        "Teacher",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="prospects",
    )
    interest_level = models.CharField(
        max_length=10,
        choices=InterestLevel.choices,
        blank=True,
    )
    notes = models.TextField(blank=True)
    is_archived = models.BooleanField(default=False)
    converted_to_student = models.BooleanField(default=False)
    converted_student = models.ForeignKey(
        "Student",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="source_prospects",
    )
    converted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["is_archived"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.first_name} {self.last_name}".strip() or f"Prospect #{self.pk}"

    @property
    def first_name(self) -> str:
        return self.contact.first_name if self.contact_id else ""

    @property
    def last_name(self) -> str:
        return self.contact.last_name if self.contact_id else ""

    @property
    def email(self) -> str:
        return self.contact.email if self.contact_id else ""

    @property
    def phone(self) -> str:
        return self.contact.phone_number if self.contact_id else ""

    @property
    def contact_attempt_count(self) -> int:
        """
        Count communication attempts linked to this prospect.

        A record counts as an attempt when it was marked sent (has sent_at) or
        moved out of the queued state.
        """
        return self.communications.filter(
            Q(sent_at__isnull=False) | ~Q(delivery_status=DeliveryStatus.QUEUED)
        ).count()

    def apply_bad_lead_rule(self, *, save: bool = True) -> bool:
        """
        Archive prospect as Bad Lead when contact attempts reach threshold.

        Returns True when a state change occurred.
        """
        if self.status == ProspectStatus.CONVERTED:
            return False
        if self.contact_attempt_count < self.BAD_LEAD_ATTEMPT_THRESHOLD:
            return False
        changed = False
        if self.status != ProspectStatus.BAD_LEAD:
            self.status = ProspectStatus.BAD_LEAD
            changed = True
        if not self.is_archived:
            self.is_archived = True
            changed = True
        if changed and save:
            self.save(update_fields=["status", "is_archived", "updated_at"])
        return changed

    @staticmethod
    def _normalize_phone(phone: str) -> str:
        return re.sub(r"\D+", "", phone or "")

    @classmethod
    def _phone_identity_variants(cls, phone: str) -> set[str]:
        normalized = cls._normalize_phone(phone)
        variants = {normalized} if normalized else set()
        # Handle common North America formatting where +1 may be present or omitted.
        if len(normalized) == 11 and normalized.startswith("1"):
            variants.add(normalized[1:])
        return variants

    def find_potential_duplicate_student(self):
        """
        Return an existing Student that appears to represent the same person.

        Matching names alone are not enough because name collisions are valid.
        We require an additional identity signal (email or normalized phone).
        """
        first = (self.first_name or "").strip()
        last = (self.last_name or "").strip()
        if not first or not last:
            return None

        candidates = Student.objects.select_related("prospect").exclude(
            prospect=self
        ).filter(
            prospect__contact__first_name__iexact=first,
            prospect__contact__last_name__iexact=last,
        )

        email = (self.email or "").strip()
        if email:
            match_by_email = candidates.filter(prospect__contact__email__iexact=email).first()
            if match_by_email:
                return match_by_email

        phone_variants = self._phone_identity_variants(self.phone)
        if phone_variants:
            for candidate in candidates.exclude(prospect__contact__phone_number__isnull=True):
                candidate_phone_variants = self._phone_identity_variants(
                    candidate.prospect.contact.phone_number
                )
                if phone_variants.intersection(candidate_phone_variants):
                    return candidate
        return None

    def convert_to_student(self):
        """
        Idempotently convert this prospect into a student record.

        Returns:
            tuple[Student, bool]: (student_instance, created_flag)
        """
        duplicate_student = self.find_potential_duplicate_student()
        if duplicate_student:
            raise ValidationError(
                (
                    "Potential duplicate student detected "
                    f"(existing Student #{duplicate_student.pk}). "
                    "Please review records before converting."
                )
            )
        try:
            with transaction.atomic():
                student, created = Student.objects.get_or_create(
                    prospect=self,
                    defaults={
                        "owner": self.owner,
                        "teacher": self.teacher,
                        "notes": self.notes,
                    },
                )
                if (
                    self.status != ProspectStatus.CONVERTED
                    or not self.converted_to_student
                    or self.converted_student_id != student.pk
                    or self.converted_at is None
                ):
                    self.status = ProspectStatus.CONVERTED
                    self.converted_to_student = True
                    self.converted_student = student
                    self.converted_at = timezone.now()
                    self.save(
                        update_fields=[
                            "status",
                            "converted_to_student",
                            "converted_student",
                            "converted_at",
                            "updated_at",
                        ]
                    )
                return student, created
        except IntegrityError:
            # Handles rare race conditions where another process creates the row
            # concurrently between get and create.
            student = Student.objects.get(prospect=self)
            if (
                self.status != ProspectStatus.CONVERTED
                or not self.converted_to_student
                or self.converted_student_id != student.pk
                or self.converted_at is None
            ):
                self.status = ProspectStatus.CONVERTED
                self.converted_to_student = True
                self.converted_student = student
                self.converted_at = timezone.now()
                self.save(
                    update_fields=[
                        "status",
                        "converted_to_student",
                        "converted_student",
                        "converted_at",
                        "updated_at",
                    ]
                )
            return student, False

    def clean(self) -> None:
        if not self.contact_id:
            raise ValidationError("A prospect must be linked to a contact.")

    def save(self, *args, **kwargs):
        if not self.contact_id:
            raise ValidationError("A prospect must be linked to a contact.")
        super().save(*args, **kwargs)


class Contact(TimeStampedModel):
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(blank=True, null=True)
    phone_number = models.CharField(max_length=30, blank=True, null=True)
    converted_to_prospect = models.BooleanField(default=False)
    converted_prospect = models.ForeignKey(
        "Prospect",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="source_contacts",
    )
    converted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["first_name", "last_name"]
        indexes = [
            models.Index(fields=["email"]),
            models.Index(fields=["phone_number"]),
        ]

    def __str__(self) -> str:
        return f"{self.first_name} {self.last_name}".strip() or f"Contact #{self.pk}"

    @property
    def has_converted_prospect(self) -> bool:
        return bool(self.converted_to_prospect or self.converted_prospect_id or hasattr(self, "prospect"))

    def convert_to_prospect(self, *, owner=None, source: str = "", notes: str = ""):
        """
        Idempotently convert this contact into a prospect record.

        Returns:
            tuple[Prospect, bool]: (prospect_instance, created_flag)
        """
        defaults = {
            "owner": owner,
            "source": (source or "").strip(),
            "notes": (notes or "").strip(),
        }
        try:
            with transaction.atomic():
                return Prospect.objects.get_or_create(
                    contact=self,
                    defaults=defaults,
                )
        except IntegrityError:
            return Prospect.objects.get(contact=self), False

    @staticmethod
    def _normalize_phone(phone: str) -> str:
        return re.sub(r"\D+", "", phone or "")

    @classmethod
    def find_matching_contact(cls, *, email: str = "", phone_number: str = ""):
        email_value = (email or "").strip()
        phone_digits = cls._normalize_phone(phone_number)
        if email_value:
            match = cls.objects.filter(email__iexact=email_value).first()
            if match:
                return match
        if phone_digits:
            for contact in cls.objects.exclude(phone_number__isnull=True).exclude(phone_number=""):
                if cls._normalize_phone(contact.phone_number) == phone_digits:
                    return contact
        return None

    @classmethod
    def get_or_create_from_identity(
        cls,
        *,
        first_name: str,
        last_name: str,
        email: str = "",
        phone_number: str = "",
    ):
        match = cls.find_matching_contact(email=email, phone_number=phone_number)
        if match:
            updated_fields = []
            if first_name and not match.first_name:
                match.first_name = first_name
                updated_fields.append("first_name")
            if last_name and not match.last_name:
                match.last_name = last_name
                updated_fields.append("last_name")
            if email and not match.email:
                match.email = email
                updated_fields.append("email")
            if phone_number and not match.phone_number:
                match.phone_number = phone_number
                updated_fields.append("phone_number")
            if updated_fields:
                match.save(update_fields=updated_fields + ["updated_at"])
            return match, False
        return cls.objects.create(
            first_name=first_name,
            last_name=last_name,
            email=email or None,
            phone_number=phone_number or None,
        ), True

    def clean(self) -> None:
        if not self.first_name:
            raise ValidationError("Contact first_name is required.")
        if not self.last_name:
            raise ValidationError("Contact last_name is required.")
        if self.email == "":
            self.email = None
        if self.phone_number == "":
            self.phone_number = None

        if self.email:
            duplicate_email = Contact.objects.filter(email__iexact=self.email).exclude(
                pk=self.pk
            )
            if duplicate_email.exists():
                raise ValidationError({"email": "A contact with this email already exists."})

        if self.phone_number:
            phone_digits = self._normalize_phone(self.phone_number)
            for candidate in Contact.objects.exclude(pk=self.pk).exclude(
                phone_number__isnull=True
            ).exclude(phone_number=""):
                if self._normalize_phone(candidate.phone_number) == phone_digits:
                    raise ValidationError(
                        {"phone_number": "A contact with this phone number already exists."}
                    )


class Teacher(TimeStampedModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="teacher_profile",
    )
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=30, blank=True)
    qualification = models.CharField(max_length=150, blank=True)
    specializations = models.ManyToManyField(
        "TeacherSpecialization",
        blank=True,
        related_name="teachers",
    )
    availability = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=TeacherStatus.choices,
        default=TeacherStatus.ACTIVE,
    )

    class Meta:
        ordering = ["first_name", "last_name"]
        indexes = [
            models.Index(fields=["email"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def specializations_display(self) -> str:
        return ", ".join(
            self.specializations.values_list("name", flat=True).order_by("name")
        )


class TeacherSpecialization(TimeStampedModel):
    name = models.CharField(
        max_length=50,
        choices=TeacherSpecializationName.choices,
        unique=True,
    )

    class Meta:
        ordering = ["name"]
        verbose_name = "Teacher Specialization"
        verbose_name_plural = "Teacher Specializations"

    def __str__(self) -> str:
        return self.name


class Location(TimeStampedModel):
    name = models.CharField(max_length=150, unique=True)
    code = models.SlugField(max_length=50, unique=True, blank=True)
    address_line1 = models.CharField(max_length=255, blank=True)
    address_line2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    province_state = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, default="Ghana")
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["is_active"]),
            models.Index(fields=["city"]),
        ]

    def save(self, *args, **kwargs):
        if not self.code:
            base = slugify(self.name)[:40] or "location"
            code = base
            i = 1
            while Location.objects.filter(code=code).exclude(pk=self.pk).exists():
                i += 1
                code = f"{base[:38]}-{i}"
            self.code = code
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class Student(TimeStampedModel):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_students",
        null=True,
        blank=True,
    )
    prospect = models.OneToOneField(
        Prospect,
        on_delete=models.PROTECT,
        related_name="student_record",
    )
    teacher = models.ForeignKey(
        "Teacher",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="students",
    )
    date_of_birth = models.DateField(null=True, blank=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    province_state = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True)
    enrollment_status = models.CharField(
        max_length=20,
        choices=EnrollmentStatus.choices,
        default=EnrollmentStatus.PENDING,
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["prospect__contact__first_name", "prospect__contact__last_name"]
        indexes = [
            models.Index(fields=["enrollment_status"]),
            models.Index(fields=["created_at"]),
        ]

    @property
    def first_name(self) -> str:
        return self.prospect.contact.first_name if self.prospect_id and self.prospect.contact_id else ""

    @property
    def last_name(self) -> str:
        return self.prospect.contact.last_name if self.prospect_id and self.prospect.contact_id else ""

    @property
    def email(self) -> str:
        return self.prospect.contact.email if self.prospect_id and self.prospect.contact_id else ""

    @property
    def phone(self) -> str:
        return (
            self.prospect.contact.phone_number
            if self.prospect_id and self.prospect.contact_id
            else ""
        )

    def __str__(self) -> str:
        teacher_name = self.teacher if self.teacher_id else "Unassigned"
        return f"{self.prospect} ({teacher_name})"

    def has_intro_enrollment(self) -> bool:
        if not self.pk:
            return False
        return self.enrollments.filter(
            session__course__name="TM Introductory Program"
        ).exists()

    def clean(self) -> None:
        if self.prospect_id and not self.owner_id:
            self.owner = self.prospect.owner
        if (
            self.enrollment_status == EnrollmentStatus.COMPLETED
            and self.pk
            and not self.has_intro_enrollment()
        ):
            raise ValidationError(
                "Student cannot be marked completed without TM Introductory Program enrollment."
            )

    def save(self, *args, **kwargs):
        if self.prospect_id and not self.owner_id:
            self.owner = self.prospect.owner
        if (
            self.enrollment_status == EnrollmentStatus.COMPLETED
            and self.pk
            and not self.has_intro_enrollment()
        ):
            raise ValidationError(
                "Student cannot be marked completed without TM Introductory Program enrollment."
            )
        super().save(*args, **kwargs)
        if self.pk:
            # System-managed transition check; users do not manually set meditator state.
            from core.services.meditator_transitions import (
                ensure_meditator_transition_for_student,
            )

            ensure_meditator_transition_for_student(self)


class Course(TimeStampedModel):
    name = models.CharField(max_length=150, unique=True)
    description = models.TextField(blank=True)
    format = models.CharField(
        max_length=20,
        choices=CourseFormat.choices,
        default=CourseFormat.IN_PERSON,
    )
    duration_weeks = models.PositiveIntegerField(null=True, blank=True)
    standard_fee = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
        default=Decimal("0.00"),
    )
    status = models.CharField(
        max_length=20,
        choices=CourseStatus.choices,
        default=CourseStatus.ACTIVE,
    )

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return self.name


class CourseSession(TimeStampedModel):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_course_sessions",
        null=True,
        blank=True,
    )
    course = models.ForeignKey(
        Course,
        on_delete=models.PROTECT,
        related_name="sessions",
    )
    teacher = models.ForeignKey(
        Teacher,
        on_delete=models.PROTECT,
        related_name="sessions",
    )
    session_name = models.CharField(max_length=150, blank=True)
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="course_sessions",
    )
    delivery_mode = models.CharField(
        max_length=20,
        choices=CourseFormat.choices,
        default=CourseFormat.IN_PERSON,
    )
    capacity = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=SessionStatus.choices,
        default=SessionStatus.SCHEDULED,
    )

    class Meta:
        ordering = ["-start_date"]
        indexes = [
            models.Index(fields=["course", "start_date"]),
            models.Index(fields=["teacher", "start_date"]),
            models.Index(fields=["location", "start_date"]),
            models.Index(fields=["status"]),
        ]

    def clean(self) -> None:
        if self.end_date and self.start_date and self.end_date <= self.start_date:
            raise ValidationError("end_date must be later than start_date.")

    def __str__(self) -> str:
        return self.session_name or f"{self.course.name} ({self.start_date:%Y-%m-%d})"


class Inquiry(TimeStampedModel):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_inquiries",
        null=True,
        blank=True,
    )
    prospect = models.ForeignKey(
        Prospect,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="inquiries",
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="inquiries",
    )
    inquiry_date = models.DateTimeField()
    channel = models.CharField(
        max_length=20,
        choices=InquiryChannel.choices,
        default=InquiryChannel.WEBSITE,
    )
    subject = models.CharField(max_length=150, blank=True)
    message = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=InquiryStatus.choices,
        default=InquiryStatus.OPEN,
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_inquiries",
    )

    class Meta:
        ordering = ["-inquiry_date"]
        indexes = [
            models.Index(fields=["prospect", "inquiry_date"]),
            models.Index(fields=["student", "inquiry_date"]),
            models.Index(fields=["status"]),
            models.Index(fields=["channel"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=Q(prospect__isnull=False) | Q(student__isnull=False),
                name="inquiry_requires_prospect_or_student",
            )
        ]

    def __str__(self) -> str:
        if self.student_id:
            owner = f"Student: {self.student}"
        elif self.prospect_id:
            owner = f"Prospect: {self.prospect}"
        else:
            owner = "Unassigned"
        return self.subject or f"Inquiry #{self.pk} ({owner})"

    def clean(self) -> None:
        if self.student_id and not self.owner_id:
            self.owner = self.student.owner
        elif self.prospect_id and not self.owner_id:
            self.owner = self.prospect.owner
        if not self.prospect_id and not self.student_id:
            raise ValidationError("Set at least one of prospect or student for an inquiry.")
        if self.student_id and self.prospect_id:
            if self.student.prospect_id != self.prospect_id:
                raise ValidationError(
                    "When both student and prospect are set, they must refer to the same person."
                )


class Enrollment(TimeStampedModel):
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="enrollments",
    )
    session = models.ForeignKey(
        CourseSession,
        on_delete=models.PROTECT,
        related_name="enrollments",
    )
    enrollment_date = models.DateTimeField()
    status = models.CharField(
        max_length=20,
        choices=EnrollmentStatus.choices,
        default=EnrollmentStatus.ENROLLED,
    )
    fee_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
        default=Decimal("0.00"),
    )
    discount_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
        default=Decimal("0.00"),
    )
    balance_due = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
        default=Decimal("0.00"),
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-enrollment_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "session"],
                name="unique_student_session_enrollment",
            ),
            models.CheckConstraint(
                check=Q(discount_amount__lte=models.F("fee_amount")),
                name="enrollment_discount_lte_fee",
            ),
        ]
        indexes = [
            models.Index(fields=["student", "status"]),
            models.Index(fields=["session", "status"]),
        ]

    def clean(self) -> None:
        if self.discount_amount and self.fee_amount and self.discount_amount > self.fee_amount:
            raise ValidationError("discount_amount cannot exceed fee_amount.")

    def save(self, *args, **kwargs):
        if self.discount_amount and self.fee_amount and self.discount_amount > self.fee_amount:
            raise ValidationError("discount_amount cannot exceed fee_amount.")
        self.balance_due = (self.fee_amount or Decimal("0.00")) - (
            self.discount_amount or Decimal("0.00")
        )
        super().save(*args, **kwargs)
        if self.student_id:
            from core.services.meditator_transitions import (
                ensure_meditator_transition_for_student,
            )

            ensure_meditator_transition_for_student(self.student)

    def __str__(self) -> str:
        return f"{self.student} - {self.session}"


class Disbursement(TimeStampedModel):
    """
    A single payout allocation snapshot per enrollment.

    We keep one record per enrollment (OneToOne) for simplicity and auditability.
    If future requirements need installment payouts, this can evolve into a header/detail
    model without breaking enrollment-level referential integrity.
    """

    enrollment = models.OneToOneField(
        Enrollment,
        on_delete=models.PROTECT,
        related_name="disbursement",
    )
    teacher = models.ForeignKey(
        Teacher,
        on_delete=models.PROTECT,
        related_name="disbursements",
    )
    location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="disbursements",
    )
    balance_due_snapshot = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    teacher_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    location_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    ico_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    disbursement_date = models.DateField()
    status = models.CharField(
        max_length=20,
        choices=DisbursementStatus.choices,
        default=DisbursementStatus.PENDING,
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-disbursement_date", "-id"]
        indexes = [
            models.Index(fields=["teacher", "disbursement_date"]),
            models.Index(fields=["location", "disbursement_date"]),
            models.Index(fields=["status"]),
            models.Index(fields=["disbursement_date"]),
        ]

    def clean(self) -> None:
        if self.enrollment_id:
            expected_teacher = self.enrollment.session.teacher
            expected_location = self.enrollment.session.location
            if self.teacher_id and self.teacher_id != expected_teacher.id:
                raise ValidationError("teacher must match enrollment.session.teacher.")
            if self.location_id and self.location_id != expected_location.id:
                raise ValidationError("location must match enrollment.session.location.")
        total = (self.teacher_amount or Decimal("0.00")) + (
            self.location_amount or Decimal("0.00")
        ) + (self.ico_amount or Decimal("0.00"))
        if self.balance_due_snapshot is not None and total != self.balance_due_snapshot:
            raise ValidationError(
                "teacher_amount + location_amount + ico_amount must equal balance_due_snapshot."
            )

    def save(self, *args, **kwargs):
        # Always derive allocation from enrollment.balance_due.
        if self.enrollment_id:
            self.teacher = self.enrollment.session.teacher
            self.location = self.enrollment.session.location
            self.balance_due_snapshot = self.enrollment.balance_due or Decimal("0.00")
        base = self.balance_due_snapshot or Decimal("0.00")
        teacher_amount = (base * Decimal("0.50")).quantize(Decimal("0.01"))
        location_amount = (base * Decimal("0.20")).quantize(Decimal("0.01"))
        ico_amount = (base - teacher_amount - location_amount).quantize(Decimal("0.01"))
        self.teacher_amount = teacher_amount
        self.location_amount = location_amount
        self.ico_amount = ico_amount
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"Disbursement #{self.pk} - {self.enrollment}"


class InterviewForm(TimeStampedModel):
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="interview_forms",
    )
    teacher = models.ForeignKey(
        Teacher,
        on_delete=models.PROTECT,
        related_name="interview_forms",
    )
    session = models.ForeignKey(
        CourseSession,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="interview_forms",
    )
    submitted_at = models.DateTimeField()
    summary = models.TextField(blank=True)
    recommendation = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=InterviewStatus.choices,
        default=InterviewStatus.DRAFT,
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-submitted_at"]
        indexes = [
            models.Index(fields=["student", "submitted_at"]),
            models.Index(fields=["teacher", "submitted_at"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return f"InterviewForm #{self.pk} - {self.student}"


class Invoice(TimeStampedModel):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_invoices",
        null=True,
        blank=True,
    )
    enrollment = models.OneToOneField(
        Enrollment,
        on_delete=models.PROTECT,
        related_name="invoice",
    )
    invoice_number = models.CharField(max_length=50, unique=True)
    issue_date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    subtotal = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    discount_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
        default=Decimal("0.00"),
    )
    tax_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
        default=Decimal("0.00"),
    )
    total_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    status = models.CharField(
        max_length=20,
        choices=InvoiceStatus.choices,
        default=InvoiceStatus.DRAFT,
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-issue_date", "-id"]
        indexes = [
            models.Index(fields=["invoice_number"]),
            models.Index(fields=["status"]),
            models.Index(fields=["issue_date"]),
        ]

    def clean(self) -> None:
        if self.due_date and self.issue_date and self.due_date < self.issue_date:
            raise ValidationError("due_date cannot be earlier than issue_date.")
        if self.enrollment_id and not self.owner_id:
            # Invoice ownership is inherited from the owning student.
            self.owner = self.enrollment.student.owner

    def save(self, *args, **kwargs):
        if self.enrollment_id:
            self.owner = self.enrollment.student.owner
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.invoice_number

    @property
    def student(self):
        """Convenience accessor for invoice ownership through enrollment."""
        return self.enrollment.student


class Payment(TimeStampedModel):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_payments",
        null=True,
        blank=True,
    )
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.PROTECT,
        related_name="payments",
    )
    payment_date = models.DateTimeField()
    amount_paid = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    payment_method = models.CharField(
        max_length=20,
        choices=PaymentMethod.choices,
    )
    reference_number = models.CharField(max_length=100, blank=True)
    confirmation_status = models.CharField(
        max_length=20,
        choices=PaymentConfirmationStatus.choices,
        default=PaymentConfirmationStatus.PENDING,
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-payment_date"]
        indexes = [
            models.Index(fields=["invoice", "payment_date"]),
            models.Index(fields=["confirmation_status"]),
        ]

    def clean(self) -> None:
        if self.invoice_id and not self.owner_id:
            # Payment ownership follows its parent invoice.
            self.owner = self.invoice.owner

    def save(self, *args, **kwargs):
        if self.invoice_id:
            self.owner = self.invoice.owner
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"Payment #{self.pk} - {self.invoice.invoice_number}"


class Meditator(TimeStampedModel):
    student = models.OneToOneField(
        Student,
        on_delete=models.CASCADE,
        related_name="meditator_profile",
    )
    transitioned_at = models.DateTimeField(default=timezone.now, editable=False)
    transition_trigger = models.CharField(
        max_length=50,
        choices=MeditatorTransitionTrigger.choices,
        default=MeditatorTransitionTrigger.INTRO_AND_DAY20_COMPLETED,
        editable=False,
    )
    intro_completed_on = models.DateField(null=True, blank=True, editable=False)
    day20_completed_on = models.DateField(null=True, blank=True, editable=False)
    metadata = models.JSONField(default=dict, blank=True, editable=False)

    class Meta:
        ordering = ["-transitioned_at"]
        indexes = [
            models.Index(fields=["transitioned_at"]),
            models.Index(fields=["transition_trigger"]),
        ]

    def __str__(self) -> str:
        return f"Meditator: {self.student.prospect}"


class MeditatorTransitionEvent(TimeStampedModel):
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="meditator_transition_events",
    )
    meditator = models.ForeignKey(
        Meditator,
        on_delete=models.CASCADE,
        related_name="events",
    )
    event_type = models.CharField(
        max_length=30,
        choices=MeditatorTransitionEventType.choices,
        default=MeditatorTransitionEventType.TRANSITIONED,
        editable=False,
    )
    triggered_at = models.DateTimeField(default=timezone.now, editable=False)
    transition_trigger = models.CharField(
        max_length=50,
        choices=MeditatorTransitionTrigger.choices,
        default=MeditatorTransitionTrigger.INTRO_AND_DAY20_COMPLETED,
        editable=False,
    )
    intro_completed_on = models.DateField(null=True, blank=True, editable=False)
    day20_completed_on = models.DateField(null=True, blank=True, editable=False)
    metadata = models.JSONField(default=dict, blank=True, editable=False)

    class Meta:
        ordering = ["-triggered_at", "-id"]
        indexes = [
            models.Index(fields=["student", "triggered_at"]),
            models.Index(fields=["transition_trigger"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "event_type", "transition_trigger"],
                name="unique_student_meditator_transition_event",
            )
        ]

    def __str__(self) -> str:
        return f"Meditator transition event for {self.student.prospect}"


class Communication(TimeStampedModel):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_communications",
        null=True,
        blank=True,
    )
    recipient_type = models.CharField(
        max_length=20,
        choices=RecipientType.choices,
    )
    prospect = models.ForeignKey(
        Prospect,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="communications",
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="communications",
    )
    enrollment = models.ForeignKey(
        Enrollment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="communications",
    )
    channel = models.CharField(
        max_length=10,
        choices=CommunicationChannel.choices,
    )
    communication_type = models.CharField(
        max_length=30,
        choices=CommunicationType.choices,
        default=CommunicationType.GENERAL,
    )
    subject = models.CharField(max_length=255, blank=True)
    body = models.TextField()
    sent_at = models.DateTimeField(null=True, blank=True)
    delivery_status = models.CharField(
        max_length=20,
        choices=DeliveryStatus.choices,
        default=DeliveryStatus.QUEUED,
    )
    provider_status = models.CharField(max_length=100, blank=True)
    related_entity_type = models.CharField(max_length=50, blank=True)
    related_entity_id = models.PositiveBigIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient_type"]),
            models.Index(fields=["channel"]),
            models.Index(fields=["communication_type"]),
            models.Index(fields=["delivery_status"]),
            models.Index(fields=["prospect"]),
            models.Index(fields=["student"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=(
                    (
                        Q(recipient_type=RecipientType.PROSPECT)
                        & Q(prospect__isnull=False)
                        & Q(student__isnull=True)
                    )
                    | (
                        Q(recipient_type=RecipientType.STUDENT)
                        & Q(student__isnull=False)
                        & Q(prospect__isnull=True)
                    )
                ),
                name="communication_recipient_matches_type",
            )
        ]

    def clean(self) -> None:
        if self.student_id and not self.owner_id:
            self.owner = self.student.owner
        elif self.prospect_id and not self.owner_id:
            self.owner = self.prospect.owner
        if self.recipient_type == RecipientType.PROSPECT:
            if not self.prospect or self.student:
                raise ValidationError(
                    "For recipient_type='prospect', set prospect only."
                )
        elif self.recipient_type == RecipientType.STUDENT:
            if not self.student or self.prospect:
                raise ValidationError(
                    "For recipient_type='student', set student only."
                )

    def __str__(self) -> str:
        return f"{self.get_communication_type_display()} via {self.get_channel_display()}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.recipient_type == RecipientType.PROSPECT and self.prospect_id:
            self.prospect.apply_bad_lead_rule()
        should_check_transition = (
            self.student_id
            and self.communication_type == CommunicationType.FOLLOW_UP
            and self.sent_at is not None
        )
        if should_check_transition:
            from core.services.meditator_transitions import (
                ensure_meditator_transition_for_student,
            )

            ensure_meditator_transition_for_student(self.student)
