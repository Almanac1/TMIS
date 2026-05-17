import logging
from decimal import Decimal

from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.http import Http404, HttpRequest, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from django.db.models import Count, Sum
from django.utils import timezone

from .models import (
    Communication,
    Contact,
    Course,
    CourseSession,
    Disbursement,
    DisbursementStatus,
    Enrollment,
    Inquiry,
    InterviewForm,
    Invoice,
    Location,
    Meditator,
    MeditatorTransitionEvent,
    Payment,
    Prospect,
    Student,
    Teacher,
    TeacherSpecialization,
)
from .forms import DisbursementDateRangeReportForm, ProspectForm, StudentForm
from .services.ownership import scope_queryset_for_user
from .services.disbursements import generate_disbursement_for_enrollment
from .services.disbursement_reports import (
    get_disbursed_total_for_period,
)
from .services.invoicing import generate_invoice_for_enrollment

logger = logging.getLogger(__name__)


class OwnerScopedAdminMixin:
    """
    Enforce owner-based admin visibility for non-superusers.
    Superusers/staff retain global admin visibility.
    """

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return scope_queryset_for_user(
            queryset=queryset,
            model=self.model,
            user=request.user,
        )

    def get_exclude(self, request, obj=None):
        exclude = list(super().get_exclude(request, obj) or [])
        if not request.user.is_superuser and "owner" not in exclude:
            exclude.append("owner")
        return exclude

    def save_model(self, request, obj, form, change):
        if (
            not request.user.is_superuser
            and hasattr(obj, "owner_id")
            and not obj.owner_id
        ):
            obj.owner = request.user
        super().save_model(request, obj, form, change)


class StudentEnrollmentInline(admin.TabularInline):
    model = Enrollment
    extra = 0
    can_delete = False
    show_change_link = True
    fields = (
        "enrollment_date",
        "status",
        "course_name",
        "session_name",
        "location_name",
        "fee_amount",
        "discount_amount",
        "balance_due",
    )
    readonly_fields = fields
    ordering = ("-enrollment_date",)

    @admin.display(description="Course")
    def course_name(self, obj):
        return obj.session.course.name

    @admin.display(description="Session")
    def session_name(self, obj):
        return obj.session.session_name or str(obj.session)

    @admin.display(description="Location")
    def location_name(self, obj):
        return obj.session.location.name

    def has_add_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related(
            "session",
            "session__course",
            "session__location",
        )


class LocationCourseSessionInline(admin.TabularInline):
    model = CourseSession
    extra = 0
    show_change_link = True
    can_delete = False
    fields = (
        "session_name",
        "course",
        "teacher",
        "start_date",
        "end_date",
        "status",
    )
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class TeacherDisbursementInline(admin.TabularInline):
    model = Disbursement
    extra = 0
    can_delete = False
    show_change_link = True
    fields = (
        "disbursement_date",
        "enrollment",
        "location",
        "teacher_amount",
        "status",
    )
    readonly_fields = fields
    ordering = ("-disbursement_date",)

    def has_add_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("enrollment", "location")


class DisbursementYearFilter(admin.SimpleListFilter):
    title = "year"
    parameter_name = "year"

    def lookups(self, request, model_admin):
        years = (
            model_admin.get_queryset(request)
            .dates("disbursement_date", "year", order="DESC")
        )
        return [(str(d.year), str(d.year)) for d in years]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(disbursement_date__year=self.value())
        return queryset


class DisbursementMonthFilter(admin.SimpleListFilter):
    title = "month"
    parameter_name = "month"

    def lookups(self, request, model_admin):
        return [
            ("1", "Jan"),
            ("2", "Feb"),
            ("3", "Mar"),
            ("4", "Apr"),
            ("5", "May"),
            ("6", "Jun"),
            ("7", "Jul"),
            ("8", "Aug"),
            ("9", "Sep"),
            ("10", "Oct"),
            ("11", "Nov"),
            ("12", "Dec"),
        ]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(disbursement_date__month=self.value())
        return queryset


class DisbursementQuarterFilter(admin.SimpleListFilter):
    title = "quarter"
    parameter_name = "quarter"

    def lookups(self, request, model_admin):
        return [("1", "Q1"), ("2", "Q2"), ("3", "Q3"), ("4", "Q4")]

    def queryset(self, request, queryset):
        value = self.value()
        if not value:
            return queryset
        quarter_map = {
            "1": (1, 2, 3),
            "2": (4, 5, 6),
            "3": (7, 8, 9),
            "4": (10, 11, 12),
        }
        months = quarter_map.get(value)
        if not months:
            return queryset
        return queryset.filter(disbursement_date__month__in=months)


