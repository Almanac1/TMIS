from datetime import datetime, time
from decimal import Decimal

from django import forms
from django.db.models import DecimalField, ExpressionWrapper, F, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import (
    CommunicationChannel,
    Communication,
    Contact,
    ContactMethod,
    InterestLevel,
    Location,
    Enrollment,
    Invoice,
    Payment,
    Prospect,
    ProspectStatus,
    RecipientType,
    Student,
    Teacher,
)


class DisbursementDateRangeReportForm(forms.Form):
    report_by = forms.ChoiceField(
        required=True,
        initial="teacher",
        choices=(
            ("teacher", "Governor"),
            ("location", "Location"),
        ),
    )
    start_date = forms.DateField(
        required=True,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    end_date = forms.DateField(
        required=True,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    teacher = forms.ModelChoiceField(
        queryset=Teacher.objects.order_by("first_name", "last_name"),
        required=False,
        empty_label="All governors",
    )
    location = forms.ModelChoiceField(
        queryset=Location.objects.order_by("name"),
        required=False,
        empty_label="All locations",
    )
    def clean(self):
        cleaned = super().clean()
        start_date = cleaned.get("start_date")
        end_date = cleaned.get("end_date")
        report_by = cleaned.get("report_by")
        teacher = cleaned.get("teacher")
        location = cleaned.get("location")

        if start_date and end_date and start_date > end_date:
            raise forms.ValidationError("start_date must be on or before end_date.")

        if report_by == "teacher" and not teacher:
            self.add_error("teacher", "Select a governor for a governor report.")
        if report_by == "location" and not location:
            self.add_error("location", "Select a location for a location report.")
        return cleaned


class ProspectPipelineFilterForm(forms.Form):
    q = forms.CharField(required=False, label="Search")
    status = forms.ChoiceField(
        required=False,
        choices=[("", "All statuses")] + list(ProspectStatus.choices),
    )
    interest_level = forms.ChoiceField(
        required=False,
        choices=[("", "All interest levels")] + list(InterestLevel.choices),
    )
    preferred_contact_method = forms.ChoiceField(
        required=False,
        choices=[("", "All contact methods")] + list(ContactMethod.choices),
    )
    converted = forms.ChoiceField(
        required=False,
        choices=(
            ("", "All"),
            ("yes", "Converted"),
            ("no", "Not Converted"),
        ),
    )


class ProspectFollowUpForm(forms.Form):
    channel = forms.ChoiceField(choices=CommunicationChannel.choices)
    subject = forms.CharField(required=False, max_length=255)
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}))


