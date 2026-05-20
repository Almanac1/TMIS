from datetime import datetime, time
from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Column, Div, Field, Fieldset, HTML, Layout, Row, Submit
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
    Course,
    CourseSession,
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
    PERSON_TYPE_CHOICES = (
        ("student", "Existing Student"),
        ("prospect", "Existing Prospect"),
        ("contact", "Existing Contact"),
        ("new_prospect", "New Prospect"),
    )

    person_type = forms.ChoiceField(choices=PERSON_TYPE_CHOICES, initial="student")
    student = forms.ModelChoiceField(queryset=Student.objects.none(), required=False)
    prospect = forms.ModelChoiceField(queryset=Prospect.objects.none(), required=False)
    contact = forms.ModelChoiceField(queryset=Contact.objects.none(), required=False)
    new_first_name = forms.CharField(required=False, max_length=100)
    new_last_name = forms.CharField(required=False, max_length=100)
    new_email = forms.EmailField(required=False)
    new_phone_number = forms.CharField(required=False, max_length=30)
    new_source = forms.CharField(required=False, max_length=100)
    new_notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    course = forms.ModelChoiceField(queryset=Course.objects.none(), required=False)

    enrollment_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    session = forms.ModelChoiceField(queryset=CourseSession.objects.none())
    fee_amount = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.00"),
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0", "readonly": "readonly"}),
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
        widget=forms.NumberInput(attrs={"step": "0.01", "readonly": "readonly", "class": "bg-light"}),
    )
    number_of_children_under_18 = forms.IntegerField(
        required=False,
        min_value=0,
        initial=0,
        widget=forms.NumberInput(attrs={"min": "0"}),
    )

    class Meta:
        model = Enrollment
        fields = "__all__"

    @staticmethod
    def _as_money(value):
        value = value if value is not None else Decimal("0.00")
        return Decimal(value).quantize(Decimal("0.01"))

    @staticmethod
    def _is_tm_family_course(course):
        if not course:
            return False
        code = (getattr(course, "code", "") or "").strip().upper().replace("-", "")
        name = (getattr(course, "name", "") or "").strip().lower()
        return code in {"TMF", "TMFM"} or "family" in name

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.form_id = "enrollment-form"
        self.helper.layout = Layout(
            HTML(
                """
                <div class="enroll-step-strip mb-4">
                  <span class="enroll-step-chip active">Step 1 Person</span>
                  <span class="enroll-step-chip">Step 2 Course</span>
                  <span class="enroll-step-chip">Step 3 Pricing</span>
                  <span class="enroll-step-chip">Step 4 Details</span>
                </div>
                """
            ),
            Div(
                HTML('<div class="enroll-step-kicker">Step 1</div><h2 class="enroll-step-title">Person Type & Person Lookup</h2>'),
                Fieldset(
                    "",
                    Row(Column("person_type", css_class="col-12 col-xl-6")),
                    HTML(
                        """
                        <div id="search-step-wrap" class="mb-3">
                          <label for="entity-search-input" class="form-label">Search and select person</label>
                          <div class="input-group input-group-lg">
                            <span class="input-group-text"><i class="bi bi-search"></i></span>
                            <input type="text" id="entity-search-input" class="form-control" placeholder="Type at least 2 characters">
                          </div>
                          <div class="form-text">Search by first name, last name, email, or phone.</div>
                          <div id="entity-search-results" class="list-group mt-2"></div>
                          <div id="selected-person" class="alert alert-light border mt-2 d-none mb-0"></div>
                        </div>
                        """
                    ),
                    Div(
                        Row(
                            Column("new_first_name", css_class="col-12 col-lg-6"),
                            Column("new_last_name", css_class="col-12 col-lg-6"),
                            Column("new_email", css_class="col-12 col-lg-6"),
                            Column("new_phone_number", css_class="col-12 col-lg-6"),
                            Column("new_source", css_class="col-12 col-lg-6"),
                            Column("new_notes", css_class="col-12"),
                        ),
                        css_id="new-prospect-wrap",
                        css_class="d-none",
                    ),
                    Field("student", type="hidden"),
                    Field("prospect", type="hidden"),
                    Field("contact", type="hidden"),
                ),
                css_class="card shadow-sm rounded-4 border-0 p-4 mb-4 enroll-step-card",
            ),
            Div(
                HTML('<div class="enroll-step-kicker">Step 2</div><h2 class="enroll-step-title">Course & Session</h2>'),
                Fieldset(
                    "",
                    Row(
                        Column("course", css_class="col-12 col-lg-6"),
                    ),
                    Field("session", type="hidden"),
                    HTML('<div id="course-preview" class="enroll-course-preview mt-2"></div>'),
                ),
                css_class="card shadow-sm rounded-4 border-0 p-4 mb-4 enroll-step-card",
            ),
            Div(
                HTML('<div class="enroll-step-kicker">Step 3</div><h2 class="enroll-step-title">Pricing</h2>'),
                Fieldset(
                    "",
                    Row(
                        Column("fee_amount", css_class="col-12 col-md-6 col-xl-3"),
                        Column("discount_amount", css_class="col-12 col-md-6 col-xl-3"),
                        Column(
                            Div("number_of_children_under_18", css_id="children-wrap", css_class="enroll-collapse d-none"),
                            css_class="col-12 col-md-6 col-xl-3",
                        ),
                        Column("balance_due", css_class="col-12 col-md-6 col-xl-3"),
                    ),
                    HTML(
                        """
                        <div class="card border-0 bg-light mt-3" id="price-summary-panel">
                          <div class="card-body py-3">
                            <div class="small text-uppercase text-muted fw-semibold mb-2">Price Summary</div>
                            <div class="d-flex justify-content-between"><span>Course fee</span><strong id="summary-course-fee">0.00</strong></div>
                            <div class="d-flex justify-content-between"><span>Discount</span><strong id="summary-discount">0.00</strong></div>
                            <div class="d-flex justify-content-between d-none" id="summary-children-row"><span>Children fee</span><strong id="summary-children-fee">0.00</strong></div>
                            <hr class="my-2">
                            <div class="d-flex justify-content-between"><span>Balance due</span><strong id="summary-balance">0.00</strong></div>
                          </div>
                        </div>
                        """
                    ),
                ),
                css_class="card shadow-sm rounded-4 border-0 p-4 mb-4 enroll-step-card",
            ),
            Div(
                HTML('<div class="enroll-step-kicker">Step 4</div><h2 class="enroll-step-title">Enrollment Details</h2>'),
                Fieldset(
                    "",
                    Row(
                        Column("enrollment_date", css_class="col-12 col-lg-6"),
                        Column("status", css_class="col-12 col-lg-6"),
                    ),
                ),
                css_class="card shadow-sm rounded-4 border-0 p-4 mb-5 enroll-step-card",
            ),
            Div(
                Submit("submit", "Create Enrollment", css_class="btn btn-primary px-4"),
                HTML('<a class="btn btn-outline-secondary" href="../">Cancel</a>'),
                css_class="sticky-action-bar d-flex flex-wrap gap-2 justify-content-end",
            ),
        )
        self.fields["student"].queryset = Student.objects.select_related("prospect__contact").order_by(
            "prospect__contact__first_name",
            "prospect__contact__last_name",
        )
        self.fields["prospect"].queryset = Prospect.objects.select_related("contact").filter(
            is_archived=False
        ).order_by("contact__first_name", "contact__last_name")
        self.fields["contact"].queryset = Contact.objects.order_by("first_name", "last_name")
        self.fields["course"].queryset = Course.objects.filter(status="active").order_by("name")
        session_qs = CourseSession.objects.select_related("course", "teacher", "location").order_by("-start_date")
        selected_course = None
        if self.is_bound:
            selected_course_id = (self.data.get("course") or "").strip()
            if selected_course_id.isdigit():
                selected_course = self.fields["course"].queryset.filter(pk=int(selected_course_id)).first()
        elif self.instance and self.instance.pk and self.instance.session_id:
            selected_course = self.instance.session.course
            self.initial["course"] = selected_course.pk
        if selected_course is not None:
            session_qs = session_qs.filter(course=selected_course)
        self.fields["session"].queryset = session_qs
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
        if self.instance and self.instance.pk and self.instance.session_id:
            self.initial["number_of_children_under_18"] = 0
            self.fields["student"].initial = self.instance.student_id
            self.fields["person_type"].initial = "student"

    def clean(self):
        cleaned = super().clean()
        person_type = cleaned.get("person_type")
        student = cleaned.get("student")
        prospect = cleaned.get("prospect")
        contact = cleaned.get("contact")
        selected_course = cleaned.get("course")
        session = cleaned.get("session")
        children_count = cleaned.get("number_of_children_under_18") or 0

        if person_type == "student" and not student:
            self.add_error("student", "Select an existing student.")
        elif person_type == "prospect" and not prospect:
            self.add_error("prospect", "Select an existing prospect.")
        elif person_type == "contact" and not contact:
            self.add_error("contact", "Select an existing contact.")
        elif person_type == "new_prospect":
            if not cleaned.get("new_first_name"):
                self.add_error("new_first_name", "First name is required.")
            if not cleaned.get("new_last_name"):
                self.add_error("new_last_name", "Last name is required.")

        if not selected_course:
            self.add_error("course", "Select a course.")
        if not session and selected_course:
            session = (
                CourseSession.objects.filter(course=selected_course)
                .order_by("-start_date", "-pk")
                .first()
            )
            cleaned["session"] = session
        if not session:
            self.add_error("session", "No session is available for the selected course.")
        if selected_course and session and session.course_id != selected_course.pk:
            self.add_error("session", "Selected session does not belong to the selected course.")

        is_tm_family = self._is_tm_family_course(selected_course)
        if is_tm_family:
            if children_count < 0:
                self.add_error("number_of_children_under_18", "Children count cannot be negative.")
            fee = self._as_money(Decimal("4500.00") + (Decimal("750.00") * Decimal(children_count)))
        else:
            children_count = 0
            fee = self._as_money(
                selected_course.standard_fee if selected_course else cleaned.get("fee_amount")
            )

        discount = self._as_money(cleaned.get("discount_amount"))
        if discount > fee:
            self.add_error("discount_amount", "Discount amount cannot exceed fee amount.")
        balance_due = self._as_money(fee - discount)
        if balance_due < Decimal("0.00"):
            raise forms.ValidationError("Balance due cannot be negative.")
        cleaned["fee_amount"] = fee
        cleaned["discount_amount"] = discount
        cleaned["balance_due"] = balance_due
        cleaned["number_of_children_under_18"] = children_count
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
    prospect_is_existing_contact = forms.BooleanField(
        required=False,
        label="Prospect is an existing contact",
    )
    selected_contact = forms.ModelChoiceField(
        queryset=Contact.objects.none(),
        required=False,
        label="Existing contact",
    )
    governor_assigned = forms.ModelChoiceField(
        queryset=get_user_model().objects.none(),
        required=False,
        label="Governor assigned",
    )
    first_name = forms.CharField(required=False, max_length=100)
    last_name = forms.CharField(required=False, max_length=100)
    email = forms.EmailField(required=False)
    phone_number = forms.CharField(required=False, max_length=30)

    class Meta:
        model = Prospect
        fields = (
            "preferred_contact_method",
            "source",
            "status",
            "course_interest",
            "interest_level",
            "notes",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["selected_contact"].queryset = Contact.objects.order_by("first_name", "last_name")
        self.fields["governor_assigned"].queryset = get_user_model().objects.order_by("username")
        self.fields["course_interest"].queryset = Course.objects.filter(status="active").order_by("name")
        self.fields["course_interest"].label = "Course interest"
        self.fields["course_interest"].help_text = "Select the program this prospect is most likely interested in."
        self.fields["course_interest"].label_from_instance = (
            lambda obj: f"{obj.name} ({getattr(obj, 'code', '-')})"
        )
        self.fields["notes"].widget.attrs.update({"rows": 4})
        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.layout = Layout(
            Div(
                HTML('<h2 class="prospect-section-title">Contact source</h2>'),
                Div(
                    Field("prospect_is_existing_contact"),
                    css_class="prospect-toggle-row",
                ),
                Div(
                    Field("selected_contact", css_class="d-none"),
                    HTML(
                        """
                        <label for="existing-contact-search" class="form-label">Existing contact</label>
                        <input type="text" id="existing-contact-search" class="form-control" placeholder="Type at least 2 characters to search contacts">
                        <div class="form-text">Search by first name, last name, email, or phone number.</div>
                        <div id="existing-contact-results" class="list-group mt-2"></div>
                        <div id="existing-contact-selected" class="alert alert-light border mt-2 d-none mb-0"></div>
                        """
                    ),
                    css_id="existing-contact-wrap",
                    css_class="d-none",
                ),
                css_class="prospect-section-card",
            ),
            Div(
                HTML('<h2 class="prospect-section-title">Contact details</h2>'),
                Div(
                    Row(
                        Column("first_name", css_class="col-12 col-lg-6"),
                        Column("last_name", css_class="col-12 col-lg-6"),
                        Column("email", css_class="col-12 col-lg-6"),
                        Column("phone_number", css_class="col-12 col-lg-6"),
                    ),
                    css_id="new-contact-wrap",
                ),
                css_class="prospect-section-card",
            ),
            Div(
                HTML('<h2 class="prospect-section-title">Prospect details</h2>'),
                Row(
                    Column("preferred_contact_method", css_class="col-12 col-lg-6"),
                    Column("status", css_class="col-12 col-lg-6"),
                ),
                Row(
                    Column("course_interest", css_class="col-12 col-lg-6"),
                    Column("interest_level", css_class="col-12 col-lg-6"),
                ),
                Row(
                    Column("source", css_class="col-12 col-lg-6"),
                    Column("governor_assigned", css_class="col-12 col-lg-6"),
                ),
                css_class="prospect-section-card",
            ),
            Div(
                HTML('<h2 class="prospect-section-title">Assignment</h2>'),
                HTML('<p class="text-muted small mb-0">Prospect ownership is assigned through <strong>Governor assigned</strong> above.</p>'),
                css_class="prospect-section-card",
            ),
            Div(
                HTML('<h2 class="prospect-section-title">Notes</h2>'),
                Row(Column("notes", css_class="col-12")),
                css_class="prospect-section-card",
            ),
            Div(
                Submit("submit", "Create Prospect", css_class="btn btn-primary"),
                HTML('<a class="btn btn-outline-secondary" href="../">Cancel</a>'),
                css_class="prospect-action-bar d-flex gap-2 justify-content-end",
            ),
        )
        if self.instance and self.instance.contact_id:
            self.initial["prospect_is_existing_contact"] = True
            self.initial["selected_contact"] = self.instance.contact
            self.initial["first_name"] = self.instance.contact.first_name
            self.initial["last_name"] = self.instance.contact.last_name
            self.initial["email"] = self.instance.contact.email
            self.initial["phone_number"] = self.instance.contact.phone_number
        if self.instance and self.instance.owner_id:
            self.initial["governor_assigned"] = self.instance.owner_id

    def clean(self):
        cleaned = super().clean()
        use_existing_contact = bool(cleaned.get("prospect_is_existing_contact"))
        contact = cleaned.get("selected_contact")
        if not contact and self.instance and self.instance.contact_id:
            contact = self.instance.contact

        first_name = (
            (cleaned.get("first_name") or "").strip()
            or (self.data.get("contact_first_name") or "").strip()
        )
        last_name = (
            (cleaned.get("last_name") or "").strip()
            or (self.data.get("contact_last_name") or "").strip()
        )
        email = (
            (cleaned.get("email") or "").strip()
            or (self.data.get("contact_email") or "").strip()
        )
        phone_number = (
            (cleaned.get("phone_number") or "").strip()
            or (self.data.get("contact_phone_number") or "").strip()
        )

        if use_existing_contact and not contact:
            self.add_error("selected_contact", "Select an existing contact.")
            return cleaned

        if not use_existing_contact:
            if not first_name or not last_name:
                raise forms.ValidationError("First and last name are required for new contacts.")
            if not email and not phone_number:
                raise forms.ValidationError("Provide at least an email or phone number for new contacts.")
            if not contact:
                if email:
                    contact = Contact.objects.filter(email__iexact=email).first()
                if not contact and phone_number:
                    contact = Contact.find_matching_contact(phone_number=phone_number)
                if not contact and first_name and last_name:
                    contact = Contact.objects.filter(
                        first_name__iexact=first_name,
                        last_name__iexact=last_name,
                    ).first()

        if not contact:
            contact = Contact.objects.create(
                first_name=first_name,
                last_name=last_name,
                email=email or None,
                phone_number=phone_number or None,
            )

        existing_prospect = Prospect.objects.filter(contact=contact).exclude(pk=self.instance.pk).first()
        if existing_prospect:
            raise forms.ValidationError(
                f"Selected contact is already linked to Prospect #{existing_prospect.pk}."
            )

        self.instance.contact = contact
        self.instance.owner = cleaned.get("governor_assigned")
        cleaned["contact"] = contact
        cleaned["first_name"] = first_name
        cleaned["last_name"] = last_name
        cleaned["email"] = email
        cleaned["phone_number"] = phone_number
        return cleaned

    def save(self, commit=True):
        prospect = super().save(commit=False)
        contact = self.cleaned_data.get("contact")
        use_existing_contact = bool(self.cleaned_data.get("prospect_is_existing_contact"))

        if contact:
            first_name = (self.cleaned_data.get("first_name") or "").strip()
            last_name = (self.cleaned_data.get("last_name") or "").strip()
            email = (self.cleaned_data.get("email") or "").strip()
            phone_number = (self.cleaned_data.get("phone_number") or "").strip()

            if not use_existing_contact:
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
        prospect.owner = self.cleaned_data.get("governor_assigned") or prospect.owner

        if commit:
            prospect.save()
            self.save_m2m()
        return prospect


class StudentForm(forms.ModelForm):
    class Meta:
        model = Student
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "date_of_birth" in self.fields:
            self.fields["date_of_birth"].widget = forms.DateInput(attrs={"type": "date"})
        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.layout = Layout(
            *list(self.fields.keys()),
            Div(
                Submit("submit", "Save", css_class="btn btn-primary"),
                HTML('<a class="btn btn-outline-secondary" href="../">Cancel</a>'),
                css_class="d-flex gap-2 justify-content-end mt-3",
            ),
        )

    def clean(self):
        cleaned = super().clean()
        prospect = cleaned.get("prospect")
        if prospect and not prospect.contact_id:
            self.add_error("prospect", "Selected prospect must have a linked contact.")
        return cleaned


class StudentCreateForm(forms.ModelForm):
    PERSON_TYPE_CHOICES = (
        ("student", "Existing Student"),
        ("prospect", "Existing Prospect"),
        ("contact", "Existing Contact"),
        ("new_prospect", "New Prospect"),
    )

    person_type = forms.ChoiceField(choices=PERSON_TYPE_CHOICES, initial="prospect")
    student = forms.ModelChoiceField(queryset=Student.objects.none(), required=False)
    prospect = forms.ModelChoiceField(queryset=Prospect.objects.none(), required=False)
    contact = forms.ModelChoiceField(queryset=Contact.objects.none(), required=False)
    new_first_name = forms.CharField(required=False, max_length=100)
    new_last_name = forms.CharField(required=False, max_length=100)
    new_email = forms.EmailField(required=False)
    new_phone_number = forms.CharField(required=False, max_length=30)
    new_source = forms.CharField(required=False, max_length=100)
    new_notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    class Meta:
        model = Student
        fields = (
            "teacher",
            "date_of_birth",
            "address",
            "city",
            "province_state",
            "country",
            "enrollment_status",
            "notes",
            "owner",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "date_of_birth" in self.fields:
            self.fields["date_of_birth"].widget = forms.DateInput(attrs={"type": "date"})
        self.fields["student"].queryset = Student.objects.select_related("prospect__contact").order_by(
            "prospect__contact__first_name",
            "prospect__contact__last_name",
        )
        self.fields["prospect"].queryset = Prospect.objects.select_related("contact").order_by(
            "contact__first_name",
            "contact__last_name",
        )
        self.fields["contact"].queryset = Contact.objects.order_by("first_name", "last_name")

        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.form_id = "student-create-form"
        self.helper.layout = Layout(
            Div(
                HTML('<div class="enroll-step-kicker">Step 1</div><h2 class="enroll-step-title">Select person type</h2>'),
                Row(Column("person_type", css_class="col-12 col-xl-6")),
                HTML(
                    """
                    <div id="search-step-wrap" class="mb-3">
                      <label for="entity-search-input" class="form-label">Search and select person</label>
                      <div class="input-group input-group-lg">
                        <span class="input-group-text"><i class="bi bi-search"></i></span>
                        <input type="text" id="entity-search-input" class="form-control" placeholder="Type at least 2 characters">
                      </div>
                      <div class="form-text">Search by first name, last name, email, or phone.</div>
                      <div id="entity-search-results" class="list-group mt-2"></div>
                      <div id="selected-person" class="alert alert-light border mt-2 d-none mb-0"></div>
                    </div>
                    """
                ),
                Div(
                    Row(
                        Column("new_first_name", css_class="col-12 col-lg-6"),
                        Column("new_last_name", css_class="col-12 col-lg-6"),
                        Column("new_email", css_class="col-12 col-lg-6"),
                        Column("new_phone_number", css_class="col-12 col-lg-6"),
                        Column("new_source", css_class="col-12"),
                        Column("new_notes", css_class="col-12"),
                    ),
                    css_id="new-prospect-wrap",
                    css_class="d-none",
                ),
                Field("student", type="hidden"),
                Field("prospect", type="hidden"),
                Field("contact", type="hidden"),
                css_class="card shadow-sm rounded-4 border-0 p-4 mb-4 enroll-step-card",
            ),
            Div(
                HTML('<div class="enroll-step-kicker">Student details</div><h2 class="enroll-step-title">Profile & Assignment</h2>'),
                Row(
                    Column("teacher", css_class="col-12 col-lg-6"),
                    Column("enrollment_status", css_class="col-12 col-lg-6"),
                    Column("date_of_birth", css_class="col-12 col-lg-6"),
                    Column("city", css_class="col-12 col-lg-6"),
                    Column("province_state", css_class="col-12 col-lg-6"),
                    Column("country", css_class="col-12 col-lg-6"),
                    Column("address", css_class="col-12"),
                    Column("notes", css_class="col-12"),
                ),
                css_class="card shadow-sm rounded-4 border-0 p-4 mb-4 enroll-step-card",
            ),
            Div(
                Submit("submit", "Create Student", css_class="btn btn-primary px-4"),
                HTML('<a class="btn btn-outline-secondary" href="../">Cancel</a>'),
                css_class="sticky-action-bar d-flex flex-wrap gap-2 justify-content-end",
            ),
        )

    def clean(self):
        cleaned = super().clean()
        person_type = cleaned.get("person_type")
        selected_student = cleaned.get("student")
        selected_prospect = cleaned.get("prospect")
        selected_contact = cleaned.get("contact")

        if person_type == "student":
            if not selected_student:
                self.add_error("student", "Select an existing student.")
            else:
                self.add_error("student", f"Student #{selected_student.pk} already exists. Open the existing record instead.")
            return cleaned

        if person_type == "prospect" and not selected_prospect:
            self.add_error("prospect", "Select an existing prospect.")
        if person_type == "contact" and not selected_contact:
            self.add_error("contact", "Select an existing contact.")
        if person_type == "new_prospect":
            first_name = (cleaned.get("new_first_name") or "").strip()
            last_name = (cleaned.get("new_last_name") or "").strip()
            if not first_name or not last_name:
                raise forms.ValidationError("First and last name are required for new prospects.")

        return cleaned

    def save(self, commit=True):
        person_type = self.cleaned_data.get("person_type")

        if person_type == "prospect":
            prospect = self.cleaned_data.get("prospect")
            student, _ = prospect.convert_to_student()
        elif person_type == "contact":
            contact = self.cleaned_data.get("contact")
            prospect, _ = contact.convert_to_prospect(
                owner=self.cleaned_data.get("owner"),
                source="Student Creation",
                notes=self.cleaned_data.get("new_notes") or "",
            )
            student, _ = prospect.convert_to_student()
        elif person_type == "new_prospect":
            contact, _ = Contact.get_or_create_from_identity(
                first_name=(self.cleaned_data.get("new_first_name") or "").strip(),
                last_name=(self.cleaned_data.get("new_last_name") or "").strip(),
                email=(self.cleaned_data.get("new_email") or "").strip(),
                phone_number=(self.cleaned_data.get("new_phone_number") or "").strip(),
            )
            prospect, _ = contact.convert_to_prospect(
                owner=self.cleaned_data.get("owner"),
                source=(self.cleaned_data.get("new_source") or "").strip(),
                notes=(self.cleaned_data.get("new_notes") or "").strip(),
            )
            student, _ = prospect.convert_to_student()
        else:
            raise forms.ValidationError("Select a valid person type.")

        for field in (
            "teacher",
            "date_of_birth",
            "address",
            "city",
            "province_state",
            "country",
            "enrollment_status",
            "notes",
        ):
            setattr(student, field, self.cleaned_data.get(field))

        if not student.owner_id:
            student.owner = self.cleaned_data.get("owner")

        if commit:
            student.save()
        return student