class ProspectConvertedFilter(admin.SimpleListFilter):
    title = "conversion status"
    parameter_name = "converted"

    def lookups(self, request, model_admin):
        return (
            ("yes", "Converted"),
            ("no", "Not Converted"),
        )

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(student_record__isnull=False)
        if self.value() == "no":
            return queryset.filter(student_record__isnull=True)
        return queryset


@admin.register(Prospect)
class ProspectAdmin(OwnerScopedAdminMixin, admin.ModelAdmin):
    form = ProspectForm
    list_display = (
        "first_name",
        "last_name",
        "phone",
        "email",
        "source",
        "status",
        "teacher",
        "owner",
        "is_converted",
        "created_at",
    )
    list_filter = (
        ProspectConvertedFilter,
        "status",
        "teacher",
        "interest_level",
        "preferred_contact_method",
        "created_at",
    )
    search_fields = (
        "contact__first_name",
        "contact__last_name",
        "contact__email",
        "contact__phone_number",
    )
    autocomplete_fields = ("contact", "teacher")
    ordering = ("-created_at",)
    readonly_fields = ("convert_to_student_button", "created_at", "updated_at")
    fields = (
        "contact_first_name",
        "contact_last_name",
        "contact_email",
        "contact_phone_number",
        "source",
        "status",
        "teacher",
        "interest_level",
        "preferred_contact_method",
        "notes",
        "owner",
        "convert_to_student_button",
        "created_at",
        "updated_at",
    )
    date_hierarchy = "created_at"
    actions = ("convert_selected_to_students",)
    change_form_template = "admin/core/prospect/change_form.html"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<path:object_id>/convert-to-student/",
                self.admin_site.admin_view(self.convert_to_student_view),
                name="core_prospect_convert_to_student",
            ),
        ]
        return custom_urls + urls

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = extra_context or {}
        try:
            prospect = self.get_object(request, object_id)
        except (ValueError, TypeError):
            prospect = None
        if prospect is not None:
            extra_context["convert_to_student_url"] = reverse(
                "admin:core_prospect_convert_to_student",
                args=[prospect.pk],
            )
        return super().change_view(request, object_id, form_url, extra_context=extra_context)

    @admin.display(description="Converted", boolean=True)
    def is_converted(self, obj):
        return hasattr(obj, "student_record")

    @admin.display(description="Convert")
    def convert_link(self, obj):
        if hasattr(obj, "student_record"):
            return "Already Student"
        url = reverse("admin:core_prospect_convert_to_student", args=[obj.pk])
        return format_html('<a class="button" href="{}">Convert</a>', url)

    @admin.display(description="Convert To Student")
    def convert_to_student_button(self, obj):
        if not obj or not obj.pk:
            return "Save this prospect first to enable conversion."
        if hasattr(obj, "student_record"):
            student_url = reverse("admin:core_student_change", args=[obj.student_record.pk])
            return format_html('Already converted. <a href="{}">Open Student</a>', student_url)
        url = reverse("admin:core_prospect_convert_to_student", args=[obj.pk])
        return format_html('<a class="button" href="{}">Convert to Student</a>', url)

    def convert_to_student_view(self, request: HttpRequest, object_id: str):
        prospect = self.get_object(request, object_id)
        if prospect is None:
            raise Http404("Prospect not found.")

        student_admin = self.admin_site._registry.get(Student)
        if not self.has_change_permission(request, prospect):
            self.message_user(
                request,
                "You do not have permission to convert this prospect.",
                level=messages.ERROR,
            )
            return HttpResponseRedirect(
                reverse("admin:core_prospect_change", args=[prospect.pk])
            )
        if not student_admin or not student_admin.has_add_permission(request):
            self.message_user(
                request,
                "You do not have permission to create students.",
                level=messages.ERROR,
            )
            return HttpResponseRedirect(
                reverse("admin:core_prospect_change", args=[prospect.pk])
            )

        try:
            student, created = prospect.convert_to_student()
        except ValidationError as exc:
            self.message_user(
                request,
                " ".join(exc.messages),
                level=messages.ERROR,
            )
            return HttpResponseRedirect(
                reverse("admin:core_prospect_change", args=[prospect.pk])
            )
        if created:
            self.message_user(
                request,
                f"{prospect} was converted to Student successfully.",
                level=messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                f"{prospect} is already linked to an existing Student.",
                level=messages.WARNING,
            )
        return HttpResponseRedirect(reverse("admin:core_student_change", args=[student.pk]))

    @admin.action(description="Convert selected prospects to students")
    def convert_selected_to_students(self, request: HttpRequest, queryset):
        student_admin = self.admin_site._registry.get(Student)
        if not student_admin or not student_admin.has_add_permission(request):
            self.message_user(
                request,
                "You do not have permission to create students.",
                level=messages.ERROR,
            )
            return

        created_count = 0
        existing_count = 0
        duplicate_count = 0
        failed_count = 0

        for prospect in queryset:
            try:
                _, created = prospect.convert_to_student()
                if created:
                    created_count += 1
                else:
                    existing_count += 1
            except ValidationError:
                duplicate_count += 1
            except Exception:
                logger.exception(
                    "Failed to convert prospect %s to student via admin action.",
                    prospect.pk,
                )
                failed_count += 1

        if created_count:
            self.message_user(
                request,
                f"Successfully converted {created_count} prospect(s) to students.",
                level=messages.SUCCESS,
            )
        if existing_count:
            self.message_user(
                request,
                f"{existing_count} prospect(s) already had student records.",
                level=messages.WARNING,
            )
        if duplicate_count:
            self.message_user(
                request,
                (
                    f"{duplicate_count} prospect(s) were skipped because they look like "
                    "duplicate students."
                ),
                level=messages.WARNING,
            )
        if failed_count:
            self.message_user(
                request,
                f"Failed to convert {failed_count} prospect(s). Check logs for details.",
                level=messages.ERROR,
            )