class EnrollmentForm(forms.ModelForm):
    enrollment_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    fee_amount = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.00"),
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
    )
    discount_amount = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.00"),
        required=False,
        initial=Decimal("0.00"),
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
    )
    balance_due = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        required=False,
        disabled=True,
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )

    class Meta:
        model = Enrollment
        fields = "__all__"

    @staticmethod
    def _as_money(value):
        value = value if value is not None else Decimal("0.00")
        return Decimal(value).quantize(Decimal("0.01"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and self.instance.enrollment_date:
            self.initial["enrollment_date"] = timezone.localtime(
                self.instance.enrollment_date
            ).date()
        fee = self.initial.get("fee_amount", getattr(self.instance, "fee_amount", Decimal("0.00")))
        discount = self.initial.get(
            "discount_amount",
            getattr(self.instance, "discount_amount", Decimal("0.00")),
        )
        self.initial["discount_amount"] = self._as_money(discount)
        self.initial["balance_due"] = self._as_money(fee) - self._as_money(discount)

    def clean(self):
        cleaned = super().clean()
        fee = self._as_money(cleaned.get("fee_amount"))
        discount = self._as_money(cleaned.get("discount_amount"))
        if discount > fee:
            self.add_error("discount_amount", "Discount amount cannot exceed fee amount.")
        balance_due = self._as_money(fee - discount)
        if balance_due < Decimal("0.00"):
            raise forms.ValidationError("Balance due cannot be negative.")
        cleaned["fee_amount"] = fee
        cleaned["discount_amount"] = discount
        cleaned["balance_due"] = balance_due
        return cleaned

    def clean_enrollment_date(self):
        enrollment_day = self.cleaned_data.get("enrollment_date")
        if enrollment_day is None:
            return enrollment_day
        local_dt = datetime.combine(enrollment_day, time.min)
        return timezone.make_aware(local_dt, timezone.get_current_timezone())

    def save(self, commit=True):
        enrollment = super().save(commit=False)
        enrollment.balance_due = self.cleaned_data["balance_due"]
        if commit:
            enrollment.save()
            self.save_m2m()
        return enrollment


class DisbursementReportingFilterForm(forms.Form):
    REPORT_BY_CHOICES = (
        ("teacher", "Governor Disbursement"),
        ("location", "Location Disbursement"),
    )

    report_by = forms.ChoiceField(choices=REPORT_BY_CHOICES, initial="teacher")
    start_date = forms.DateField(
        required=True,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    end_date = forms.DateField(
        required=True,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    teacher = forms.ModelChoiceField(
        queryset=Teacher.objects.order_by("first_name", "last_name"),
        required=False,
        empty_label="All governors",
    )
    location = forms.ModelChoiceField(
        queryset=Location.objects.order_by("name"),
        required=False,
        empty_label="All locations",
    )

    def clean(self):
        cleaned = super().clean()
        start_date = cleaned.get("start_date")
        end_date = cleaned.get("end_date")
        if start_date and end_date and start_date > end_date:
            raise forms.ValidationError("start_date must be on or before end_date.")
        return cleaned


class InvoicePaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = (
            "amount_paid",
            "payment_date",
            "payment_method",
            "reference_number",
            "notes",
        )
        widgets = {
            "payment_date": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["payment_date"].input_formats = ("%Y-%m-%dT%H:%M",)


class PaymentForm(forms.ModelForm):
    student = forms.ModelChoiceField(
        queryset=Student.objects.none(),
        required=False,
        help_text="Select a student to narrow invoice options.",
    )

    class Meta:
        model = Payment
        fields = (
            "student",
            "invoice",
            "payment_date",
            "amount_paid",
            "payment_method",
            "reference_number",
            "confirmation_status",
            "notes",
            "owner",
        )
        widgets = {
            "payment_date": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    @staticmethod
    def _open_invoice_queryset(*, user, student_id=None):
        queryset = (
            Invoice.objects.select_related(
                "enrollment__student__prospect__contact",
                "enrollment__session__course",
            )
            .annotate(
                amount_paid=Coalesce(
                    Sum("payments__amount_paid"),
                    Value(Decimal("0.00")),
                    output_field=DecimalField(max_digits=10, decimal_places=2),
                )
            )
            .annotate(
                outstanding_balance=ExpressionWrapper(
                    F("total_amount") - F("amount_paid"),
                    output_field=DecimalField(max_digits=10, decimal_places=2),
                )
            )
            .filter(outstanding_balance__gt=Decimal("0.00"))
        )
        if user and getattr(user, "is_authenticated", False) and not (user.is_staff or user.is_superuser):
            queryset = queryset.filter(owner=user)
        if student_id:
            queryset = queryset.filter(enrollment__student_id=student_id)
        return queryset.order_by("-issue_date", "-pk")

    @staticmethod
    def _invoice_label(invoice):
        student_name = str(invoice.enrollment.student.prospect)
        course_name = invoice.enrollment.session.course.name
        total = (invoice.total_amount or Decimal("0.00")).quantize(Decimal("0.01"))
        paid = (invoice.amount_paid or Decimal("0.00")).quantize(Decimal("0.01"))
        outstanding = (invoice.outstanding_balance or Decimal("0.00")).quantize(Decimal("0.01"))
        return (
            f"{invoice.invoice_number} | {student_name} | {course_name} | "
            f"Total ${total} | Paid ${paid} | Due ${outstanding}"
        )

    def __init__(self, *args, **kwargs):
        self.request_user = kwargs.pop("request_user", None)
        self.selected_student_id = kwargs.pop("selected_student_id", None)
        super().__init__(*args, **kwargs)
        self.fields["payment_date"].input_formats = ("%Y-%m-%dT%H:%M",)
        self.fields["student"].queryset = Student.objects.select_related(
            "prospect__contact"
        ).order_by("prospect__contact__first_name", "prospect__contact__last_name")

        if self.request_user and not (self.request_user.is_staff or self.request_user.is_superuser):
            self.fields["student"].queryset = self.fields["student"].queryset.filter(
                owner=self.request_user
            )

        bound_student = (
            self.data.get("student")
            if self.is_bound
            else self.selected_student_id or self.initial.get("student")
        )
        if bound_student and str(bound_student).isdigit():
            self.selected_student_id = int(bound_student)
            self.initial["student"] = int(bound_student)
        is_create = not (self.instance and self.instance.pk)
        self.no_student_selected = is_create and not self.selected_student_id
        if self.no_student_selected:
            self._allowed_open_invoice_ids = set()
            self.fields["invoice"].queryset = Invoice.objects.none()
            self.fields["invoice"].choices = [("", "---------")]
            self.fields["invoice"].disabled = True
            self.fields["invoice"].help_text = "Select a student to see available invoices."
            self.no_open_invoices = False
        else:
            open_invoices = self._open_invoice_queryset(
                user=self.request_user,
                student_id=self.selected_student_id,
            )
            open_ids = list(open_invoices.values_list("id", flat=True))
            if self.instance and self.instance.pk and self.instance.invoice_id:
                if self.instance.invoice_id not in open_ids:
                    open_ids.append(self.instance.invoice_id)
                open_invoices = (
                    Invoice.objects.filter(pk__in=open_ids)
                    .select_related(
                        "enrollment__student__prospect__contact",
                        "enrollment__session__course",
                    )
                    .annotate(
                        amount_paid=Coalesce(
                            Sum("payments__amount_paid"),
                            Value(Decimal("0.00")),
                            output_field=DecimalField(max_digits=10, decimal_places=2),
                        )
                    )
                    .annotate(
                        outstanding_balance=ExpressionWrapper(
                            F("total_amount") - F("amount_paid"),
                            output_field=DecimalField(max_digits=10, decimal_places=2),
                        )
                    )
                    .order_by("-issue_date", "-pk")
                )

            self._allowed_open_invoice_ids = set(open_invoices.values_list("id", flat=True))
            self.fields["invoice"].queryset = open_invoices
            self.fields["invoice"].choices = [
                ("", "---------"),
                *[(invoice.pk, self._invoice_label(invoice)) for invoice in open_invoices],
            ]

            if self.selected_student_id and open_invoices.count() == 1 and not self.is_bound:
                self.initial["invoice"] = open_invoices.first().pk

            open_invoice_count = self._open_invoice_queryset(
                user=self.request_user,
                student_id=self.selected_student_id,
            ).count()
            self.no_open_invoices = open_invoice_count == 0 and not (self.instance and self.instance.pk)
            if self.no_open_invoices:
                self.fields["invoice"].help_text = (
                    "No open invoices found for this selection. Create or adjust an invoice first."
                )
                for field_name in ("invoice", "amount_paid", "payment_method", "confirmation_status"):
                    self.fields[field_name].disabled = True

        if self.instance and self.instance.pk and self.instance.payment_date:
            payment_date_value = self.instance.payment_date
            if timezone.is_aware(payment_date_value):
                payment_date_value = timezone.localtime(payment_date_value)
            self.initial["payment_date"] = payment_date_value.strftime("%Y-%m-%dT%H:%M")

    def clean_invoice(self):
        invoice = self.cleaned_data.get("invoice")
        student = self.cleaned_data.get("student")
        if not invoice:
            return invoice

        if (
            self.request_user
            and getattr(self.request_user, "is_authenticated", False)
            and not (self.request_user.is_staff or self.request_user.is_superuser)
            and invoice.owner_id != self.request_user.id
        ):
            raise forms.ValidationError("Selected invoice does not belong to your account.")

        if student and invoice.enrollment.student_id != student.pk:
            raise forms.ValidationError("Selected invoice does not belong to the selected student.")
        return invoice

    def clean(self):
        cleaned = super().clean()
        if self.no_student_selected:
            self.add_error("student", "Select a student to see available invoices.")
            return cleaned
        if self.no_open_invoices and not (self.instance and self.instance.pk):
            raise forms.ValidationError(
                "No open invoices available for payment creation with the current selection."
            )

        invoice = cleaned.get("invoice")
        amount_paid = cleaned.get("amount_paid")

        if (
            invoice
            and not (self.instance and self.instance.pk)
            and self._allowed_open_invoice_ids
            and invoice.pk not in self._allowed_open_invoice_ids
        ):
            self.add_error("invoice", "Select a valid open invoice for this student/owner.")

        if invoice and amount_paid:
            paid_queryset = invoice.payments.all()
            if self.instance and self.instance.pk:
                paid_queryset = paid_queryset.exclude(pk=self.instance.pk)
            paid_total = (
                paid_queryset.aggregate(
                    total=Coalesce(
                        Sum("amount_paid"),
                        Value(Decimal("0.00")),
                        output_field=DecimalField(max_digits=10, decimal_places=2),
                    )
                )["total"]
                or Decimal("0.00")
            )
            outstanding = (invoice.total_amount or Decimal("0.00")) - paid_total
            if amount_paid > outstanding:
                self.add_error(
                    "amount_paid",
                    f"Amount cannot exceed outstanding balance of ${outstanding.quantize(Decimal('0.01'))}.",
                )
        return cleaned


class CommunicationForm(forms.ModelForm):
    class Meta:
        model = Communication
        fields = (
            "recipient_type",
            "prospect",
            "student",
            "enrollment",
            "channel",
            "communication_type",
            "subject",
            "body",
            "sent_at",
            "delivery_status",
            "provider_status",
            "related_entity_type",
            "related_entity_id",
            "notes",
            "owner",
        )
        widgets = {
            "sent_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "body": forms.Textarea(attrs={"rows": 4}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            css_class = "form-control"
            if isinstance(field.widget, forms.Select):
                css_class = "form-select"
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{existing} {css_class}".strip()
        self.fields["recipient_type"].label = "Recipient Type"
        self.fields["communication_type"].label = "Message Type"
        self.fields["sent_at"].label = "Sent On"
        self.fields["subject"].help_text = "Optional short title for easy timeline scanning."
        self.fields["body"].help_text = "Main communication content."
        self.fields["delivery_status"].help_text = "Current delivery outcome."
        self.fields["enrollment"].help_text = (
            "Optional. Link only when this communication is about a specific enrollment."
        )
        self.fields["provider_status"].help_text = (
            "Optional provider response (for example: delivered, failed, bounced)."
        )
        self.fields["related_entity_type"].help_text = (
            "Optional advanced reference to another entity type."
        )
        self.fields["related_entity_id"].help_text = (
            "Optional advanced reference to another entity ID."
        )
        self.fields["prospect"].required = False
        self.fields["student"].required = False
        self.fields["enrollment"].required = False
        self.fields["subject"].required = False
        self.fields["sent_at"].required = False
        self.fields["provider_status"].required = False
        self.fields["related_entity_type"].required = False
        self.fields["related_entity_id"].required = False
        self.fields["notes"].required = False
        self.fields["prospect"].queryset = Prospect.objects.order_by(
            "contact__first_name", "contact__last_name"
        )
        self.fields["student"].queryset = Student.objects.select_related(
            "prospect__contact"
        ).order_by("prospect__contact__first_name", "prospect__contact__last_name")
        self.fields["enrollment"].queryset = self.fields["enrollment"].queryset.none()
        self.fields["channel"].initial = CommunicationChannel.EMAIL
        self.fields["delivery_status"].initial = self.fields["delivery_status"].initial or "queued"
        self.fields["owner"].required = False
        self.fields["owner"].help_text = "Auto-assigned for non-superusers."
        recipient_type = (
            self.initial.get("recipient_type")
            or (self.instance.recipient_type if self.instance and self.instance.pk else "")
        )
        selected_student = self.initial.get("student") or (
            self.instance.student_id if self.instance and self.instance.pk else None
        )
        selected_prospect = self.initial.get("prospect") or (
            self.instance.prospect_id if self.instance and self.instance.pk else None
        )
        if recipient_type == "student" and selected_student:
            self.fields["enrollment"].queryset = (
                self.fields["student"].queryset.filter(pk=selected_student)
                .first()
                .enrollments.order_by("-enrollment_date")
            )
        elif recipient_type == "prospect" and selected_prospect:
            student = self.fields["student"].queryset.filter(prospect_id=selected_prospect).first()
            if student:
                self.fields["enrollment"].queryset = student.enrollments.order_by(
                    "-enrollment_date"
                )
        self.fields["sent_at"].input_formats = ("%Y-%m-%dT%H:%M",)
        self.fields["sent_at"].error_messages.update(
            {
                "invalid": "Enter a valid date and time (YYYY-MM-DD HH:MM).",
            }
        )
        if self.instance and self.instance.pk and self.instance.sent_at:
            sent_at_value = self.instance.sent_at
            if timezone.is_aware(sent_at_value):
                sent_at_value = timezone.localtime(sent_at_value)
            self.initial["sent_at"] = sent_at_value.strftime("%Y-%m-%dT%H:%M")

    def clean(self):
        cleaned = super().clean()
        recipient_type = cleaned.get("recipient_type")
        prospect = cleaned.get("prospect")
        student = cleaned.get("student")
        if recipient_type == "prospect":
            if not prospect:
                self.add_error("prospect", "Select a prospect for this recipient type.")
            if student:
                self.add_error("student", "Leave student empty when recipient type is prospect.")
        elif recipient_type == "student":
            if not student:
                self.add_error("student", "Select a student for this recipient type.")
            if prospect:
                self.add_error("prospect", "Leave prospect empty when recipient type is student.")

        if cleaned.get("channel") != CommunicationChannel.EMAIL:
            self.add_error("channel", "Only Email channel is supported for in-CRM sending.")

        recipient_email = ""
        if recipient_type == RecipientType.PROSPECT and prospect and prospect.contact_id:
            recipient_email = (prospect.contact.email or "").strip()
        elif (
            recipient_type == RecipientType.STUDENT
            and student
            and student.prospect_id
            and student.prospect.contact_id
        ):
            recipient_email = (student.prospect.contact.email or "").strip()
        if recipient_type in {RecipientType.PROSPECT, RecipientType.STUDENT} and not recipient_email:
            raise forms.ValidationError("Selected recipient does not have an email address.")
        return cleaned


class ProspectForm(forms.ModelForm):
    contact_first_name = forms.CharField(required=False, max_length=100)
    contact_last_name = forms.CharField(required=False, max_length=100)
    contact_email = forms.EmailField(required=False)
    contact_phone_number = forms.CharField(required=False, max_length=30)

    class Meta:
        model = Prospect
        fields = (
            "preferred_contact_method",
            "source",
            "status",
            "teacher",
            "interest_level",
            "notes",
            "owner",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.contact_id:
            self.initial["contact_first_name"] = self.instance.contact.first_name
            self.initial["contact_last_name"] = self.instance.contact.last_name
            self.initial["contact_email"] = self.instance.contact.email
            self.initial["contact_phone_number"] = self.instance.contact.phone_number

    def clean(self):
        cleaned = super().clean()
        contact = self.instance.contact if self.instance and self.instance.contact_id else None
        first_name = (cleaned.get("contact_first_name") or "").strip()
        last_name = (cleaned.get("contact_last_name") or "").strip()
        email = (cleaned.get("contact_email") or "").strip()
        phone_number = (cleaned.get("contact_phone_number") or "").strip()

        if not contact and not (first_name and last_name):
            raise forms.ValidationError(
                "Provide at least first and last name for this prospect."
            )
        if not contact:
            contact, _ = Contact.get_or_create_from_identity(
                first_name=first_name,
                last_name=last_name,
                email=email,
                phone_number=phone_number,
            )
        cleaned["contact"] = contact
        return cleaned

    def save(self, commit=True):
        prospect = super().save(commit=False)
        contact = self.cleaned_data.get("contact")

        if contact:
            first_name = (self.cleaned_data.get("contact_first_name") or "").strip()
            last_name = (self.cleaned_data.get("contact_last_name") or "").strip()
            email = (self.cleaned_data.get("contact_email") or "").strip()
            phone_number = (self.cleaned_data.get("contact_phone_number") or "").strip()

            if first_name:
                contact.first_name = first_name
            if last_name:
                contact.last_name = last_name
            contact.email = email or None
            contact.phone_number = phone_number or None

            contact.full_clean()
            if commit:
                contact.save()

            prospect.contact = contact

        if commit:
            prospect.save()
            self.save_m2m()
        return prospect


class StudentForm(forms.ModelForm):
    class Meta:
        model = Student
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        prospect = cleaned.get("prospect")
        if prospect and not prospect.contact_id:
            self.add_error("prospect", "Selected prospect must have a linked contact.")
        return cleaned
