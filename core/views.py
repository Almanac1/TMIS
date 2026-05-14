from datetime import datetime, time
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.mail import EmailMessage
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import DecimalField, ExpressionWrapper, F, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    TemplateView,
    UpdateView,
    View,
)

from .forms import (
    StudentForm,
    DisbursementReportingFilterForm,
    CommunicationForm,
    EnrollmentForm,
    InvoicePaymentForm,
    PaymentForm,
    ProspectForm,
    ProspectFollowUpForm,
    ProspectPipelineFilterForm,
)
from .models import (
    Communication,
    CommunicationChannel,
    DeliveryStatus,
    Contact,
    Course,
    CourseSession,
    Disbursement,
    Enrollment,
    Inquiry,
    InterviewForm,
    Invoice,
    Location,
    Meditator,
    Payment,
    Prospect,
    Student,
    Teacher,
    TeacherSpecialization,
    EnrollmentStatus,
)
from .services.prospect_pipeline import (
    convert_prospect_to_student_for_pipeline,
    get_pipeline_status_breakdown,
    get_prospect_dashboard_metrics,
    get_prospect_detail_context,
    get_prospect_pipeline_queryset,
    get_user_scoped_prospect_queryset,
    log_prospect_follow_up,
)
from .services.disbursement_product_reporting import get_disbursement_reporting_data
from .services.ownership import scope_queryset_for_user
from .services.teacher_earnings import get_teacher_earnings_dashboard_data
from .services.home_dashboard import get_home_dashboard_data


@require_POST
def secure_logout_view(request):
    username = request.user.get_username() if request.user.is_authenticated else ""
    logout(request)
    if username:
        messages.success(request, f"You have been logged out, {username}.")
    return redirect("core:login")


CRUD_MODELS = [
    Prospect,
    Contact,
    Student,
    Teacher,
    TeacherSpecialization,
    Location,
    Course,
    CourseSession,
    Inquiry,
    Enrollment,
    Invoice,
    Payment,
    Communication,
    InterviewForm,
    Disbursement,
]

CRUD_MODEL_UI_OPTIONS = {
    Student: {
        "allow_delete": False,
        "allow_archive": True,
    },
}


def get_model_ui_options(model):
    return {
        "allow_delete": True,
        "allow_archive": False,
        **CRUD_MODEL_UI_OPTIONS.get(model, {}),
    }


class ProductLoginRequiredMixin(LoginRequiredMixin):
    login_url = "/login/"


class HomeView(ProductLoginRequiredMixin, TemplateView):
    template_name = "core/crud/home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["dashboard"] = get_home_dashboard_data(user=self.request.user)
        return context


class MeditatorListView(ProductLoginRequiredMixin, ListView):
    model = Meditator
    paginate_by = 25
    template_name = "core/crud/meditator/list.html"
    context_object_name = "meditators"

    def get_queryset(self):
        queryset = (
            Meditator.objects.select_related(
                "student",
                "student__prospect",
                "student__prospect__contact",
                "student__teacher",
            )
            .order_by("-transitioned_at", "-pk")
        )
        queryset = scope_queryset_for_user(
            queryset=queryset,
            model=Meditator,
            user=self.request.user,
        )
        query = (self.request.GET.get("q") or "").strip()
        if query:
            filters = (
                Q(student__prospect__contact__first_name__icontains=query)
                | Q(student__prospect__contact__last_name__icontains=query)
                | Q(student__prospect__contact__email__icontains=query)
                | Q(student__prospect__contact__phone_number__icontains=query)
                | Q(student__teacher__first_name__icontains=query)
                | Q(student__teacher__last_name__icontains=query)
                | Q(student__enrollment_status__icontains=query)
            )
            if query.isdigit():
                filters |= Q(pk=int(query)) | Q(student__pk=int(query))
            queryset = queryset.filter(filters)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["search_query"] = (self.request.GET.get("q") or "").strip()
        params = self.request.GET.copy()
        params.pop("page", None)
        context["querystring_without_page"] = params.urlencode()
        context["show_search"] = True
        context["search_label"] = "Search Meditators"
        context["search_placeholder"] = "Search by student, governor, status, email, phone, or ID"
        context["search_clear_url"] = reverse_lazy("core:meditator-list")
        return context


class EmailLoginView(View):
    template_name = "core/login.html"

    def get(self, request):
        if request.user.is_authenticated:
            return redirect("core:home")
        return render(request, self.template_name, {"next": request.GET.get("next", "")})

    def post(self, request):
        if request.user.is_authenticated:
            return redirect("core:home")

        email = (request.POST.get("email") or "").strip()
        password = request.POST.get("password") or ""
        next_url = (request.POST.get("next") or "").strip()

        user = authenticate(request, email=email, password=password)
        if user is None:
            messages.error(request, "Invalid email or password.")
            return render(
                request,
                self.template_name,
                {"email": email, "next": next_url},
                status=401,
            )

        login(request, user)
        if next_url and url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return redirect(next_url)
        return redirect("core:home")


class ProspectDashboardView(ProductLoginRequiredMixin, TemplateView):
    template_name = "core/prospect_pipeline/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        scoped_prospects = get_user_scoped_prospect_queryset(self.request.user)
        context["metrics"] = get_prospect_dashboard_metrics(user=self.request.user)
        context["status_breakdown"] = get_pipeline_status_breakdown(user=self.request.user)
        context["recent_prospects"] = scoped_prospects.order_by("-created_at")[:10]
        return context