@admin.register(TeacherSpecialization)
class TeacherSpecializationAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at")
    search_fields = ("name",)
    list_filter = ("name", "created_at")
    ordering = ("name",)
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "created_at"


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "code",
        "city",
        "country",
        "is_active",
        "session_count",
        "enrollment_count",
        "student_count",
        "created_at",
    )
    search_fields = ("name", "code", "city", "province_state", "country")
    list_filter = ("is_active", "city", "country", "created_at")
    ordering = ("name",)
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "created_at"
    inlines = (LocationCourseSessionInline,)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            _session_count=Count("course_sessions", distinct=True),
            _enrollment_count=Count("course_sessions__enrollments", distinct=True),
            _student_count=Count("course_sessions__enrollments__student", distinct=True),
        )

    @admin.display(description="Sessions")
    def session_count(self, obj):
        return getattr(obj, "_session_count", 0)

    @admin.display(description="Enrollments")
    def enrollment_count(self, obj):
        return getattr(obj, "_enrollment_count", 0)

    @admin.display(description="Students")
    def student_count(self, obj):
        return getattr(obj, "_student_count", 0)


@admin.register(Teacher)
class TeacherAdmin(OwnerScopedAdminMixin, admin.ModelAdmin):
    list_display = (
        "first_name",
        "last_name",
        "email",
        "phone",
        "status",
        "students_taught_count",
        "assigned_students_count",
        "assigned_prospects_count",
        "current_month_disbursement_total",
        "current_year_disbursement_total",
        "get_specializations",
        "created_at",
    )
    search_fields = ("first_name", "last_name", "email", "phone", "specializations__name")
    list_filter = ("status", "specializations", "created_at")
    ordering = ("first_name", "last_name")
    readonly_fields = (
        "students_taught_count",
        "assigned_students_count",
        "assigned_prospects_count",
        "disbursable_amount",
        "today_disbursement_total",
        "current_month_disbursement_total",
        "current_quarter_disbursement_total",
        "current_year_disbursement_total",
        "session_disbursement_totals",
        "created_at",
        "updated_at",
    )
    date_hierarchy = "created_at"
    autocomplete_fields = ("user",)
    filter_horizontal = ("specializations",)
    inlines = (TeacherDisbursementInline,)

    def _current_quarter_months(self):
        month = timezone.localdate().month
        quarter = (month - 1) // 3
        start = quarter * 3 + 1
        return (start, start + 1, start + 2)

    def _teacher_disbursement_sum(self, obj, **filters):
        return (
            obj.disbursements.exclude(status=DisbursementStatus.CANCELLED)
            .filter(**filters)
            .aggregate(total=Sum("teacher_amount"))
            .get("total")
            or Decimal("0.00")
        )

    @admin.display(description="Specializations")
    def get_specializations(self, obj):
        return obj.specializations_display or "-"

    @admin.display(description="Students Taught")
    def students_taught_count(self, obj):
        return (
            Enrollment.objects.filter(session__teacher=obj)
            .values("student")
            .distinct()
            .count()
        )

    @admin.display(description="Assigned Students")
    def assigned_students_count(self, obj):
        return obj.students.count()

    @admin.display(description="Assigned Prospects")
    def assigned_prospects_count(self, obj):
        return obj.prospects.count()

    @admin.display(description="Disbursable (Pending)")
    def disbursable_amount(self, obj):
        return (
            obj.disbursements.filter(status=DisbursementStatus.PENDING).aggregate(
                total=Sum("teacher_amount")
            )["total"]
            or Decimal("0.00")
        )

    @admin.display(description="Today Total")
    def today_disbursement_total(self, obj):
        return self._teacher_disbursement_sum(obj, disbursement_date=timezone.localdate())

    @admin.display(description="Month Total")
    def current_month_disbursement_total(self, obj):
        today = timezone.localdate()
        return self._teacher_disbursement_sum(
            obj,
            disbursement_date__year=today.year,
            disbursement_date__month=today.month,
        )

    @admin.display(description="Quarter Total")
    def current_quarter_disbursement_total(self, obj):
        today = timezone.localdate()
        return self._teacher_disbursement_sum(
            obj,
            disbursement_date__year=today.year,
            disbursement_date__month__in=self._current_quarter_months(),
        )

    @admin.display(description="Year Total")
    def current_year_disbursement_total(self, obj):
        today = timezone.localdate()
        return self._teacher_disbursement_sum(
            obj,
            disbursement_date__year=today.year,
        )

    @admin.display(description="Totals By Session")
    def session_disbursement_totals(self, obj):
        rows = (
            obj.disbursements.exclude(status=DisbursementStatus.CANCELLED)
            .values(
                "enrollment__session__id",
                "enrollment__session__session_name",
                "enrollment__session__course__name",
            )
            .annotate(total=Sum("teacher_amount"))
            .order_by("-total")
        )
        if not rows:
            return "-"
        return format_html(
            "<br>".join(
                f"{r['enrollment__session__course__name']} / "
                f"{r['enrollment__session__session_name'] or 'Unnamed Session'}: "
                f"{r['total']}"
                for r in rows
            )
        )


@admin.register(Student)
class StudentAdmin(OwnerScopedAdminMixin, admin.ModelAdmin):
    form = StudentForm
    list_display = (
        "prospect",
        "teacher",
        "enrollment_status",
        "city",
        "province_state",
        "country",
        "created_at",
    )
    search_fields = (
        "prospect__contact__first_name",
        "prospect__contact__last_name",
        "prospect__contact__email",
        "prospect__contact__phone_number",
    )
    list_filter = ("enrollment_status", "teacher", "province_state", "country", "created_at")
    ordering = ("prospect__contact__first_name", "prospect__contact__last_name")
    list_select_related = ("prospect", "teacher")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "created_at"
    autocomplete_fields = ("prospect", "teacher")
    inlines = (StudentEnrollmentInline,)


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "format",
        "duration_weeks",
        "standard_fee",
        "status",
        "created_at",
    )
    search_fields = ("name", "description")
    list_filter = ("format", "status", "created_at")
    ordering = ("name",)
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "created_at"


@admin.register(CourseSession)
class CourseSessionAdmin(OwnerScopedAdminMixin, admin.ModelAdmin):
    list_display = (
        "session_name",
        "course",
        "teacher",
        "location",
        "start_date",
        "end_date",
        "status",
        "delivery_mode",
    )
    search_fields = (
        "session_name",
        "course__name",
        "teacher__first_name",
        "teacher__last_name",
    )
    list_filter = ("status", "delivery_mode", "course", "teacher", "location", "start_date")
    ordering = ("-start_date",)
    list_select_related = ("course", "teacher", "location")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "start_date"
    autocomplete_fields = ("course", "teacher", "location")