class ProspectPipelineListView(ProductLoginRequiredMixin, ListView):
    model = Prospect
    paginate_by = 20
    template_name = "core/prospect_pipeline/pipeline_list.html"

    def get_filter_form(self):
        return ProspectPipelineFilterForm(self.request.GET or None)

    def get_template_names(self):
        if self.request.headers.get("HX-Request"):
            return ["core/prospect_pipeline/partials/pipeline_table.html"]
        return [self.template_name]

    def get_queryset(self):
        form = self.get_filter_form()
        self.filter_form = form
        if not form.is_valid():
            return Prospect.objects.none()
        return get_prospect_pipeline_queryset(user=self.request.user, **form.cleaned_data)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter_form"] = getattr(self, "filter_form", self.get_filter_form())
        context["pipeline_total"] = self.object_list.count()
        return context


class ProspectPipelineDetailView(ProductLoginRequiredMixin, DetailView):
    model = Prospect
    template_name = "core/prospect_pipeline/prospect_detail.html"
    context_object_name = "prospect"

    def get_queryset(self):
        return get_user_scoped_prospect_queryset(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(get_prospect_detail_context(self.object))
        if "follow_up_form" not in context:
            context["follow_up_form"] = ProspectFollowUpForm()
        return context


class ProspectConvertToStudentView(ProductLoginRequiredMixin, View):
    def post(self, request, pk):
        prospect = get_object_or_404(get_user_scoped_prospect_queryset(request.user), pk=pk)
        try:
            student, created = convert_prospect_to_student_for_pipeline(prospect)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect("core:prospect-pipeline-detail", pk=prospect.pk)
        if created:
            messages.success(request, f"{prospect} was converted to Student successfully.")
        else:
            messages.info(request, f"{prospect} is already linked to Student #{student.pk}.")
        return redirect("core:prospect-pipeline-detail", pk=prospect.pk)


class ProspectFollowUpCreateView(ProductLoginRequiredMixin, View):
    def post(self, request, pk):
        prospect = get_object_or_404(get_user_scoped_prospect_queryset(request.user), pk=pk)
        form = ProspectFollowUpForm(request.POST)

        if form.is_valid():
            log_prospect_follow_up(prospect=prospect, **form.cleaned_data)
            messages.success(request, "Follow-up communication logged.")
            form = ProspectFollowUpForm()
        elif not request.headers.get("HX-Request"):
            messages.error(request, "Please correct the follow-up form errors.")

        if request.headers.get("HX-Request"):
            detail_context = get_prospect_detail_context(prospect)
            return render(
                request,
                "core/prospect_pipeline/partials/communication_log.html",
                {
                    "prospect": prospect,
                    "communications": detail_context["communications"],
                    "follow_up_form": form,
                },
            )

        return redirect("core:prospect-pipeline-detail", pk=prospect.pk)


class ContactConvertToProspectView(ProductLoginRequiredMixin, View):
    template_name = "core/crud/prospect/convert_from_contact_form.html"

    def _get_contact(self, request, pk):
        return get_object_or_404(
            scope_queryset_for_user(
                queryset=Contact.objects.all(),
                model=Contact,
                user=request.user,
            ),
            pk=pk,
        )

    def _build_form(self, request, *, contact, data=None):
        owner = request.user if request.user.is_authenticated and not request.user.is_superuser else None
        instance = Prospect(contact=contact, owner=owner)
        return ProspectForm(data=data, instance=instance)

    def get(self, request, pk):
        contact = self._get_contact(request, pk)
        if contact.has_converted_prospect:
            existing = getattr(contact, "prospect", None) or contact.converted_prospect
            if existing and (
                not contact.converted_to_prospect
                or contact.converted_prospect_id != existing.pk
                or contact.converted_at is None
            ):
                contact.converted_to_prospect = True
                contact.converted_prospect = existing
                contact.converted_at = contact.converted_at or timezone.now()
                contact.save(
                    update_fields=[
                        "converted_to_prospect",
                        "converted_prospect",
                        "converted_at",
                        "updated_at",
                    ]
                )
            messages.info(
                request,
                f"{contact} is already linked to Prospect #{existing.pk}.",
            )
            return redirect("core:prospect-detail", pk=existing.pk)
        form = self._build_form(request, contact=contact)
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "contact": contact,
            },
        )

    def post(self, request, pk):
        contact = self._get_contact(request, pk)
        if contact.has_converted_prospect:
            messages.info(
                request,
                f"{contact} is already linked to Prospect #{contact.prospect.pk}.",
            )
            return redirect("core:prospect-detail", pk=contact.prospect.pk)

        form = self._build_form(request, contact=contact, data=request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    if not request.user.is_superuser:
                        form.instance.owner = request.user
                    prospect = form.save()
                    contact.converted_to_prospect = True
                    contact.converted_prospect = prospect
                    contact.converted_at = timezone.now()
                    contact.save(
                        update_fields=[
                            "converted_to_prospect",
                            "converted_prospect",
                            "converted_at",
                            "updated_at",
                        ]
                    )
            except IntegrityError:
                existing = Prospect.objects.filter(contact=contact).first()
                if existing:
                    contact.converted_to_prospect = True
                    contact.converted_prospect = existing
                    if contact.converted_at is None:
                        contact.converted_at = timezone.now()
                    contact.save(
                        update_fields=[
                            "converted_to_prospect",
                            "converted_prospect",
                            "converted_at",
                            "updated_at",
                        ]
                    )
                    messages.info(
                        request,
                        f"{contact} is already linked to Prospect #{existing.pk}.",
                    )
                    return redirect("core:prospect-detail", pk=existing.pk)
                raise
            messages.success(request, "Contact converted to Prospect successfully.")
            return redirect("core:prospect-detail", pk=prospect.pk)

        return render(
            request,
            self.template_name,
            {
                "form": form,
                "contact": contact,
            },
            status=400,
        )


class ProspectListConvertToStudentView(ProductLoginRequiredMixin, View):
    def post(self, request, pk):
        prospect = get_object_or_404(
            scope_queryset_for_user(
                queryset=Prospect.objects.all(),
                model=Prospect,
                user=request.user,
            ),
            pk=pk,
        )
        existing_student = getattr(prospect, "student_record", None) or prospect.converted_student
        if prospect.status == "converted" and existing_student is not None:
            messages.info(
                request,
                f"{prospect} is already linked to Student #{existing_student.pk}.",
            )
            return redirect("core:student-detail", pk=existing_student.pk)
        try:
            student, created = convert_prospect_to_student_for_pipeline(prospect)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            next_url = request.POST.get("next") or request.META.get("HTTP_REFERER")
            if next_url:
                return redirect(next_url)
            return redirect("core:prospect-list")

        if created:
            messages.success(request, f"{prospect} was converted to Student successfully.")
            return redirect("core:student-detail", pk=student.pk)
        else:
            messages.info(request, f"{prospect} is already linked to Student #{student.pk}.")
            return redirect("core:student-detail", pk=student.pk)


class TeacherEarningsDashboardView(ProductLoginRequiredMixin, TemplateView):
    template_name = "core/reporting/teacher_earnings_dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(get_teacher_earnings_dashboard_data())
        return context


class DisbursementReportingView(ProductLoginRequiredMixin, TemplateView):
    template_name = "core/reporting/disbursement_reporting.html"

    def get_initial(self):
        # Default to current month window for a practical first view.
        from django.utils import timezone

        today = timezone.localdate()
        month_start = today.replace(day=1)
        return {
            "start_date": month_start,
            "end_date": today,
            "report_by": "teacher",
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        data = self.request.GET.copy()
        if not data:
            data = self.get_initial()

        form = DisbursementReportingFilterForm(data or None)
        context["form"] = form
        context["report"] = None

        if form.is_valid():
            report_data = get_disbursement_reporting_data(**form.cleaned_data)
            context["report"] = {
                **report_data,
                "report_by": form.cleaned_data["report_by"],
                "start_date": form.cleaned_data["start_date"],
                "end_date": form.cleaned_data["end_date"],
                "teacher": form.cleaned_data.get("teacher"),
                "location": form.cleaned_data.get("location"),
            }
        return context


class CRUDContextMixin:
    model = None
    MODEL_UI_NAME_OVERRIDES = {
        Teacher: ("Governor", "Governors"),
        TeacherSpecialization: ("Governor Specialization", "Governor Specializations"),
    }

    def _model_slug(self):
        return self.model._meta.model_name

    def _ui_model_names(self):
        return self.MODEL_UI_NAME_OVERRIDES.get(
            self.model,
            (
                self.model._meta.verbose_name.title(),
                self.model._meta.verbose_name_plural.title(),
            ),
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        slug = self._model_slug()
        ui_options = get_model_ui_options(self.model)
        model_name, model_name_plural = self._ui_model_names()
        context.update(
            {
                "model_name": model_name,
                "model_name_plural": model_name_plural,
                "model_slug": slug,
                "list_url_name": f"core:{slug}-list",
                "detail_url_name": f"core:{slug}-detail",
                "create_url_name": f"core:{slug}-create",
                "update_url_name": f"core:{slug}-update",
                "delete_url_name": f"core:{slug}-delete",
                "archive_url_name": f"core:{slug}-archive",
                "can_delete": ui_options["allow_delete"],
                "can_archive": ui_options["allow_archive"],
            }
        )
        return context


class BaseListView(ProductLoginRequiredMixin, CRUDContextMixin, ListView):
    paginate_by = 25

    SEARCH_CONFIG = {
        Prospect: [
            "contact__first_name__icontains",
            "contact__last_name__icontains",
            "contact__email__icontains",
            "contact__phone_number__icontains",
            "status__icontains",
            "source__icontains",
        ],
        Contact: [
            "first_name__icontains",
            "last_name__icontains",
            "email__icontains",
            "phone_number__icontains",
        ],
        Student: [
            "prospect__contact__first_name__icontains",
            "prospect__contact__last_name__icontains",
            "prospect__contact__email__icontains",
            "prospect__contact__phone_number__icontains",
            "enrollment_status__icontains",
            "teacher__first_name__icontains",
            "teacher__last_name__icontains",
        ],
        Teacher: [
            "first_name__icontains",
            "last_name__icontains",
            "email__icontains",
            "phone__icontains",
            "qualification__icontains",
            "status__icontains",
        ],
        TeacherSpecialization: ["name__icontains"],
        Location: [
            "name__icontains",
            "code__icontains",
            "city__icontains",
            "province_state__icontains",
            "country__icontains",
        ],
        Course: ["name__icontains", "description__icontains", "status__icontains", "format__icontains"],
        CourseSession: [
            "session_name__icontains",
            "course__name__icontains",
            "teacher__first_name__icontains",
            "teacher__last_name__icontains",
            "location__name__icontains",
            "status__icontains",
            "delivery_mode__icontains",
        ],
        Inquiry: [
            "subject__icontains",
            "message__icontains",
            "status__icontains",
            "channel__icontains",
            "prospect__contact__first_name__icontains",
            "prospect__contact__last_name__icontains",
            "prospect__contact__email__icontains",
            "student__prospect__contact__first_name__icontains",
            "student__prospect__contact__last_name__icontains",
            "student__prospect__contact__email__icontains",
        ],
        Enrollment: [
            "status__icontains",
            "session__course__name__icontains",
            "session__session_name__icontains",
            "student__prospect__contact__first_name__icontains",
            "student__prospect__contact__last_name__icontains",
            "student__prospect__contact__email__icontains",
        ],
        Invoice: [
            "invoice_number__icontains",
            "status__icontains",
            "enrollment__student__prospect__contact__first_name__icontains",
            "enrollment__student__prospect__contact__last_name__icontains",
            "enrollment__student__prospect__contact__email__icontains",
        ],
        Payment: [
            "reference_number__icontains",
            "confirmation_status__icontains",
            "payment_method__icontains",
            "invoice__invoice_number__icontains",
            "invoice__enrollment__student__prospect__contact__first_name__icontains",
            "invoice__enrollment__student__prospect__contact__last_name__icontains",
            "invoice__enrollment__student__prospect__contact__email__icontains",
        ],
        Communication: [
            "subject__icontains",
            "body__icontains",
            "recipient_type__icontains",
            "channel__icontains",
            "communication_type__icontains",
            "delivery_status__icontains",
            "prospect__contact__first_name__icontains",
            "prospect__contact__last_name__icontains",
            "prospect__contact__email__icontains",
            "student__prospect__contact__first_name__icontains",
            "student__prospect__contact__last_name__icontains",
            "student__prospect__contact__email__icontains",
        ],
        InterviewForm: [
            "status__icontains",
            "summary__icontains",
            "recommendation__icontains",
            "student__prospect__contact__first_name__icontains",
            "student__prospect__contact__last_name__icontains",
            "teacher__first_name__icontains",
            "teacher__last_name__icontains",
        ],
        Disbursement: [
            "status__icontains",
            "teacher__first_name__icontains",
            "teacher__last_name__icontains",
            "location__name__icontains",
            "enrollment__session__course__name__icontains",
            "enrollment__session__session_name__icontains",
            "enrollment__student__prospect__contact__first_name__icontains",
            "enrollment__student__prospect__contact__last_name__icontains",
        ],
    }

    def _apply_search(self, queryset):
        query = (self.request.GET.get("q") or "").strip()
        if not query:
            return queryset

        if self.model is Contact and query.isdigit():
            id_match = queryset.filter(pk=int(query))
            if id_match.exists():
                return id_match

        filters = Q()
        for lookup in self.SEARCH_CONFIG.get(self.model, []):
            filters |= Q(**{lookup: query})
        if query.isdigit():
            filters |= Q(pk=int(query))
        if filters:
            return queryset.filter(filters)
        return queryset

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = scope_queryset_for_user(
            queryset=queryset,
            model=self.model,
            user=self.request.user,
        )
        if self.model is Prospect:
            queryset = queryset.select_related("contact", "teacher", "owner")
        if self.model is Prospect:
            converted_filter = (
                Q(converted_to_student=True)
                | Q(converted_student__isnull=False)
                | Q(status="converted")
            )
            state = (self.request.GET.get("state") or "active").strip().lower()
            if self.request.user.is_staff or self.request.user.is_superuser:
                if state == "archived":
                    queryset = queryset.filter(is_archived=True)
                elif state == "converted":
                    queryset = queryset.filter(converted_filter)
                elif state == "all":
                    queryset = queryset
                else:
                    queryset = queryset.filter(is_archived=False).exclude(converted_filter)
            else:
                queryset = queryset.filter(is_archived=False).exclude(converted_filter)
        if self.model is Invoice:
            queryset = queryset.select_related("enrollment__student__prospect")
        if self.model is Payment:
            queryset = queryset.select_related("invoice__enrollment__student__prospect")
        return self._apply_search(queryset)

    def get_template_names(self):
        slug = self._model_slug()
        return [
            f"core/crud/{slug}/list.html",
            "core/crud/model_list.html",
        ]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["search_query"] = (self.request.GET.get("q") or "").strip()
        params = self.request.GET.copy()
        params.pop("page", None)
        encoded = params.urlencode()
        context["querystring_without_page"] = encoded
        context["show_search"] = True
        _, model_name_plural = self._ui_model_names()
        context["search_label"] = f"Search {model_name_plural}"
        context["search_placeholder"] = (
            "Search by ID, name, email, phone, status, or related details"
        )
        context["search_clear_url"] = reverse_lazy(f"core:{self._model_slug()}-list")
        if self.model is Prospect and (self.request.user.is_staff or self.request.user.is_superuser):
            selected_state = (self.request.GET.get("state") or "active").strip().lower()
            if selected_state not in {"active", "archived", "converted", "all"}:
                selected_state = "active"
            context["prospect_state_filter"] = selected_state
        return context


class BaseDetailView(ProductLoginRequiredMixin, CRUDContextMixin, DetailView):
    def get_queryset(self):
        queryset = super().get_queryset()
        return scope_queryset_for_user(
            queryset=queryset,
            model=self.model,
            user=self.request.user,
        )

    def get_template_names(self):
        slug = self._model_slug()
        return [
            f"core/crud/{slug}/detail.html",
            "core/crud/model_detail.html",
        ]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        obj = context["object"]
        object_fields = []
        for field in obj._meta.fields:
            value = getattr(obj, field.name)
            object_fields.append((field.verbose_name.title(), value))
        for field in obj._meta.many_to_many:
            related_values = getattr(obj, field.name).all()
            display_value = ", ".join(str(item) for item in related_values) or "-"
            object_fields.append((field.verbose_name.title(), display_value))
        context["object_fields"] = object_fields
        return context


class BaseCreateView(ProductLoginRequiredMixin, CRUDContextMixin, CreateView):
    fields = "__all__"

    @staticmethod
    def _apply_governor_label_replacements(form):
        for field in form.fields.values():
            if field.label:
                field.label = field.label.replace("Teachers", "Governors").replace(
                    "Teacher", "Governor"
                )
            if field.help_text:
                field.help_text = str(field.help_text).replace("Teachers", "Governors").replace(
                    "Teacher", "Governor"
                )

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        if not self.request.user.is_superuser and "owner" in form.fields:
            form.fields.pop("owner")
        self._apply_governor_label_replacements(form)
        return form

    def form_valid(self, form):
        if hasattr(form.instance, "owner_id") and not self.request.user.is_superuser:
            form.instance.owner = self.request.user
        return super().form_valid(form)

    def get_template_names(self):
        slug = self._model_slug()
        return [
            f"core/crud/{slug}/form.html",
            "core/crud/model_form.html",
        ]

    def get_success_url(self):
        return reverse_lazy(
            f"core:{self._model_slug()}-detail",
            kwargs={"pk": self.object.pk},
        )


class BaseUpdateView(ProductLoginRequiredMixin, CRUDContextMixin, UpdateView):
    fields = "__all__"

    def get_queryset(self):
        queryset = super().get_queryset()
        return scope_queryset_for_user(
            queryset=queryset,
            model=self.model,
            user=self.request.user,
        )

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        if not self.request.user.is_superuser and "owner" in form.fields:
            form.fields.pop("owner")
        BaseCreateView._apply_governor_label_replacements(form)
        return form

    def get_template_names(self):
        slug = self._model_slug()
        return [
            f"core/crud/{slug}/form.html",
            "core/crud/model_form.html",
        ]

    def get_success_url(self):
        return reverse_lazy(
            f"core:{self._model_slug()}-detail",
            kwargs={"pk": self.object.pk},
        )


class BaseDeleteView(ProductLoginRequiredMixin, CRUDContextMixin, DeleteView):
    def get_queryset(self):
        queryset = super().get_queryset()
        return scope_queryset_for_user(
            queryset=queryset,
            model=self.model,
            user=self.request.user,
        )

    def get_template_names(self):
        slug = self._model_slug()
        return [
            f"core/crud/{slug}/confirm_delete.html",
            "core/crud/model_confirm_delete.html",
        ]

    def get_success_url(self):
        return reverse_lazy(f"core:{self._model_slug()}-list")


class CommunicationCreateView(BaseCreateView):
    model = Communication
    form_class = CommunicationForm
    fields = None

    def get_initial(self):
        initial = super().get_initial()
        recipient_type = (self.request.GET.get("recipient_type") or "").strip().lower()
        student_id = (self.request.GET.get("student") or "").strip()
        prospect_id = (self.request.GET.get("prospect") or "").strip()
        enrollment_id = (self.request.GET.get("enrollment") or "").strip()

        if recipient_type in {"prospect", "student"}:
            initial["recipient_type"] = recipient_type
        if student_id.isdigit():
            initial["recipient_type"] = "student"
            initial["student"] = int(student_id)
        elif prospect_id.isdigit():
            initial["recipient_type"] = "prospect"
            initial["prospect"] = int(prospect_id)

        if enrollment_id.isdigit():
            initial["enrollment"] = int(enrollment_id)
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = context.get("form")
        prospect = None
        student = None

        prospect_id = self.request.GET.get("prospect")
        student_id = self.request.GET.get("student")
        if form and form.is_bound:
            if form.data.get("prospect"):
                prospect_id = form.data.get("prospect")
            if form.data.get("student"):
                student_id = form.data.get("student")

        if prospect_id and str(prospect_id).isdigit():
            prospect = Prospect.objects.select_related("contact").filter(pk=int(prospect_id)).first()
        if student_id and str(student_id).isdigit():
            student = Student.objects.select_related("prospect__contact").filter(pk=int(student_id)).first()
            if student and not prospect:
                prospect = student.prospect

        context["recipient_prospect"] = prospect
        context["recipient_student"] = student
        context["recipient_contact"] = prospect.contact if prospect and prospect.contact_id else None
        return context

    def form_valid(self, form):
        communication = form.save(commit=False)
        communication.owner = self.request.user
        communication.channel = CommunicationChannel.EMAIL

        recipient_email = ""
        if communication.recipient_type == "prospect" and communication.prospect_id:
            recipient_email = (communication.prospect.contact.email or "").strip()
        elif communication.recipient_type == "student" and communication.student_id:
            recipient_email = (communication.student.prospect.contact.email or "").strip()
        if not recipient_email:
            form.add_error(None, "Selected recipient does not have an email address.")
            return self.form_invalid(form)

        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@localhost")
        reply_to = [self.request.user.email] if getattr(self.request.user, "email", "") else None

        try:
            email_message = EmailMessage(
                subject=communication.subject or "TMIS Message",
                body=communication.body,
                from_email=from_email,
                to=[recipient_email],
                reply_to=reply_to or None,
            )
            email_message.send(fail_silently=False)
            communication.delivery_status = DeliveryStatus.SENT
            communication.provider_status = communication.provider_status or "outbound"
            communication.sent_at = communication.sent_at or timezone.now()
        except Exception:
            communication.delivery_status = DeliveryStatus.FAILED
            communication.provider_status = communication.provider_status or "send_failed"
            communication.sent_at = communication.sent_at or timezone.now()

        communication.save()
        self.object = communication
        return redirect(self.get_success_url())


class CommunicationUpdateView(BaseUpdateView):
    model = Communication
    form_class = CommunicationForm
    fields = None


class ProspectCreateView(BaseCreateView):
    model = Prospect
    form_class = ProspectForm
    fields = None


class ProspectUpdateView(BaseUpdateView):
    model = Prospect
    form_class = ProspectForm
    fields = None


class EnrollmentCreateView(BaseCreateView):
    model = Enrollment
    form_class = EnrollmentForm
    fields = None


class EnrollmentUpdateView(BaseUpdateView):
    model = Enrollment
    form_class = EnrollmentForm
    fields = None


class PaymentCreateView(BaseCreateView):
    model = Payment
    form_class = PaymentForm
    fields = None


class PaymentUpdateView(BaseUpdateView):
    model = Payment
    form_class = PaymentForm
    fields = None


class StudentCreateView(BaseCreateView):
    model = Student
    form_class = StudentForm
    fields = None


class StudentUpdateView(BaseUpdateView):
    model = Student
    form_class = StudentForm
    fields = None


class StudentArchiveView(ProductLoginRequiredMixin, View):
    def post(self, request, pk):
        student = get_object_or_404(
            scope_queryset_for_user(
                queryset=Student.objects.all(),
                model=Student,
                user=request.user,
            ),
            pk=pk,
        )
        if student.enrollment_status == EnrollmentStatus.INACTIVE:
            messages.info(request, f"{student} is already marked inactive.")
        else:
            student.enrollment_status = EnrollmentStatus.INACTIVE
            student.save(update_fields=["enrollment_status"])
            messages.success(request, f"{student} was archived and marked inactive.")
        return redirect("core:student-detail", pk=student.pk)


class StudentDetailView(BaseDetailView):
    model = Student

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        student = self.object
        today = timezone.localdate()
        now = timezone.now()

        enrollments = (
            Enrollment.objects.filter(student=student)
            .select_related("session__course", "session__teacher")
            .order_by("-enrollment_date", "-pk")
        )
        invoices = (
            Invoice.objects.filter(enrollment__student=student)
            .select_related("enrollment")
            .annotate(
                amount_paid=Coalesce(
                    Sum("payments__amount_paid"),
                    Value(Decimal("0.00")),
                    output_field=DecimalField(max_digits=10, decimal_places=2),
                )
            )
            .annotate(
                balance_due=ExpressionWrapper(
                    F("total_amount") - F("amount_paid"),
                    output_field=DecimalField(max_digits=10, decimal_places=2),
                )
            )
            .order_by("-issue_date", "-pk")
        )

        total_amount_paid = (
            Payment.objects.filter(invoice__enrollment__student=student).aggregate(
                total=Coalesce(
                    Sum("amount_paid"),
                    Value(Decimal("0.00")),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                )
            )["total"]
            or Decimal("0.00")
        )
        outstanding_balance = (
            invoices.aggregate(
                total=Coalesce(
                    Sum("balance_due"),
                    Value(Decimal("0.00")),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                )
            )["total"]
            or Decimal("0.00")
        )
        assigned_teachers_count = (
            enrollments.exclude(session__teacher__isnull=True)
            .values("session__teacher_id")
            .distinct()
            .count()
        )
        latest_enrollment = enrollments.first()
        latest_payment = (
            Payment.objects.filter(invoice__enrollment__student=student)
            .order_by("-payment_date")
            .first()
        )
        latest_communication = (
            Communication.objects.filter(student=student).order_by("-created_at").first()
        )
        last_activity_dt = max(
            dt
            for dt in [
                student.updated_at,
                latest_enrollment.enrollment_date if latest_enrollment else None,
                latest_payment.payment_date if latest_payment else None,
                latest_communication.created_at if latest_communication else None,
            ]
            if dt is not None
        )

        course_history = [
            {
                "name": enrollment.session.course.name,
                "start_date": enrollment.session.start_date,
                "end_date": enrollment.session.end_date,
                "status": enrollment.get_status_display(),
                "teacher": enrollment.session.teacher,
                "progress": "-",
            }
            for enrollment in enrollments
        ]

        teacher_history = []
        for enrollment in enrollments:
            teacher = enrollment.session.teacher
            if not teacher:
                continue
            status_value = (
                "Current"
                if enrollment.status
                in {
                    EnrollmentStatus.ENROLLED,
                    EnrollmentStatus.ACTIVE,
                    EnrollmentStatus.PENDING,
                }
                else "Previous"
            )
            teacher_history.append(
                {
                    "name": teacher,
                    "role": (
                        "Primary Governor"
                        if student.teacher_id and student.teacher_id == teacher.pk
                        else "Course Governor"
                    ),
                    "course": enrollment.session.course.name,
                    "date_assigned": enrollment.enrollment_date,
                    "status": status_value,
                    "status_class": "" if status_value == "Current" else "sp-chip-soft",
                }
            )

        recent_payments = (
            Payment.objects.filter(invoice__enrollment__student=student)
            .select_related("invoice")
            .order_by("-payment_date", "-pk")
        )

        timeline_events = []
        for payment in recent_payments[:8]:
            payment_note_parts = [
                f"{payment.get_payment_method_display()} · ${payment.amount_paid.quantize(Decimal('0.01'))}",
                f"Invoice {payment.invoice.invoice_number}",
            ]
            if payment.reference_number:
                payment_note_parts.append(f"Ref: {payment.reference_number}")
            if payment.notes:
                payment_note_parts.append(payment.notes[:120])
            timeline_events.append(
                {
                    "event_datetime": payment.payment_date,
                    "title": "Payment recorded",
                    "date": payment.payment_date,
                    "author": "Finance",
                    "note": " · ".join(payment_note_parts),
                }
            )

        for communication in Communication.objects.filter(student=student).order_by(
            "-created_at"
        )[:8]:
            timeline_events.append(
                {
                    "event_datetime": communication.created_at,
                    "title": communication.get_communication_type_display(),
                    "date": communication.created_at,
                    "author": "CRM",
                    "note": communication.subject or communication.body[:120] or "-",
                }
            )
        for enrollment in enrollments[:5]:
            timeline_events.append(
                {
                    "event_datetime": enrollment.enrollment_date,
                    "title": "Enrollment updated",
                    "date": enrollment.enrollment_date,
                    "author": enrollment.session.teacher or "TMIS",
                    "note": f"{enrollment.session.course.name} ({enrollment.get_status_display()})",
                }
            )
        for invoice in invoices[:5]:
            timeline_events.append(
                {
                    "event_datetime": timezone.make_aware(
                        datetime.combine(invoice.issue_date, time.min)
                    ),
                    "title": "Invoice issued",
                    "date": invoice.issue_date,
                    "author": "Finance",
                    "note": (
                        f"{invoice.invoice_number} · Balance due "
                        f"{invoice.balance_due.quantize(Decimal('0.01'))}"
                    ),
                }
            )
            if invoice.notes:
                timeline_events.append(
                    {
                        "event_datetime": invoice.updated_at,
                        "title": "Invoice note updated",
                        "date": invoice.updated_at,
                        "author": "Finance",
                        "note": f"{invoice.invoice_number} · {invoice.notes[:120]}",
                    }
                )
        if student.notes:
            timeline_events.append(
                {
                    "event_datetime": student.updated_at,
                    "title": "Student note updated",
                    "date": student.updated_at,
                    "author": "TMIS",
                    "note": student.notes[:120],
                }
            )
        timeline_events = sorted(
            timeline_events, key=lambda item: item["event_datetime"], reverse=True
        )[:10]

        next_enrollment = (
            enrollments.filter(session__start_date__gte=now)
            .select_related("session")
            .order_by("session__start_date")
            .first()
        )
        primary_teacher = student.teacher or (
            latest_enrollment.session.teacher if latest_enrollment else None
        )
        overdue_exists = invoices.filter(balance_due__gt=0, due_date__lt=today).exists()
        if not invoices.exists():
            payment_status = "No Invoices"
            payment_status_class = "sp-chip-soft"
        elif outstanding_balance <= Decimal("0.00"):
            payment_status = "Paid"
            payment_status_class = ""
        elif overdue_exists:
            payment_status = "Overdue"
            payment_status_class = "sp-chip-overdue"
        else:
            payment_status = "Partially Paid"
            payment_status_class = "sp-chip-partial"

        tags = []
        if student.get_enrollment_status_display():
            tags.append(student.get_enrollment_status_display())
        if student.city:
            tags.append(student.city)
        if student.country:
            tags.append(student.country)

        context["invoices"] = invoices
        context["today"] = today
        context["student_metrics"] = {
            "total_courses": enrollments.count(),
            "total_amount_paid": total_amount_paid,
            "outstanding_balance": outstanding_balance,
            "assigned_teachers_count": assigned_teachers_count,
            "last_activity": last_activity_dt,
        }
        context["course_history"] = course_history
        context["teacher_history"] = teacher_history
        context["timeline_events"] = timeline_events
        context["student_summary"] = {
            "primary_teacher": primary_teacher,
            "next_session": (
                next_enrollment.session.start_date if next_enrollment else None
            ),
            "payment_status": payment_status,
            "payment_status_class": payment_status_class,
            "tags": tags,
        }
        return context


class ProspectDetailView(BaseDetailView):
    model = Prospect

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        prospect = self.object
        latest_communication = prospect.communications.order_by("-created_at", "-pk").first()
        latest_follow_up = (
            prospect.communications.filter(communication_type="follow_up")
            .order_by("-created_at", "-pk")
            .first()
        )
        has_student_record = hasattr(prospect, "student_record")
        primary_fields = [
            ("Full Name", str(prospect) or "-"),
            ("Phone Number", prospect.phone or "-"),
            ("Email", prospect.email or "-"),
            ("Source", prospect.source or "-"),
            ("Status", prospect.get_status_display() or "-"),
            ("Assigned Teacher", str(prospect.teacher) if prospect.teacher_id else "-"),
            ("Assigned User", str(prospect.owner) if prospect.owner_id else "-"),
            ("Interest", prospect.get_interest_level_display() or "-"),
            ("Last Contacted", latest_communication.created_at if latest_communication else None),
            ("Next Follow-up", "-"),
            ("Created At", prospect.created_at),
        ]
        secondary_fields = [
            (
                "Preferred Contact Method",
                prospect.get_preferred_contact_method_display()
                if prospect.preferred_contact_method
                else "-",
            ),
            ("Archived", "Yes" if prospect.is_archived else "No"),
            (
                "Last Follow-up Type",
                latest_follow_up.get_communication_type_display() if latest_follow_up else "-",
            ),
        ]
        context["primary_fields"] = primary_fields
        context["secondary_fields"] = secondary_fields
        context["has_student_record"] = has_student_record
        context["contact_attempt_count"] = prospect.contact_attempt_count
        context["communications"] = prospect.communications.order_by("-created_at", "-pk")
        return context


class InvoiceDetailView(BaseDetailView):
    model = Invoice

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        invoice = self.object
        total_paid = (
            invoice.payments.aggregate(
                total=Coalesce(
                    Sum("amount_paid"),
                    Value(Decimal("0.00")),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                )
            )["total"]
            or Decimal("0.00")
        )
        outstanding_balance = (invoice.total_amount or Decimal("0.00")) - total_paid
        if outstanding_balance < Decimal("0.00"):
            outstanding_balance = Decimal("0.00")

        context["invoice_student"] = invoice.enrollment.student
        context["payments"] = invoice.payments.order_by("-payment_date", "-pk")
        context["total_paid"] = total_paid
        context["outstanding_balance"] = outstanding_balance
        context["can_add_payment"] = outstanding_balance > Decimal("0.00")
        return context


def _recalculate_invoice_status(invoice: Invoice) -> None:
    total_paid = (
        invoice.payments.aggregate(
            total=Coalesce(
                Sum("amount_paid"),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            )
        )["total"]
        or Decimal("0.00")
    )
    outstanding = (invoice.total_amount or Decimal("0.00")) - total_paid
    today = timezone.localdate()

    if outstanding <= Decimal("0.00"):
        new_status = "paid"
    elif invoice.due_date and invoice.due_date < today:
        new_status = "overdue"
    elif total_paid > Decimal("0.00"):
        new_status = "partial"
    else:
        new_status = "sent"

    if invoice.status != new_status:
        invoice.status = new_status
        invoice.save(update_fields=["status", "updated_at"])


@login_required(login_url="/login/")
def add_invoice_payment(request, pk):
    scoped_invoices = scope_queryset_for_user(
        queryset=Invoice.objects.select_related("enrollment__student__prospect"),
        model=Invoice,
        user=request.user,
    )
    invoice = get_object_or_404(scoped_invoices, pk=pk)

    total_paid = (
        invoice.payments.aggregate(
            total=Coalesce(
                Sum("amount_paid"),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            )
        )["total"]
        or Decimal("0.00")
    )
    outstanding_balance = (invoice.total_amount or Decimal("0.00")) - total_paid
    if outstanding_balance <= Decimal("0.00"):
        messages.info(request, "Invoice already fully paid.")
        return redirect("core:invoice-detail", pk=invoice.pk)

    if request.method == "POST":
        form = InvoicePaymentForm(request.POST)
        if form.is_valid():
            payment = form.save(commit=False)
            if payment.amount_paid > outstanding_balance:
                form.add_error(
                    "amount_paid",
                    f"Amount cannot exceed outstanding balance of {outstanding_balance:.2f}.",
                )
            else:
                payment.invoice = invoice
                payment.owner = invoice.owner
                payment.save()
                _recalculate_invoice_status(invoice)
                messages.success(
                    request,
                    f"Payment recorded for invoice {invoice.invoice_number}.",
                )
                return redirect("core:invoice-detail", pk=invoice.pk)
    else:
        form = InvoicePaymentForm(
            initial={"payment_date": timezone.localtime().strftime("%Y-%m-%dT%H:%M")}
        )

    context = {
        "invoice": invoice,
        "student": invoice.enrollment.student,
        "total_paid": total_paid,
        "outstanding_balance": outstanding_balance,
        "form": form,
    }
    return render(request, "core/crud/invoice/add_payment.html", context)


for _model in CRUD_MODELS:
    _name = _model.__name__
    list_view_name = f"{_name}ListView"
    detail_view_name = f"{_name}DetailView"
    create_view_name = f"{_name}CreateView"
    update_view_name = f"{_name}UpdateView"
    delete_view_name = f"{_name}DeleteView"

    if list_view_name not in globals():
        globals()[list_view_name] = type(
            list_view_name,
            (BaseListView,),
            {"model": _model},
        )
    if detail_view_name not in globals():
        globals()[detail_view_name] = type(
            detail_view_name,
            (BaseDetailView,),
            {"model": _model},
        )
    if create_view_name not in globals():
        globals()[create_view_name] = type(
            create_view_name,
            (BaseCreateView,),
            {"model": _model},
        )
    if update_view_name not in globals():
        globals()[update_view_name] = type(
            update_view_name,
            (BaseUpdateView,),
            {"model": _model},
        )
    if delete_view_name not in globals():
        globals()[delete_view_name] = type(
            delete_view_name,
            (BaseDeleteView,),
            {"model": _model},
        )


del _model