@admin.register(Inquiry)
class InquiryAdmin(OwnerScopedAdminMixin, admin.ModelAdmin):
    list_display = ("prospect", "student", "channel", "status", "inquiry_date", "assigned_to")
    search_fields = (
        "subject",
        "message",
        "prospect__contact__first_name",
        "prospect__contact__last_name",
        "prospect__contact__email",
        "student__prospect__contact__first_name",
        "student__prospect__contact__last_name",
        "student__prospect__contact__email",
    )
    list_filter = ("channel", "status", "inquiry_date", "student", "prospect")
    ordering = ("-inquiry_date",)
    list_select_related = ("prospect", "student", "assigned_to")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "inquiry_date"
    autocomplete_fields = ("prospect", "student", "assigned_to")


@admin.register(Enrollment)
class EnrollmentAdmin(OwnerScopedAdminMixin, admin.ModelAdmin):
    list_display = (
        "student",
        "session",
        "status",
        "fee_amount",
        "discount_amount",
        "balance_due",
        "has_invoice",
        "invoice_link",
        "has_disbursement",
        "disbursement_link",
        "enrollment_date",
    )
    search_fields = (
        "student__prospect__contact__first_name",
        "student__prospect__contact__last_name",
        "student__prospect__contact__email",
        "session__course__name",
    )
    list_filter = ("status", "enrollment_date", "session__course")
    ordering = ("-enrollment_date",)
    list_select_related = (
        "student",
        "student__prospect",
        "session",
        "session__course",
        "session__location",
    )
    readonly_fields = (
        "balance_due",
        "generate_invoice_button",
        "generate_disbursement_button",
        "created_at",
        "updated_at",
    )
    date_hierarchy = "enrollment_date"
    autocomplete_fields = ("student", "session")
    actions = (
        "generate_invoices_for_selected_enrollments",
        "generate_disbursements_for_selected_enrollments",
    )
    change_form_template = "admin/core/enrollment/change_form.html"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<path:object_id>/generate-invoice/",
                self.admin_site.admin_view(self.generate_invoice_view),
                name="core_enrollment_generate_invoice",
            ),
            path(
                "<path:object_id>/generate-disbursement/",
                self.admin_site.admin_view(self.generate_disbursement_view),
                name="core_enrollment_generate_disbursement",
            ),
        ]
        return custom_urls + urls

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = extra_context or {}
        try:
            enrollment = self.get_object(request, object_id)
        except (ValueError, TypeError):
            enrollment = None
        if enrollment is not None:
            extra_context["generate_invoice_url"] = reverse(
                "admin:core_enrollment_generate_invoice",
                args=[enrollment.pk],
            )
            extra_context["generate_disbursement_url"] = reverse(
                "admin:core_enrollment_generate_disbursement",
                args=[enrollment.pk],
            )
        return super().change_view(request, object_id, form_url, extra_context=extra_context)

    @admin.display(description="Has Invoice", boolean=True)
    def has_invoice(self, obj):
        return hasattr(obj, "invoice")

    @admin.display(description="Invoice")
    def invoice_link(self, obj):
        if not hasattr(obj, "invoice"):
            return "-"
        url = reverse("admin:core_invoice_change", args=[obj.invoice.pk])
        return format_html('<a href="{}">{}</a>', url, obj.invoice.invoice_number)

    @admin.display(description="Has Disbursement", boolean=True)
    def has_disbursement(self, obj):
        return hasattr(obj, "disbursement")

    @admin.display(description="Disbursement")
    def disbursement_link(self, obj):
        if not hasattr(obj, "disbursement"):
            return "-"
        url = reverse("admin:core_disbursement_change", args=[obj.disbursement.pk])
        return format_html('<a href="{}">#{}</a>', url, obj.disbursement.pk)

    @admin.display(description="Generate Invoice")
    def generate_invoice_button(self, obj):
        if not obj or not obj.pk:
            return "Save this enrollment first to enable invoice generation."
        if hasattr(obj, "invoice"):
            invoice_url = reverse("admin:core_invoice_change", args=[obj.invoice.pk])
            return format_html('Invoice exists. <a href="{}">Open Invoice</a>', invoice_url)
        url = reverse("admin:core_enrollment_generate_invoice", args=[obj.pk])
        return format_html('<a class="button" href="{}">Generate Invoice</a>', url)

    @admin.display(description="Generate Disbursement")
    def generate_disbursement_button(self, obj):
        if not obj or not obj.pk:
            return "Save this enrollment first to enable disbursement generation."
        if hasattr(obj, "disbursement"):
            disb_url = reverse("admin:core_disbursement_change", args=[obj.disbursement.pk])
            return format_html("Disbursement exists. <a href=\"{}\">Open Disbursement</a>", disb_url)
        url = reverse("admin:core_enrollment_generate_disbursement", args=[obj.pk])
        return format_html('<a class="button" href="{}">Generate Disbursement</a>', url)

    def generate_invoice_view(self, request: HttpRequest, object_id: str):
        enrollment = self.get_object(request, object_id)
        if enrollment is None:
            raise Http404("Enrollment not found.")

        invoice_admin = self.admin_site._registry.get(Invoice)
        if not self.has_change_permission(request, enrollment):
            self.message_user(
                request,
                "You do not have permission to generate an invoice for this enrollment.",
                level=messages.ERROR,
            )
            return HttpResponseRedirect(
                reverse("admin:core_enrollment_change", args=[enrollment.pk])
            )
        if not invoice_admin or not invoice_admin.has_add_permission(request):
            self.message_user(
                request,
                "You do not have permission to create invoices.",
                level=messages.ERROR,
            )
            return HttpResponseRedirect(
                reverse("admin:core_enrollment_change", args=[enrollment.pk])
            )

        invoice, created = generate_invoice_for_enrollment(enrollment)
        if created:
            self.message_user(
                request,
                f"Invoice {invoice.invoice_number} was generated successfully.",
                level=messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                f"Invoice {invoice.invoice_number} already exists for this enrollment.",
                level=messages.WARNING,
            )
        return HttpResponseRedirect(reverse("admin:core_invoice_change", args=[invoice.pk]))

    def generate_disbursement_view(self, request: HttpRequest, object_id: str):
        enrollment = self.get_object(request, object_id)
        if enrollment is None:
            raise Http404("Enrollment not found.")

        disb_admin = self.admin_site._registry.get(Disbursement)
        if not self.has_change_permission(request, enrollment):
            self.message_user(
                request,
                "You do not have permission to generate a disbursement for this enrollment.",
                level=messages.ERROR,
            )
            return HttpResponseRedirect(
                reverse("admin:core_enrollment_change", args=[enrollment.pk])
            )
        if not disb_admin or not disb_admin.has_add_permission(request):
            self.message_user(
                request,
                "You do not have permission to create disbursements.",
                level=messages.ERROR,
            )
            return HttpResponseRedirect(
                reverse("admin:core_enrollment_change", args=[enrollment.pk])
            )

        disbursement, created = generate_disbursement_for_enrollment(enrollment)
        if created:
            self.message_user(
                request,
                f"Disbursement #{disbursement.pk} was generated successfully.",
                level=messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                f"Disbursement #{disbursement.pk} already exists for this enrollment.",
                level=messages.WARNING,
            )
        return HttpResponseRedirect(
            reverse("admin:core_disbursement_change", args=[disbursement.pk])
        )

    @admin.action(description="Generate invoices for selected enrollments")
    def generate_invoices_for_selected_enrollments(self, request: HttpRequest, queryset):
        invoice_admin = self.admin_site._registry.get(Invoice)
        if not invoice_admin or not invoice_admin.has_add_permission(request):
            self.message_user(
                request,
                "You do not have permission to create invoices.",
                level=messages.ERROR,
            )
            return

        created_count = 0
        existing_count = 0
        failed_count = 0

        for enrollment in queryset:
            try:
                _, created = generate_invoice_for_enrollment(enrollment)
                if created:
                    created_count += 1
                else:
                    existing_count += 1
            except Exception:
                logger.exception(
                    "Failed to generate invoice for enrollment %s via admin action.",
                    enrollment.pk,
                )
                failed_count += 1

        if created_count:
            self.message_user(
                request,
                f"Generated {created_count} invoice(s) successfully.",
                level=messages.SUCCESS,
            )
        if existing_count:
            self.message_user(
                request,
                f"{existing_count} enrollment(s) already had invoices.",
                level=messages.WARNING,
            )
        if failed_count:
            self.message_user(
                request,
                f"Failed to generate invoices for {failed_count} enrollment(s). Check logs.",
                level=messages.ERROR,
            )

    @admin.action(description="Generate disbursements for selected enrollments")
    def generate_disbursements_for_selected_enrollments(self, request: HttpRequest, queryset):
        disb_admin = self.admin_site._registry.get(Disbursement)
        if not disb_admin or not disb_admin.has_add_permission(request):
            self.message_user(
                request,
                "You do not have permission to create disbursements.",
                level=messages.ERROR,
            )
            return

        created_count = 0
        existing_count = 0
        failed_count = 0

        for enrollment in queryset:
            try:
                _, created = generate_disbursement_for_enrollment(enrollment)
                if created:
                    created_count += 1
                else:
                    existing_count += 1
            except Exception:
                logger.exception(
                    "Failed to generate disbursement for enrollment %s via admin action.",
                    enrollment.pk,
                )
                failed_count += 1

        if created_count:
            self.message_user(
                request,
                f"Generated {created_count} disbursement(s) successfully.",
                level=messages.SUCCESS,
            )
        if existing_count:
            self.message_user(
                request,
                f"{existing_count} enrollment(s) already had disbursements.",
                level=messages.WARNING,
            )
        if failed_count:
            self.message_user(
                request,
                f"Failed to generate disbursements for {failed_count} enrollment(s). Check logs.",
                level=messages.ERROR,
            )


@admin.register(Disbursement)
class DisbursementAdmin(OwnerScopedAdminMixin, admin.ModelAdmin):
    list_display = (
        "id",
        "disbursement_date",
        "status",
        "teacher",
        "location",
        "session_display",
        "teacher_amount",
        "location_amount",
        "ico_amount",
        "balance_due_snapshot",
    )
    search_fields = (
        "enrollment__student__prospect__contact__first_name",
        "enrollment__student__prospect__contact__last_name",
        "enrollment__student__prospect__contact__email",
        "teacher__first_name",
        "teacher__last_name",
        "location__name",
        "enrollment__session__session_name",
        "enrollment__session__course__name",
    )
    list_filter = (
        "status",
        "teacher",
        "location",
        "enrollment__session",
        DisbursementYearFilter,
        DisbursementMonthFilter,
        DisbursementQuarterFilter,
        "disbursement_date",
    )
    ordering = ("-disbursement_date", "-id")
    date_hierarchy = "disbursement_date"
    list_select_related = (
        "teacher",
        "location",
        "enrollment",
        "enrollment__session",
        "enrollment__session__course",
        "enrollment__student",
        "enrollment__student__prospect",
    )
    autocomplete_fields = ("enrollment", "teacher", "location")
    change_list_template = "admin/core/disbursement/change_list.html"
    readonly_fields = (
        "balance_due_snapshot",
        "teacher_amount",
        "location_amount",
        "ico_amount",
        "created_at",
        "updated_at",
    )

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "report/",
                self.admin_site.admin_view(self.report_view),
                name="core_disbursement_report",
            ),
        ]
        return custom_urls + urls

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["disbursement_report_url"] = reverse("admin:core_disbursement_report")
        return super().changelist_view(request, extra_context=extra_context)

    def report_view(self, request: HttpRequest):
        form = DisbursementDateRangeReportForm(request.GET or None)
        context = dict(
            self.admin_site.each_context(request),
            title="Disbursement Date-Range Report",
            opts=self.model._meta,
            form=form,
            report=None,
        )
        if form.is_valid():
            start_date = form.cleaned_data["start_date"]
            end_date = form.cleaned_data["end_date"]
            report_by = form.cleaned_data["report_by"]
            teacher = form.cleaned_data.get("teacher")
            location = form.cleaned_data.get("location")

            total_amount = get_disbursed_total_for_period(
                report_by=report_by,
                start_date=start_date,
                end_date=end_date,
                teacher=teacher,
                location=location,
            )

            context["report"] = {
                "start_date": start_date,
                "end_date": end_date,
                "report_by": report_by,
                "teacher": teacher,
                "location": location,
                "total_amount": total_amount,
            }

        return TemplateResponse(
            request,
            "admin/core/disbursement/report.html",
            context,
        )

    @admin.display(description="Session")
    def session_display(self, obj):
        session = obj.enrollment.session
        return session.session_name or str(session)


@admin.register(InterviewForm)
class InterviewFormAdmin(OwnerScopedAdminMixin, admin.ModelAdmin):
    list_display = ("student", "teacher", "session", "status", "submitted_at")
    search_fields = (
        "student__prospect__contact__first_name",
        "student__prospect__contact__last_name",
        "teacher__first_name",
        "teacher__last_name",
    )
    list_filter = ("status", "submitted_at", "teacher")
    ordering = ("-submitted_at",)
    list_select_related = ("student", "student__prospect", "teacher", "session")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "submitted_at"
    autocomplete_fields = ("student", "teacher", "session")


@admin.register(Invoice)
class InvoiceAdmin(OwnerScopedAdminMixin, admin.ModelAdmin):
    list_display = (
        "invoice_number",
        "enrollment",
        "issue_date",
        "due_date",
        "total_amount",
        "status",
    )
    search_fields = (
        "invoice_number",
        "enrollment__student__prospect__contact__first_name",
        "enrollment__student__prospect__contact__last_name",
    )
    list_filter = ("status", "issue_date", "due_date")
    ordering = ("-issue_date", "-id")
    list_select_related = ("enrollment", "enrollment__student", "enrollment__student__prospect")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "issue_date"
    autocomplete_fields = ("enrollment",)


@admin.register(Payment)
class PaymentAdmin(OwnerScopedAdminMixin, admin.ModelAdmin):
    list_display = (
        "invoice",
        "payment_date",
        "amount_paid",
        "payment_method",
        "confirmation_status",
    )
    search_fields = ("invoice__invoice_number", "reference_number")
    list_filter = ("payment_method", "confirmation_status", "payment_date")
    ordering = ("-payment_date",)
    list_select_related = ("invoice", "invoice__enrollment")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "payment_date"
    autocomplete_fields = ("invoice",)


@admin.register(Communication)
class CommunicationAdmin(OwnerScopedAdminMixin, admin.ModelAdmin):
    list_display = (
        "recipient_type",
        "recipient_display",
        "channel",
        "communication_type",
        "delivery_status",
        "sent_at",
        "created_at",
    )
    search_fields = (
        "subject",
        "body",
        "provider_status",
        "prospect__contact__first_name",
        "prospect__contact__last_name",
        "prospect__contact__email",
        "student__prospect__contact__first_name",
        "student__prospect__contact__last_name",
        "student__prospect__contact__email",
    )
    list_filter = (
        "recipient_type",
        "channel",
        "communication_type",
        "delivery_status",
        "sent_at",
        "created_at",
    )
    ordering = ("-created_at",)
    list_select_related = ("prospect", "student", "student__prospect", "enrollment")
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="Recipient")
    def recipient_display(self, obj):
        if obj.prospect_id:
            return obj.prospect
        if obj.student_id:
            return obj.student
        return "-"


@admin.register(Contact)
class ContactAdmin(OwnerScopedAdminMixin, admin.ModelAdmin):
    list_display = (
        "first_name",
        "last_name",
        "email",
        "phone_number",
        "created_at",
    )
    list_filter = ("created_at",)
    search_fields = (
        "first_name",
        "last_name",
        "email",
        "phone_number",
    )
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "created_at"


@admin.register(Meditator)
class MeditatorAdmin(OwnerScopedAdminMixin, admin.ModelAdmin):
    list_display = (
        "student",
        "transitioned_at",
        "transition_trigger",
        "intro_completed_on",
        "day20_completed_on",
    )
    search_fields = (
        "student__prospect__contact__first_name",
        "student__prospect__contact__last_name",
        "student__prospect__contact__email",
    )
    list_filter = ("transition_trigger", "transitioned_at")
    ordering = ("-transitioned_at", "-id")
    list_select_related = ("student", "student__prospect", "student__prospect__contact")
    readonly_fields = (
        "student",
        "transitioned_at",
        "transition_trigger",
        "intro_completed_on",
        "day20_completed_on",
        "metadata",
        "created_at",
        "updated_at",
    )
    autocomplete_fields = ("student",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(MeditatorTransitionEvent)
class MeditatorTransitionEventAdmin(OwnerScopedAdminMixin, admin.ModelAdmin):
    list_display = (
        "student",
        "event_type",
        "transition_trigger",
        "triggered_at",
    )
    search_fields = (
        "student__prospect__contact__first_name",
        "student__prospect__contact__last_name",
        "student__prospect__contact__email",
    )
    list_filter = ("event_type", "transition_trigger", "triggered_at")
    ordering = ("-triggered_at", "-id")
    list_select_related = ("student", "meditator")
    readonly_fields = (
        "student",
        "meditator",
        "event_type",
        "triggered_at",
        "transition_trigger",
        "intro_completed_on",
        "day20_completed_on",
        "metadata",
        "created_at",
        "updated_at",
    )
    autocomplete_fields = ("student", "meditator")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser
