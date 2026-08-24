from datetime import datetime, time
from decimal import Decimal
import json
import logging
from pathlib import Path

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.files.base import ContentFile
from django.core.mail import EmailMessage
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import DecimalField, ExpressionWrapper, F, Q, Sum, Value
from django.db.models.functions import Coalesce, Concat
from django.http import HttpResponse, JsonResponse, QueryDict
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
    StudentCreateForm,
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
    CommunicationType,
    DeliveryStatus,
    Contact,
    Course,
    CourseSession,
    Disbursement,
    Enrollment,
    Inquiry,
    InquiryChannel,
    InquiryStatus,
    InterviewForm,
    Invoice,
    Location,
    Meditator,
    Payment,
    PaymentConfirmationStatus,
    Prospect,
    ProspectStatus,
    RecipientType,
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
from .services.governor_compensation import get_governor_compensation_data
from .services.home_dashboard import get_home_dashboard_data
from .services.invoicing import generate_invoice_for_enrollment
from .services.prospect_conversion import (
    attach_prospect_conversion_eligibility,
    get_or_create_student_enrollment_shell,
    get_prospect_conversion_eligibility,
)
from .services.invoicing import send_invoice_email
from .services.enrollment_eligibility import (
    check_course_eligibility,
    is_eligible_for_course,
    validate_course_eligibility,
)
from .services.enrollment_completion import check_student_completion_financials
from .services.invoice_pdf import build_invoice_pdf

logger = logging.getLogger(__name__)


@require_POST
def secure_logout_view(request):
    username = request.user.get_username() if request.user.is_authenticated else ""
    logout(request)
    if username:
        messages.success(request, f"You have been logged out, {username}.")
    return redirect("core:login")


def _supports_uuid_lookup(model):
    if model not in {Contact, Prospect, Student, Meditator, Inquiry}:
        return False
    return any(field.name == "uuid" for field in model._meta.fields)


def _resolve_object_by_pk_or_uuid(*, queryset, model, identifier):
    if _supports_uuid_lookup(model):
        candidate = str(identifier or "").strip()
        if candidate:
            normalized = candidate.replace("-", "").lower()
            # Only attempt UUID lookups for UUID-like tokens to avoid
            # validation errors for numeric primary keys (e.g. "1270").
            uuid_like = len(normalized) >= 8 and all(ch in "0123456789abcdef" for ch in normalized)
            if uuid_like:
                try:
                    short_matches = queryset.filter(uuid__startswith=normalized)
                    by_public_id = short_matches.first() if short_matches.count() == 1 else None
                    if by_public_id is not None:
                        return by_public_id
                    by_uuid = queryset.filter(uuid=candidate).first()
                    if by_uuid is not None:
                        return by_uuid
                except Exception:
                    # Fall back to pk lookup below.
                    pass
    return get_object_or_404(queryset, pk=identifier)


def _build_bulk_filtered_queryset(recipient_kind, user, source_query):
    class _SimpleRequest:
        def __init__(self, get_data, request_user):
            self.GET = get_data
            self.user = request_user

    querydict = QueryDict(source_query or "", mutable=False)
    simple_request = _SimpleRequest(querydict, user)

    if recipient_kind == "prospect":
        view = ProspectListView()
        view.request = simple_request
        return view.get_queryset()
    if recipient_kind == "student":
        view = StudentListView()
        view.request = simple_request
        return view.get_queryset()
    if recipient_kind == "contact":
        view = ContactListView()
        view.request = simple_request
        return view.get_queryset()
    if recipient_kind == "meditator":
        view = MeditatorListView()
        view.request = simple_request
        return view.get_queryset()
    return None


def _collect_bulk_recipient_emails(recipient_kind, queryset):
    emails = []
    if recipient_kind == "prospect":
        for obj in queryset.select_related("contact"):
            if obj.contact_id and obj.contact and obj.contact.email:
                emails.append(obj.contact.email.strip())
    elif recipient_kind == "student":
        for obj in queryset.select_related("prospect__contact"):
            if obj.prospect_id and obj.prospect.contact_id and obj.prospect.contact.email:
                emails.append(obj.prospect.contact.email.strip())
    elif recipient_kind == "contact":
        for obj in queryset:
            if obj.email:
                emails.append(obj.email.strip())
    elif recipient_kind == "meditator":
        for obj in queryset.select_related("student__prospect__contact"):
            if (
                obj.student_id
                and obj.student.prospect_id
                and obj.student.prospect.contact_id
                and obj.student.prospect.contact.email
            ):
                emails.append(obj.student.prospect.contact.email.strip())
    return sorted({email for email in emails if email})


@require_POST
@login_required(login_url="/login/")
def bulk_message_send_view(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Invalid payload."}, status=400)

    recipient_kind = (payload.get("recipient_kind") or "").strip().lower()
    bulk_mode = (payload.get("bulk_mode") or "selected").strip().lower()
    source_query = (payload.get("source_query") or "").strip()
    selected_ids = payload.get("selected_ids") or []
    subject = (payload.get("subject") or "").strip()
    body = (payload.get("body") or "").strip()

    if recipient_kind not in {"prospect", "student", "contact", "meditator"}:
        return JsonResponse({"ok": False, "error": "Unsupported recipient type."}, status=400)
    if not body:
        return JsonResponse({"ok": False, "error": "Message body is required."}, status=400)

    filtered_queryset = _build_bulk_filtered_queryset(recipient_kind, request.user, source_query)
    if filtered_queryset is None:
        return JsonResponse({"ok": False, "error": "Unable to build recipient set."}, status=400)

    if bulk_mode == "filtered":
        recipient_queryset = filtered_queryset
    else:
        normalized_ids = [str(value).strip() for value in selected_ids if str(value).strip()]
        if not normalized_ids:
            return JsonResponse({"ok": False, "error": "No selected recipients found."}, status=400)
        recipient_queryset = filtered_queryset.filter(pk__in=normalized_ids)

    recipient_emails = _collect_bulk_recipient_emails(recipient_kind, recipient_queryset)
    if not recipient_emails:
        return JsonResponse(
            {"ok": False, "error": "No recipients with valid email addresses found."},
            status=400,
        )

    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "rokosun@tm.org")
    sent_count = 0
    failed_count = 0
    for recipient_email in recipient_emails:
        try:
            EmailMessage(
                subject=subject or "TMIS Message",
                body=body,
                from_email=from_email,
                to=[recipient_email],
            ).send(fail_silently=False)
            sent_count += 1
        except Exception:
            failed_count += 1

    return JsonResponse(
        {
            "ok": True,
            "sent_count": sent_count,
            "failed_count": failed_count,
            "recipient_count": len(recipient_emails),
        }
    )


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
            Meditator.objects.filter(is_active=True).select_related(
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
        status = (self.request.GET.get("status") or "").strip()
        assigned_user = (self.request.GET.get("assigned_user") or "").strip()
        activity = (self.request.GET.get("activity") or "").strip().lower()
        course = (self.request.GET.get("course") or "").strip()
        created_from = (self.request.GET.get("created_from") or "").strip()
        created_to = (self.request.GET.get("created_to") or "").strip()

        if status:
            queryset = queryset.filter(student__enrollment_status=status)
        if assigned_user.isdigit():
            queryset = queryset.filter(student__owner_id=int(assigned_user))
        if activity == "active":
            queryset = queryset.exclude(student__enrollment_status=EnrollmentStatus.INACTIVE)
        elif activity == "inactive":
            queryset = queryset.filter(student__enrollment_status=EnrollmentStatus.INACTIVE)
        if course.isdigit():
            queryset = queryset.filter(student__enrollments__session__course_id=int(course))
        if created_from:
            queryset = queryset.filter(transitioned_at__date__gte=created_from)
        if created_to:
            queryset = queryset.filter(transitioned_at__date__lte=created_to)

        query = (self.request.GET.get("q") or "").strip()
        if query:
            terms = [part for part in query.split() if part]
            if terms:
                queryset = queryset.annotate(
                    search_full_name=Concat(
                        F("student__prospect__contact__first_name"),
                        Value(" "),
                        F("student__prospect__contact__last_name"),
                    )
                )
                token_lookups = [
                    "search_full_name__icontains",
                    "student__prospect__contact__first_name__icontains",
                    "student__prospect__contact__last_name__icontains",
                    "student__prospect__contact__email__icontains",
                    "student__prospect__contact__phone_number__icontains",
                    "student__teacher__first_name__icontains",
                    "student__teacher__last_name__icontains",
                    "student__enrollment_status__icontains",
                    "student__owner__username__icontains",
                    "student__owner__email__icontains",
                ]
                combined = Q()
                for term in terms:
                    per_term = Q()
                    for lookup in token_lookups:
                        per_term |= Q(**{lookup: term})
                    if term.isdigit():
                        per_term |= Q(pk=int(term)) | Q(student__pk=int(term))
                    combined &= per_term
                phrase = Q(search_full_name__icontains=query)
                if query.isdigit():
                    phrase |= Q(pk=int(query)) | Q(student__pk=int(query))
                queryset = queryset.filter(combined | phrase)

        return queryset.distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        UserModel = get_user_model()
        scoped_meditators = scope_queryset_for_user(
            queryset=Meditator.objects.filter(is_active=True).select_related(
                "student__owner"
            ),
            model=Meditator,
            user=self.request.user,
        )
        owner_ids = list(
            scoped_meditators.exclude(student__owner__isnull=True)
            .values_list("student__owner_id", flat=True)
            .distinct()
        )
        context["search_query"] = (self.request.GET.get("q") or "").strip()
        params = self.request.GET.copy()
        params.pop("page", None)
        context["querystring_without_page"] = params.urlencode()
        context["show_search"] = True
        context["search_label"] = "Search Meditators"
        context["search_placeholder"] = "Search by student, governor, status, email, phone, or ID"
        context["search_clear_url"] = reverse_lazy("core:meditator-list")
        context["meditator_status_choices"] = EnrollmentStatus.choices
        context["meditator_owner_choices"] = UserModel.objects.filter(id__in=owner_ids).order_by(
            "first_name", "last_name", "username"
        )
        context["meditator_course_choices"] = Course.objects.filter(
            sessions__enrollments__student__meditator_profile__in=scoped_meditators
        ).distinct().order_by("name")
        context["meditator_filter_values"] = {
            "status": (self.request.GET.get("status") or "").strip(),
            "assigned_user": (self.request.GET.get("assigned_user") or "").strip(),
            "activity": (self.request.GET.get("activity") or "").strip().lower(),
            "course": (self.request.GET.get("course") or "").strip(),
            "created_from": (self.request.GET.get("created_from") or "").strip(),
            "created_to": (self.request.GET.get("created_to") or "").strip(),
        }
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
        attach_prospect_conversion_eligibility(context.get("prospect_list", []))
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
        prospect = _resolve_object_by_pk_or_uuid(
            queryset=get_user_scoped_prospect_queryset(request.user),
            model=Prospect,
            identifier=pk,
        )
        was_converted = bool(
            prospect.converted_to_student
            or prospect.converted_student_id
            or prospect.status == ProspectStatus.CONVERTED
        )
        try:
            student, created = convert_prospect_to_student_for_pipeline(prospect)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect("core:prospect-pipeline-detail", pk=prospect.pk)
        if created or not was_converted:
            messages.success(request, f"{prospect} was converted to Student successfully.")
        else:
            messages.info(request, f"{prospect} is already linked to Student #{student.pk}.")
        return redirect("core:prospect-pipeline-detail", pk=prospect.pk)


class ProspectFollowUpCreateView(ProductLoginRequiredMixin, View):
    def post(self, request, pk):
        prospect = _resolve_object_by_pk_or_uuid(
            queryset=get_user_scoped_prospect_queryset(request.user),
            model=Prospect,
            identifier=pk,
        )
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
        return _resolve_object_by_pk_or_uuid(
            queryset=scope_queryset_for_user(
                queryset=Contact.objects.all(),
                model=Contact,
                user=request.user,
            ),
            model=Contact,
            identifier=pk,
        )

    def _build_form(self, request, *, contact, data=None):
        owner = request.user if request.user.is_authenticated and not request.user.is_superuser else None
        instance = Prospect(contact=contact, owner=owner)
        return ProspectForm(data=data, instance=instance)

    def get(self, request, pk):
        contact = self._get_contact(request, pk)
        if contact.has_converted_prospect:
            existing = getattr(contact, "prospect", None)
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
            except IntegrityError:
                existing = Prospect.objects.filter(contact=contact).first()
                if existing:
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
        prospect = _resolve_object_by_pk_or_uuid(
            queryset=scope_queryset_for_user(
                queryset=Prospect.objects.all(),
                model=Prospect,
                user=request.user,
            ),
            model=Prospect,
            identifier=pk,
        )
        existing_student = getattr(prospect, "student_record", None) or prospect.converted_student
        if prospect.status == "converted" and existing_student is not None:
            messages.info(
                request,
                f"{prospect} is already linked to Student #{existing_student.pk}.",
            )
            return redirect("core:student-detail", pk=existing_student.pk)
        was_converted = bool(
            prospect.converted_to_student
            or prospect.converted_student_id
            or prospect.status == ProspectStatus.CONVERTED
        )
        try:
            student, created = convert_prospect_to_student_for_pipeline(prospect)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            next_url = request.POST.get("next") or request.META.get("HTTP_REFERER")
            if next_url:
                return redirect(next_url)
            return redirect("core:prospect-list")

        if created or not was_converted:
            messages.success(request, f"{prospect} was converted to Student successfully.")
            return redirect("core:student-detail", pk=student.pk)
        else:
            messages.info(request, f"{prospect} is already linked to Student #{student.pk}.")
            return redirect("core:student-detail", pk=student.pk)


class TeacherEarningsDashboardView(ProductLoginRequiredMixin, TemplateView):
    template_name = "core/reporting/teacher_earnings_dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        compensation = get_governor_compensation_data(user=self.request.user)
        if not compensation["can_view"]:
            raise PermissionDenied("Governor compensation is limited to Governors and administrators.")
        context.update(compensation)
        context["generated_on"] = timezone.localdate()
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

    @staticmethod
    def _supports_uuid_lookup(model):
        if model not in {Contact, Prospect, Student, Meditator, Inquiry}:
            return False
        return any(field.name == "uuid" for field in model._meta.fields)

    def _resolve_scoped_object(self, queryset, identifier):
        if self._supports_uuid_lookup(self.model):
            candidate = str(identifier or "").strip()
            if candidate:
                normalized = candidate.replace("-", "").lower()
                uuid_like = len(normalized) >= 8 and all(ch in "0123456789abcdef" for ch in normalized)
                if uuid_like:
                    try:
                        short_matches = queryset.filter(uuid__startswith=normalized)
                        by_public_id = short_matches.first() if short_matches.count() == 1 else None
                        if by_public_id is not None:
                            return by_public_id
                        by_uuid = queryset.filter(uuid=candidate).first()
                        if by_uuid is not None:
                            return by_uuid
                    except Exception:
                        pass
        return get_object_or_404(queryset, pk=identifier)

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
            "address__icontains",
            "city__icontains",
            "province_state__icontains",
            "country__icontains",
        ],
        Student: [
            "prospect__contact__first_name__icontains",
            "prospect__contact__last_name__icontains",
            "prospect__contact__email__icontains",
            "prospect__contact__phone_number__icontains",
            "prospect__contact__city__icontains",
            "prospect__contact__province_state__icontains",
            "prospect__contact__country__icontains",
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

        if self.model is Student:
            tokens = [part for part in query.split() if part]
            if not tokens:
                return queryset

            queryset = queryset.annotate(
                search_full_name=Concat(
                    F("prospect__contact__first_name"),
                    Value(" "),
                    F("prospect__contact__last_name"),
                )
            )
            token_lookups = [
                "search_full_name__icontains",
                "prospect__contact__first_name__icontains",
                "prospect__contact__last_name__icontains",
                "prospect__contact__email__icontains",
                "prospect__contact__phone_number__icontains",
                "enrollment_status__icontains",
                "teacher__first_name__icontains",
                "teacher__last_name__icontains",
                "owner__username__icontains",
                "owner__email__icontains",
                "owner__first_name__icontains",
                "owner__last_name__icontains",
                "prospect__status__icontains",
                "prospect__source__icontains",
            ]
            combined = Q()
            for token in tokens:
                per_token = Q()
                for lookup in token_lookups:
                    per_token |= Q(**{lookup: token})
                if token.isdigit():
                    per_token |= Q(pk=int(token))
                combined &= per_token

            # Also allow single-field phrase search for quoted/long inputs.
            phrase_filters = Q(search_full_name__icontains=query)
            if query.isdigit():
                phrase_filters |= Q(pk=int(query))

            return queryset.filter(combined | phrase_filters).distinct()

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

    def _apply_student_filters(self, queryset):
        if self.model is not Student:
            return queryset

        status = (self.request.GET.get("status") or "").strip()
        assigned_user = (self.request.GET.get("assigned_user") or "").strip()
        activity = (self.request.GET.get("activity") or "").strip().lower()
        course = (self.request.GET.get("course") or "").strip()
        created_from = (self.request.GET.get("created_from") or "").strip()
        created_to = (self.request.GET.get("created_to") or "").strip()

        if status:
            queryset = queryset.filter(enrollment_status=status)
        if assigned_user.isdigit():
            queryset = queryset.filter(owner_id=int(assigned_user))
        if activity == "active":
            queryset = queryset.exclude(enrollment_status=EnrollmentStatus.INACTIVE)
        elif activity == "inactive":
            queryset = queryset.filter(enrollment_status=EnrollmentStatus.INACTIVE)
        if course.isdigit():
            queryset = queryset.filter(enrollments__session__course_id=int(course))
        if created_from:
            queryset = queryset.filter(created_at__date__gte=created_from)
        if created_to:
            queryset = queryset.filter(created_at__date__lte=created_to)
        return queryset.distinct()

    def _apply_prospect_filters(self, queryset):
        if self.model is not Prospect:
            return queryset

        status = (self.request.GET.get("status") or "").strip()
        assigned_user = (self.request.GET.get("assigned_user") or "").strip()
        activity = (self.request.GET.get("activity") or "").strip().lower()
        course = (self.request.GET.get("course") or "").strip()
        created_from = (self.request.GET.get("created_from") or "").strip()
        created_to = (self.request.GET.get("created_to") or "").strip()

        if status:
            queryset = queryset.filter(status=status)
        if assigned_user.isdigit():
            queryset = queryset.filter(owner_id=int(assigned_user))
        if activity == "active":
            queryset = queryset.filter(is_archived=False)
        elif activity == "inactive":
            queryset = queryset.filter(is_archived=True)
        if course.isdigit():
            queryset = queryset.filter(course_interest_id=int(course))
        if created_from:
            queryset = queryset.filter(created_at__date__gte=created_from)
        if created_to:
            queryset = queryset.filter(created_at__date__lte=created_to)
        return queryset.distinct()

    def _apply_contact_filters(self, queryset):
        if self.model is not Contact:
            return queryset

        conversion = (self.request.GET.get("conversion") or "").strip().lower()
        has_email = (self.request.GET.get("has_email") or "").strip().lower()
        has_phone = (self.request.GET.get("has_phone") or "").strip().lower()
        created_from = (self.request.GET.get("created_from") or "").strip()
        created_to = (self.request.GET.get("created_to") or "").strip()

        if conversion == "converted":
            queryset = queryset.filter(prospect__isnull=False)
        elif conversion == "not_converted":
            queryset = queryset.filter(prospect__isnull=True)
        if has_email == "yes":
            queryset = queryset.exclude(email__isnull=True).exclude(email="")
        elif has_email == "no":
            queryset = queryset.filter(Q(email__isnull=True) | Q(email=""))
        if has_phone == "yes":
            queryset = queryset.exclude(phone_number__isnull=True).exclude(phone_number="")
        elif has_phone == "no":
            queryset = queryset.filter(Q(phone_number__isnull=True) | Q(phone_number=""))
        if created_from:
            queryset = queryset.filter(created_at__date__gte=created_from)
        if created_to:
            queryset = queryset.filter(created_at__date__lte=created_to)
        return queryset.distinct()

    def _apply_inquiry_filters(self, queryset):
        if self.model is not Inquiry:
            return queryset

        status = (self.request.GET.get("status") or "").strip()
        assigned_user = (self.request.GET.get("assigned_user") or "").strip()
        channel = (self.request.GET.get("channel") or "").strip()
        recipient_scope = (self.request.GET.get("recipient_scope") or "").strip().lower()
        created_from = (self.request.GET.get("created_from") or "").strip()
        created_to = (self.request.GET.get("created_to") or "").strip()

        if status:
            queryset = queryset.filter(status=status)
        if assigned_user.isdigit():
            queryset = queryset.filter(assigned_to_id=int(assigned_user))
        if channel:
            queryset = queryset.filter(channel=channel)
        if recipient_scope == "student":
            queryset = queryset.filter(student__isnull=False)
        elif recipient_scope == "prospect":
            queryset = queryset.filter(prospect__isnull=False)
        if created_from:
            queryset = queryset.filter(inquiry_date__date__gte=created_from)
        if created_to:
            queryset = queryset.filter(inquiry_date__date__lte=created_to)
        return queryset.distinct()

    def _apply_communication_filters(self, queryset):
        if self.model is not Communication:
            return queryset

        status = (self.request.GET.get("status") or "").strip()
        assigned_user = (self.request.GET.get("assigned_user") or "").strip()
        channel = (self.request.GET.get("channel") or "").strip()
        recipient_type = (self.request.GET.get("recipient_type") or "").strip()
        communication_type = (self.request.GET.get("communication_type") or "").strip()
        course = (self.request.GET.get("course") or "").strip()
        created_from = (self.request.GET.get("created_from") or "").strip()
        created_to = (self.request.GET.get("created_to") or "").strip()

        if status:
            queryset = queryset.filter(delivery_status=status)
        if assigned_user.isdigit():
            queryset = queryset.filter(owner_id=int(assigned_user))
        if channel:
            queryset = queryset.filter(channel=channel)
        if recipient_type:
            queryset = queryset.filter(recipient_type=recipient_type)
        if communication_type:
            queryset = queryset.filter(communication_type=communication_type)
        if course.isdigit():
            queryset = queryset.filter(enrollment__course_id=int(course))
        if created_from:
            queryset = queryset.filter(created_at__date__gte=created_from)
        if created_to:
            queryset = queryset.filter(created_at__date__lte=created_to)
        return queryset.distinct()

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = scope_queryset_for_user(
            queryset=queryset,
            model=self.model,
            user=self.request.user,
        )
        if self.model is Prospect:
            queryset = queryset.select_related("contact", "teacher", "owner")
            queryset = self._apply_prospect_filters(queryset)
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
        if self.model is Inquiry:
            queryset = queryset.select_related("student__prospect__contact", "prospect__contact", "assigned_to")
            queryset = self._apply_inquiry_filters(queryset)
        if self.model is Communication:
            queryset = queryset.select_related(
                "student__prospect__contact",
                "prospect__contact",
                "owner",
                "enrollment__course",
            )
            queryset = self._apply_communication_filters(queryset)
        if self.model is Contact:
            queryset = self._apply_contact_filters(queryset)
        if self.model is Student:
            queryset = queryset.select_related(
                "owner",
                "prospect__contact",
                "teacher",
            )
            queryset = self._apply_student_filters(queryset)
        if self.model is Enrollment:
            queryset = queryset.select_related(
                "student__prospect__contact",
                "course",
                "session",
                "invoice",
            )
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
        if self.model is Student:
            UserModel = get_user_model()
            scoped_students = scope_queryset_for_user(
                queryset=Student.objects.select_related("owner"),
                model=Student,
                user=self.request.user,
            )
            owner_ids = list(
                scoped_students.exclude(owner__isnull=True)
                .values_list("owner_id", flat=True)
                .distinct()
            )
            context["student_status_choices"] = EnrollmentStatus.choices
            context["student_owner_choices"] = UserModel.objects.filter(id__in=owner_ids).order_by(
                "first_name", "last_name", "username"
            )
            context["student_course_choices"] = Course.objects.filter(
                sessions__enrollments__student__in=scoped_students
            ).distinct().order_by("name")
            context["student_filter_values"] = {
                "status": (self.request.GET.get("status") or "").strip(),
                "assigned_user": (self.request.GET.get("assigned_user") or "").strip(),
                "activity": (self.request.GET.get("activity") or "").strip().lower(),
                "course": (self.request.GET.get("course") or "").strip(),
                "created_from": (self.request.GET.get("created_from") or "").strip(),
                "created_to": (self.request.GET.get("created_to") or "").strip(),
            }
        if self.model is Prospect:
            attach_prospect_conversion_eligibility(context.get("object_list", []))
            UserModel = get_user_model()
            scoped_prospects = scope_queryset_for_user(
                queryset=Prospect.objects.select_related("owner", "course_interest"),
                model=Prospect,
                user=self.request.user,
            )
            owner_ids = list(
                scoped_prospects.exclude(owner__isnull=True)
                .values_list("owner_id", flat=True)
                .distinct()
            )
            context["prospect_owner_choices"] = UserModel.objects.filter(id__in=owner_ids).order_by(
                "first_name", "last_name", "username"
            )
            context["prospect_course_choices"] = Course.objects.filter(
                interested_prospects__in=scoped_prospects
            ).distinct().order_by("name")
            context["prospect_filter_values"] = {
                "status": (self.request.GET.get("status") or "").strip(),
                "assigned_user": (self.request.GET.get("assigned_user") or "").strip(),
                "activity": (self.request.GET.get("activity") or "").strip().lower(),
                "course": (self.request.GET.get("course") or "").strip(),
                "created_from": (self.request.GET.get("created_from") or "").strip(),
                "created_to": (self.request.GET.get("created_to") or "").strip(),
            }
        if self.model is Contact:
            context["contact_filter_values"] = {
                "conversion": (self.request.GET.get("conversion") or "").strip().lower(),
                "has_email": (self.request.GET.get("has_email") or "").strip().lower(),
                "has_phone": (self.request.GET.get("has_phone") or "").strip().lower(),
                "created_from": (self.request.GET.get("created_from") or "").strip(),
                "created_to": (self.request.GET.get("created_to") or "").strip(),
            }
        if self.model is Inquiry:
            UserModel = get_user_model()
            scoped_inquiries = scope_queryset_for_user(
                queryset=Inquiry.objects.select_related("assigned_to"),
                model=Inquiry,
                user=self.request.user,
            )
            assigned_ids = list(
                scoped_inquiries.exclude(assigned_to__isnull=True)
                .values_list("assigned_to_id", flat=True)
                .distinct()
            )
            context["inquiry_status_choices"] = InquiryStatus.choices
            context["inquiry_channel_choices"] = InquiryChannel.choices
            context["inquiry_assigned_user_choices"] = UserModel.objects.filter(id__in=assigned_ids).order_by(
                "first_name", "last_name", "username"
            )
            context["inquiry_filter_values"] = {
                "status": (self.request.GET.get("status") or "").strip(),
                "assigned_user": (self.request.GET.get("assigned_user") or "").strip(),
                "channel": (self.request.GET.get("channel") or "").strip(),
                "recipient_scope": (self.request.GET.get("recipient_scope") or "").strip().lower(),
                "created_from": (self.request.GET.get("created_from") or "").strip(),
                "created_to": (self.request.GET.get("created_to") or "").strip(),
            }
        if self.model is Communication:
            UserModel = get_user_model()
            scoped_comms = scope_queryset_for_user(
                queryset=Communication.objects.select_related("owner"),
                model=Communication,
                user=self.request.user,
            )
            owner_ids = list(
                scoped_comms.exclude(owner__isnull=True)
                .values_list("owner_id", flat=True)
                .distinct()
            )
            context["communication_delivery_status_choices"] = DeliveryStatus.choices
            context["communication_channel_choices"] = CommunicationChannel.choices
            context["communication_type_choices"] = CommunicationType.choices
            context["communication_recipient_type_choices"] = RecipientType.choices
            context["communication_owner_choices"] = UserModel.objects.filter(id__in=owner_ids).order_by(
                "first_name", "last_name", "username"
            )
            context["communication_course_choices"] = Course.objects.filter(
                enrollments__communications__in=scoped_comms
            ).distinct().order_by("name")
            context["communication_filter_values"] = {
                "status": (self.request.GET.get("status") or "").strip(),
                "assigned_user": (self.request.GET.get("assigned_user") or "").strip(),
                "channel": (self.request.GET.get("channel") or "").strip(),
                "recipient_type": (self.request.GET.get("recipient_type") or "").strip(),
                "communication_type": (self.request.GET.get("communication_type") or "").strip(),
                "course": (self.request.GET.get("course") or "").strip(),
                "created_from": (self.request.GET.get("created_from") or "").strip(),
                "created_to": (self.request.GET.get("created_to") or "").strip(),
            }
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

    def get_object(self, queryset=None):
        queryset = queryset or self.get_queryset()
        return self._resolve_scoped_object(queryset, self.kwargs.get("pk"))

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

    def get_object(self, queryset=None):
        queryset = queryset or self.get_queryset()
        return self._resolve_scoped_object(queryset, self.kwargs.get("pk"))

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

    def get_object(self, queryset=None):
        queryset = queryset or self.get_queryset()
        return self._resolve_scoped_object(queryset, self.kwargs.get("pk"))

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

    def _parse_recipients_payload(self):
        raw = (
            (self.request.POST.get("recipients") if self.request.method == "POST" else "")
            or self.request.GET.get("recipients")
            or ""
        ).strip()
        if not raw:
            return []
        parsed = []
        for token in [part.strip() for part in raw.split(",") if part.strip()]:
            if ":" not in token:
                continue
            kind, identifier = token.split(":", 1)
            kind = kind.strip().lower()
            identifier = identifier.strip()
            if kind and identifier:
                parsed.append((kind, identifier))
        return parsed

    def _build_targets_from_payload(self, parsed_tokens):
        targets = []
        invalid = []
        for kind, identifier in parsed_tokens:
            if kind == "prospect":
                obj = Prospect.objects.select_related("contact").filter(pk=identifier).first()
                if not obj:
                    invalid.append(f"{kind}:{identifier}")
                    continue
                email = (obj.contact.email or "").strip() if obj.contact_id else ""
                targets.append({"recipient_type": RecipientType.PROSPECT, "prospect": obj, "student": None, "email": email, "label": str(obj)})
                continue
            if kind == "student":
                obj = Student.objects.select_related("prospect__contact").filter(pk=identifier).first()
                if not obj:
                    invalid.append(f"{kind}:{identifier}")
                    continue
                email = (
                    (obj.prospect.contact.email or "").strip()
                    if obj.prospect_id and obj.prospect.contact_id
                    else ""
                )
                targets.append({"recipient_type": RecipientType.STUDENT, "prospect": None, "student": obj, "email": email, "label": str(obj.prospect if obj.prospect_id else obj)})
                continue
            if kind == "contact":
                obj = Contact.objects.filter(pk=identifier).first()
                if not obj:
                    invalid.append(f"{kind}:{identifier}")
                    continue
                prospect = getattr(obj, "prospect", None)
                if prospect is None:
                    invalid.append(f"{kind}:{identifier} (no linked prospect)")
                    continue
                email = (obj.email or "").strip()
                targets.append({"recipient_type": RecipientType.PROSPECT, "prospect": prospect, "student": None, "email": email, "label": str(obj)})
                continue
            if kind == "meditator":
                obj = Meditator.objects.select_related(
                    "student__prospect__contact"
                ).filter(pk=identifier, is_active=True).first()
                if not obj:
                    invalid.append(f"{kind}:{identifier}")
                    continue
                student = obj.student
                email = (
                    (student.prospect.contact.email or "").strip()
                    if student and student.prospect_id and student.prospect.contact_id
                    else ""
                )
                targets.append({"recipient_type": RecipientType.STUDENT, "prospect": None, "student": student, "email": email, "label": str(student.prospect if student and student.prospect_id else student)})
                continue
            if kind == "inquiry":
                obj = Inquiry.objects.select_related("student__prospect__contact", "prospect__contact").filter(pk=identifier).first()
                if not obj:
                    invalid.append(f"{kind}:{identifier}")
                    continue
                if obj.student_id:
                    student = obj.student
                    email = (
                        (student.prospect.contact.email or "").strip()
                        if student.prospect_id and student.prospect.contact_id
                        else ""
                    )
                    targets.append({"recipient_type": RecipientType.STUDENT, "prospect": None, "student": student, "email": email, "label": str(student.prospect if student.prospect_id else student)})
                elif obj.prospect_id:
                    prospect = obj.prospect
                    email = (prospect.contact.email or "").strip() if prospect.contact_id else ""
                    targets.append({"recipient_type": RecipientType.PROSPECT, "prospect": prospect, "student": None, "email": email, "label": str(prospect)})
                else:
                    invalid.append(f"{kind}:{identifier} (no recipient linked)")
                continue
            invalid.append(f"{kind}:{identifier}")
        return targets, invalid

    def _build_single_target_from_form(self, communication):
        recipient_email = ""
        label = "-"
        if communication.recipient_type == "prospect" and communication.prospect_id:
            recipient_email = (communication.prospect.contact.email or "").strip()
            label = str(communication.prospect)
            return [{"recipient_type": RecipientType.PROSPECT, "prospect": communication.prospect, "student": None, "email": recipient_email, "label": label}], []
        if communication.recipient_type == "student" and communication.student_id:
            recipient_email = (communication.student.prospect.contact.email or "").strip()
            label = str(communication.student.prospect)
            return [{"recipient_type": RecipientType.STUDENT, "prospect": None, "student": communication.student, "email": recipient_email, "label": label}], []
        return [], ["No recipient selected."]

    def get_initial(self):
        initial = super().get_initial()
        recipient_type = (self.request.GET.get("recipient_type") or "").strip().lower()
        student_id = (self.request.GET.get("student") or "").strip()
        prospect_id = (self.request.GET.get("prospect") or "").strip()
        enrollment_id = (self.request.GET.get("enrollment") or "").strip()

        if recipient_type in {"prospect", "student"}:
            initial["recipient_type"] = recipient_type
        if student_id:
            initial["recipient_type"] = "student"
            initial["student"] = student_id
        elif prospect_id:
            initial["recipient_type"] = "prospect"
            initial["prospect"] = prospect_id

        if enrollment_id.isdigit():
            initial["enrollment"] = int(enrollment_id)
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["allow_recipient_override"] = bool(self._parse_recipients_payload())
        return kwargs

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

        if prospect_id:
            prospect = Prospect.objects.select_related("contact").filter(pk=prospect_id).first()
        if student_id:
            student = Student.objects.select_related("prospect__contact").filter(pk=student_id).first()
            if student and not prospect:
                prospect = student.prospect

        context["recipient_prospect"] = prospect
        context["recipient_student"] = student
        context["recipient_contact"] = prospect.contact if prospect and prospect.contact_id else None
        parsed = self._parse_recipients_payload()
        targets, invalid_tokens = self._build_targets_from_payload(parsed)
        context["recipient_preview_count"] = len(targets)
        context["recipient_invalid_tokens"] = invalid_tokens
        context["recipient_sender_email"] = (
            (self.request.user.email or "").strip()
            or getattr(settings, "DEFAULT_FROM_EMAIL", "rokosun@tm.org")
        )
        context["recipients_payload"] = ",".join(f"{k}:{v}" for k, v in parsed)
        return context

    def form_valid(self, form):
        if not (form.cleaned_data.get("subject") or "").strip():
            form.add_error("subject", "Subject is required.")
            return self.form_invalid(form)
        if not (form.cleaned_data.get("body") or "").strip():
            form.add_error("body", "Message body is required.")
            return self.form_invalid(form)

        parsed = self._parse_recipients_payload()
        if parsed:
            targets, invalid_tokens = self._build_targets_from_payload(parsed)
        else:
            draft = form.save(commit=False)
            targets, invalid_tokens = self._build_single_target_from_form(draft)

        valid_targets = [t for t in targets if t.get("email")]
        skipped_no_email = [t for t in targets if not t.get("email")]
        if not valid_targets:
            form.add_error(None, "No selected recipients with valid email addresses were found.")
            return self.form_invalid(form)

        sender_email = (
            (self.request.user.email or "").strip()
            or getattr(settings, "DEFAULT_FROM_EMAIL", "rokosun@tm.org")
        )
        sent_count = 0
        failed_count = 0
        self.object = None
        for target in valid_targets:
            communication = form.save(commit=False)
            communication.pk = None
            communication.owner = self.request.user
            communication.channel = CommunicationChannel.EMAIL
            communication.recipient_type = target["recipient_type"]
            communication.prospect = target["prospect"]
            communication.student = target["student"]
            communication.delivery_status = DeliveryStatus.SENDING
            try:
                EmailMessage(
                    subject=communication.subject,
                    body=communication.body,
                    from_email=sender_email,
                    to=[target["email"]],
                ).send(fail_silently=False)
                communication.delivery_status = DeliveryStatus.SENT
                communication.provider_status = communication.provider_status or "outbound"
                sent_count += 1
            except Exception:
                communication.delivery_status = DeliveryStatus.FAILED
                communication.provider_status = communication.provider_status or "send_failed"
                failed_count += 1
            communication.sent_at = timezone.now()
            communication.save()
            if self.object is None:
                self.object = communication

        if sent_count == 0:
            form.add_error(None, "No messages were sent successfully.")
            return self.form_invalid(form)

        skipped_count = len(skipped_no_email) + len(invalid_tokens)
        if failed_count == 0 and skipped_count == 0:
            messages.success(self.request, f"Message sent to {sent_count} recipient(s).")
        else:
            messages.warning(
                self.request,
                f"Sent: {sent_count}, Failed: {failed_count}, Skipped: {skipped_count}.",
            )
        return redirect(self.get_success_url())


class CommunicationUpdateView(BaseUpdateView):
    model = Communication
    form_class = CommunicationForm
    fields = None


class ProspectCreateView(BaseCreateView):
    model = Prospect
    form_class = ProspectForm
    fields = None

    def form_valid(self, form):
        if not form.instance.owner_id:
            form.instance.owner = self.request.user
        return super().form_valid(form)


class ProspectUpdateView(BaseUpdateView):
    model = Prospect
    form_class = ProspectForm
    fields = None

    def form_valid(self, form):
        if not form.instance.owner_id:
            form.instance.owner = self.request.user
        return super().form_valid(form)


class EnrollmentCreateView(BaseCreateView):
    model = Enrollment
    form_class = EnrollmentForm
    fields = None

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request_user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        person_type = form.cleaned_data.get("person_type")
        selected_course = form.cleaned_data.get("course")
        selected_session = form.cleaned_data.get("session")
        resolved_student = None

        created_invoice = None
        try:
            with transaction.atomic():
                student = self._resolve_student_for_enrollment(form, person_type=person_type)
                resolved_student = student
                if selected_course:
                    validate_course_eligibility(student, selected_course)
                if selected_session and Enrollment.objects.filter(
                    student=student,
                    session=selected_session,
                ).exists():
                    existing_enrollment = Enrollment.objects.filter(
                        student=student,
                        session=selected_session,
                    ).order_by("-pk").first()
                    messages.info(
                        self.request,
                        "This enrollment already exists. Opened the existing record.",
                    )
                    return redirect("core:enrollment-detail", pk=existing_enrollment.pk)
                form.instance.student = student
                super().form_valid(form)
                created_invoice, _ = generate_invoice_for_enrollment(self.object)
        except ValidationError as exc:
            if selected_course:
                form.add_error("course", " ".join(exc.messages))
            else:
                form.add_error(None, " ".join(exc.messages))
            return self.form_invalid(form)
        except IntegrityError as exc:
            if resolved_student:
                existing_qs = Enrollment.objects.filter(student=resolved_student)
                if selected_session:
                    existing_qs = existing_qs.filter(session=selected_session)
                elif selected_course:
                    existing_qs = existing_qs.filter(session__course=selected_course)

                existing_enrollment = existing_qs.order_by("-pk").first()
                if existing_enrollment:
                    messages.info(
                        self.request,
                        "This enrollment already exists. Opened the existing record.",
                    )
                    return redirect("core:enrollment-detail", pk=existing_enrollment.pk)

            form.add_error(
                None,
                f"Unable to create enrollment due to a data constraint conflict: {exc}",
            )
            return self.form_invalid(form)
        except Exception as exc:
            form.add_error(None, f"Unable to create enrollment. Please review the form and try again. ({exc})")
            return self.form_invalid(form)

        if created_invoice is not None:
            messages.success(
                self.request,
                f"Enrollment completed and invoice {created_invoice.invoice_number} generated successfully.",
            )
            return redirect("core:invoice-detail", pk=created_invoice.pk)
        messages.success(self.request, "Enrollment completed successfully.")
        return redirect("core:invoice-list")

    def _resolve_student_for_enrollment(self, form, *, person_type):
        raw_contact_id = (self.request.POST.get("contact") or "").strip()
        raw_prospect_id = (self.request.POST.get("prospect") or "").strip()

        if person_type == "student":
            student = form.cleaned_data.get("student")
            if not student:
                raise ValidationError("Select an existing student.")
            return student

        if person_type == "prospect":
            prospect = form.cleaned_data.get("prospect")
            if not prospect:
                raise ValidationError("Select an existing prospect.")
            try:
                student, _ = get_or_create_student_enrollment_shell(prospect)
            except Prospect.DoesNotExist:
                raise ValidationError("Selected prospect no longer exists. Please search and select again.")
            except Student.DoesNotExist:
                raise ValidationError("Student conversion could not be completed for the selected prospect. Please try again.")
            return student

        if person_type == "contact":
            contact = form.cleaned_data.get("contact")
            if not contact and raw_contact_id.isdigit():
                contact = Contact.objects.filter(pk=int(raw_contact_id)).first()
            if not contact and raw_prospect_id.isdigit():
                # Graceful fallback when UI hidden fields are stale:
                # derive contact from selected prospect instead of failing hard.
                prospect_from_post = (
                    Prospect.objects.select_related("contact")
                    .filter(pk=int(raw_prospect_id))
                    .first()
                )
                if prospect_from_post and prospect_from_post.contact_id:
                    contact = prospect_from_post.contact
            if not contact:
                raise ValidationError("Select an existing contact.")
            try:
                prospect, _ = contact.convert_to_prospect(
                    owner=self.request.user if not self.request.user.is_superuser else None,
                    source="Enrollment Conversion",
                    notes="Created from Enrollment workflow.",
                )
                student, _ = get_or_create_student_enrollment_shell(prospect)
            except ValidationError:
                # Preserve precise domain validation messages (e.g., duplicate detection).
                raise
            except Prospect.DoesNotExist:
                raise ValidationError(
                    "Prospect conversion could not be completed for the selected contact. Please try again."
                )
            except Student.DoesNotExist:
                raise ValidationError(
                    "Student conversion could not be completed for the selected contact. Please try again."
                )
            return student

        first_name = (form.cleaned_data.get("new_first_name") or "").strip()
        last_name = (form.cleaned_data.get("new_last_name") or "").strip()
        email = (form.cleaned_data.get("new_email") or "").strip()
        phone = (form.cleaned_data.get("new_phone_number") or "").strip()
        source = (form.cleaned_data.get("new_source") or "").strip()
        notes = (form.cleaned_data.get("new_notes") or "").strip()

        contact, _ = Contact.get_or_create_from_identity(
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone_number=phone,
        )
        source_label = source or ("Enrollment New Contact" if person_type == "new_contact" else "Enrollment New Prospect")
        prospect, _ = contact.convert_to_prospect(
            owner=self.request.user if not self.request.user.is_superuser else None,
            source=source_label,
            notes=notes,
        )
        student, _ = get_or_create_student_enrollment_shell(prospect)
        return student


class EnrollmentUpdateView(BaseUpdateView):
    model = Enrollment
    form_class = EnrollmentForm
    fields = None

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request_user"] = self.request.user
        return kwargs


class InvoiceCreateView(BaseCreateView):
    model = Invoice

    def dispatch(self, request, *args, **kwargs):
        messages.info(
            request,
            "Invoices are now generated from enrollment submissions. Use the enrollment form to create an invoice.",
        )
        return redirect("core:enrollment-create")


class PaymentCreateView(BaseCreateView):
    model = Payment
    form_class = PaymentForm
    fields = None

    def get_initial(self):
        initial = super().get_initial()
        student_id = (self.request.GET.get("student") or "").strip()
        if student_id.isdigit():
            initial["student"] = int(student_id)
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request_user"] = self.request.user
        student_id = (
            (self.request.POST.get("student") if self.request.method == "POST" else self.request.GET.get("student"))
            or ""
        ).strip()
        kwargs["selected_student_id"] = int(student_id) if student_id.isdigit() else None
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = context.get("form")
        context["no_open_invoices"] = bool(getattr(form, "no_open_invoices", False))
        context["invoice_lookup_base_url"] = reverse_lazy("core:payment-invoices-for-student", kwargs={"student_id": 0})
        selected_student = None
        student_id = (
            (self.request.POST.get("student") if self.request.method == "POST" else self.request.GET.get("student"))
            or ""
        ).strip()
        if student_id.isdigit():
            selected_student = scope_queryset_for_user(
                queryset=Student.objects.select_related("prospect__contact"),
                model=Student,
                user=self.request.user,
            ).filter(pk=int(student_id)).first()
        context["selected_student"] = selected_student
        return context


class PaymentUpdateView(BaseUpdateView):
    model = Payment
    form_class = PaymentForm
    fields = None

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request_user"] = self.request.user
        kwargs["selected_student_id"] = self.object.invoice.enrollment.student_id if self.object else None
        return kwargs


class PaymentInvoicesForStudentView(ProductLoginRequiredMixin, View):
    def get(self, request, student_id):
        student = get_object_or_404(
            scope_queryset_for_user(
                queryset=Student.objects.select_related("prospect__contact"),
                model=Student,
                user=request.user,
            ),
            pk=student_id,
        )
        invoices = PaymentForm._open_invoice_queryset(user=request.user, student_id=student.pk)
        data = [
            {
                "id": invoice.pk,
                "label": PaymentForm._invoice_label(invoice),
            }
            for invoice in invoices
        ]
        return JsonResponse({"student_id": student.pk, "invoices": data})


class PaymentStudentSearchView(ProductLoginRequiredMixin, View):
    PAGE_SIZE = 15

    def get(self, request):
        query = (request.GET.get("q") or "").strip()
        if len(query) < 2:
            return JsonResponse({"results": []})

        open_invoice_student_ids = PaymentForm._open_invoice_queryset(
            user=request.user,
        ).values_list("enrollment__student_id", flat=True)
        students = (
            scope_queryset_for_user(
                queryset=Student.objects.select_related("prospect__contact"),
                model=Student,
                user=request.user,
            )
            .filter(pk__in=open_invoice_student_ids)
            .annotate(
                search_full_name=Concat(
                    "prospect__contact__first_name",
                    Value(" "),
                    "prospect__contact__last_name",
                )
            )
            .filter(
                Q(search_full_name__icontains=query)
                | Q(prospect__contact__first_name__icontains=query)
                | Q(prospect__contact__last_name__icontains=query)
                | Q(prospect__contact__email__icontains=query)
                | Q(prospect__contact__phone_number__icontains=query)
            )
            .order_by(
                "prospect__contact__first_name",
                "prospect__contact__last_name",
                "pk",
            )
            .distinct()[: self.PAGE_SIZE]
        )
        return JsonResponse(
            {
                "results": [
                    {
                        "id": student.pk,
                        "label": f"{student.first_name} {student.last_name}".strip(),
                        "context": " | ".join(
                            value
                            for value in (
                                f"Prospect #{student.prospect_id}",
                                student.email,
                                student.phone,
                            )
                            if value
                        ),
                    }
                    for student in students
                ]
            }
        )


class EnrollmentPersonSearchView(ProductLoginRequiredMixin, View):
    PAGE_SIZE = 15

    def get(self, request):
        person_type = (request.GET.get("type") or "").strip().lower()
        query = (request.GET.get("q") or "").strip()
        if len(query) < 2:
            return JsonResponse({"results": []})

        if person_type == "student":
            course_id = (request.GET.get("course_id") or "").strip()
            session_id = (request.GET.get("session_id") or "").strip()
            if not course_id.isdigit():
                return JsonResponse(
                    {
                        "results": [],
                        "message": "Select a course before searching for a student.",
                    }
                )
            course = scope_queryset_for_user(
                queryset=Course.objects.filter(status="active"),
                model=Course,
                user=request.user,
            ).filter(pk=int(course_id)).first()
            if course is None:
                return JsonResponse(
                    {"results": [], "message": "Selected course was not found."},
                    status=404,
                )

            session = None
            if session_id:
                if not session_id.isdigit():
                    return JsonResponse(
                        {"results": [], "message": "Selected session is invalid."},
                        status=400,
                    )
                session = scope_queryset_for_user(
                    queryset=CourseSession.objects.all(),
                    model=CourseSession,
                    user=request.user,
                ).filter(pk=int(session_id), course=course).first()
                if session is None:
                    return JsonResponse(
                        {
                            "results": [],
                            "message": "Selected session does not belong to this course.",
                        },
                        status=400,
                    )

            queryset = scope_queryset_for_user(
                queryset=Student.objects.select_related("prospect__contact"),
                model=Student,
                user=request.user,
            ).annotate(
                search_full_name=Concat(
                    "prospect__contact__first_name",
                    Value(" "),
                    "prospect__contact__last_name",
                )
            ).filter(
                Q(search_full_name__icontains=query)
                | Q(prospect__contact__first_name__icontains=query)
                | Q(prospect__contact__last_name__icontains=query)
                | Q(prospect__contact__email__icontains=query)
                | Q(prospect__contact__phone_number__icontains=query)
            ).order_by(
                "prospect__contact__first_name",
                "prospect__contact__last_name",
                "pk",
            )
            if session is not None:
                queryset = queryset.exclude(enrollments__session=session)

            eligible_students = []
            for student in queryset[: self.PAGE_SIZE * 5]:
                if is_eligible_for_course(student, course):
                    eligible_students.append(student)
                if len(eligible_students) == self.PAGE_SIZE:
                    break
            results = [
                {
                    "id": obj.pk,
                    "label": f"{obj.prospect.contact.first_name} {obj.prospect.contact.last_name}".strip(),
                    "meta": " | ".join(
                        value
                        for value in (obj.prospect.contact.email, obj.prospect.contact.phone_number)
                        if value
                    ),
                    "badge": f"Student | Prospect #{obj.prospect_id}",
                }
                for obj in eligible_students
            ]
            return JsonResponse({"results": results})

        if person_type == "prospect":
            queryset = scope_queryset_for_user(
                queryset=Prospect.objects.select_related("contact"),
                model=Prospect,
                user=request.user,
            ).filter(is_archived=False).filter(
                Q(contact__first_name__icontains=query)
                | Q(contact__last_name__icontains=query)
                | Q(contact__email__icontains=query)
                | Q(contact__phone_number__icontains=query)
            )[: self.PAGE_SIZE]
            results = [
                {
                    "id": obj.pk,
                    "label": f"{obj.contact.first_name} {obj.contact.last_name}".strip(),
                    "meta": obj.contact.email or obj.contact.phone_number or "",
                    "badge": "Prospect",
                }
                for obj in queryset
            ]
            return JsonResponse({"results": results})

        if person_type == "contact":
            queryset = scope_queryset_for_user(
                queryset=Contact.objects.all(),
                model=Contact,
                user=request.user,
            ).filter(
                Q(first_name__icontains=query)
                | Q(last_name__icontains=query)
                | Q(email__icontains=query)
                | Q(phone_number__icontains=query)
            )[: self.PAGE_SIZE]
            results = [
                {
                    "id": obj.pk,
                    "label": f"{obj.first_name} {obj.last_name}".strip(),
                    "meta": obj.email or obj.phone_number or "",
                    "badge": "Contact",
                }
                for obj in queryset
            ]
            return JsonResponse({"results": results})

        return JsonResponse({"results": []})


class ContactAutocompleteView(ProductLoginRequiredMixin, View):
    PAGE_SIZE = 20

    def get(self, request):
        query = (request.GET.get("q") or "").strip()
        if len(query) < 2:
            return JsonResponse({"results": []})

        contacts = scope_queryset_for_user(
            queryset=Contact.objects.all(),
            model=Contact,
            user=request.user,
        ).filter(
            Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(email__icontains=query)
            | Q(phone_number__icontains=query)
        ).order_by("first_name", "last_name")[: self.PAGE_SIZE]

        return JsonResponse(
            {
                "results": [
                    {
                        "id": contact.pk,
                        "name": f"{contact.first_name} {contact.last_name}".strip(),
                        "email": contact.email or "",
                        "phone": contact.phone_number or "",
                    }
                    for contact in contacts
                ]
            }
        )


class EnrollmentSessionsForCourseView(ProductLoginRequiredMixin, View):
    def get(self, request, course_id):
        course = get_object_or_404(
            scope_queryset_for_user(
                queryset=Course.objects.filter(status="active"),
                model=Course,
                user=request.user,
            ),
            pk=course_id,
        )
        sessions = scope_queryset_for_user(
            queryset=CourseSession.objects.select_related("course", "teacher", "location").filter(course=course),
            model=CourseSession,
            user=request.user,
        ).order_by("-start_date")[:50]
        data = [
            {
                "id": session.pk,
                "label": f"{session.session_name or session.start_date.strftime('%Y-%m-%d')} | {session.teacher} | {session.location}",
            }
            for session in sessions
        ]
        return JsonResponse(
            {
                "course_id": course.pk,
                "sessions": data,
                "standard_fee": f"{course.standard_fee:.2f}",
                "course_name": course.name,
                "course_code": getattr(course, "code", "") or "-",
                "additional_cost_description": getattr(course, "additional_cost_description", "")
                or (course.description[:160] if course.description else ""),
            }
        )


class EnrollmentEligibilityCheckView(ProductLoginRequiredMixin, View):
    def _resolve_person(self, request):
        person_type = (request.GET.get("person_type") or "").strip().lower()
        person_id = (request.GET.get("person_id") or "").strip()
        if not person_id.isdigit():
            return None
        pk = int(person_id)

        if person_type == "student":
            return scope_queryset_for_user(
                queryset=Student.objects.all(),
                model=Student,
                user=request.user,
            ).filter(pk=pk).first()
        if person_type == "prospect":
            return scope_queryset_for_user(
                queryset=Prospect.objects.select_related("student_record"),
                model=Prospect,
                user=request.user,
            ).filter(pk=pk).first()
        if person_type == "contact":
            return scope_queryset_for_user(
                queryset=Contact.objects.select_related("prospect__student_record"),
                model=Contact,
                user=request.user,
            ).filter(pk=pk).first()
        return None

    def get(self, request):
        course_id = (request.GET.get("course_id") or "").strip()
        if not course_id.isdigit():
            return JsonResponse({"eligible": True, "missing": [], "message": "Select a course."})
        course = scope_queryset_for_user(
            queryset=Course.objects.all(),
            model=Course,
            user=request.user,
        ).filter(pk=int(course_id)).first()
        if not course:
            return JsonResponse({"eligible": False, "missing": [], "message": "Selected course was not found."}, status=404)

        person = self._resolve_person(request)
        result = check_course_eligibility(person, course)
        return JsonResponse(
            {
                "eligible": result.eligible,
                "missing": result.missing,
                "message": result.message,
            }
        )


class StudentCreateView(BaseCreateView):
    model = Student
    form_class = StudentCreateForm
    fields = None


class StudentUpdateView(BaseUpdateView):
    model = Student
    form_class = StudentForm
    fields = None


class StudentArchiveView(ProductLoginRequiredMixin, View):
    def post(self, request, pk):
        student = _resolve_object_by_pk_or_uuid(
            queryset=scope_queryset_for_user(
                queryset=Student.objects.all(),
                model=Student,
                user=request.user,
            ),
            model=Student,
            identifier=pk,
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
        context["student_completion_financials"] = check_student_completion_financials(student)
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
                    Sum(
                        "payments__amount_paid",
                        filter=Q(
                            payments__confirmation_status=PaymentConfirmationStatus.CONFIRMED,
                        ),
                    ),
                    Value(Decimal("0.00")),
                    output_field=DecimalField(max_digits=10, decimal_places=2),
                )
            )
            .annotate(
                calculated_balance_due=ExpressionWrapper(
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
                    Sum("calculated_balance_due"),
                    Value(Decimal("0.00")),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                )
            )["total"]
            or Decimal("0.00")
        )
        for invoice in invoices:
            if invoice.calculated_balance_due <= Decimal("0.00"):
                invoice.computed_payment_status = "Paid"
            elif invoice.amount_paid > Decimal("0.00"):
                invoice.computed_payment_status = "Partial"
            else:
                invoice.computed_payment_status = "Unpaid"
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
                f"{payment.get_payment_method_display()} · GHS {payment.amount_paid.quantize(Decimal('0.01'))}",
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
            event_timestamp = communication.sent_at or communication.created_at
            timeline_events.append(
                {
                    "event_datetime": event_timestamp,
                    "title": communication.get_communication_type_display(),
                    "date": event_timestamp,
                    "author": "CRM",
                    "note": (
                        f"{communication.get_delivery_status_display()} · "
                        f"{communication.subject or communication.body[:120] or '-'}"
                    ),
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
                            f"{invoice.calculated_balance_due.quantize(Decimal('0.01'))}"
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
        overdue_exists = invoices.filter(calculated_balance_due__gt=0, due_date__lt=today).exists()
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
        latest_communication = prospect.communications.order_by("-sent_at", "-created_at", "-pk").first()
        latest_follow_up = (
            prospect.communications.filter(communication_type="follow_up")
            .order_by("-created_at", "-pk")
            .first()
        )
        has_student_record = hasattr(prospect, "student_record")
        is_converted = bool(
            prospect.converted_to_student
            or prospect.converted_student_id
            or prospect.status == ProspectStatus.CONVERTED
        )
        primary_fields = [
            ("Full Name", str(prospect) or "-"),
            ("Phone Number", prospect.phone or "-"),
            ("Email", prospect.email or "-"),
            ("Source", prospect.source or "-"),
            ("Status", prospect.get_status_display() or "-"),
            ("Assigned Teacher", str(prospect.teacher) if prospect.teacher_id else "-"),
            ("Assigned User", str(prospect.owner) if prospect.owner_id else "-"),
            ("Interest", prospect.get_interest_level_display() or "-"),
            (
                "Last Contacted",
                (latest_communication.sent_at or latest_communication.created_at)
                if latest_communication
                else None,
            ),
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
        context["is_converted"] = is_converted
        context["conversion_eligibility"] = get_prospect_conversion_eligibility(prospect)
        context["contact_attempt_count"] = prospect.contact_attempt_count
        context["communications"] = prospect.communications.order_by("-sent_at", "-created_at", "-pk")
        return context


class InvoiceDetailView(BaseDetailView):
    model = Invoice

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        invoice = self.object
        context["invoice_student"] = invoice.enrollment.student
        context["payments"] = invoice.payments.order_by("-payment_date", "-pk")
        context["total_paid"] = invoice.total_paid
        context["outstanding_balance"] = invoice.balance_due
        context["payment_status"] = invoice.payment_status
        context["can_add_payment"] = invoice.balance_due > Decimal("0.00")
        return context


@login_required(login_url="/login/")
def download_invoice_pdf(request, pk):
    scoped_invoices = scope_queryset_for_user(
        queryset=Invoice.objects.select_related(
            "enrollment__student__prospect__contact",
            "enrollment__course",
        ),
        model=Invoice,
        user=request.user,
    )
    invoice = get_object_or_404(scoped_invoices, pk=pk)

    try:
        participant = invoice.enrollment.student
        logo_path = Path(__file__).resolve().parent / "static" / "core" / "images" / "tmis-logo.svg"
        pdf_bytes = build_invoice_pdf(
            invoice=invoice,
            participant=participant,
            logo_svg_path=logo_path,
        )
        pdf_filename = f"invoice-{invoice.invoice_number}.pdf"
        invoice.pdf_file.save(pdf_filename, ContentFile(pdf_bytes), save=False)
        invoice.save(update_fields=["pdf_file"])
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{pdf_filename}"'
        return response
    except Exception as exc:
        logger.exception("Invoice PDF generation failed for invoice=%s", invoice.pk)
        base_msg = "Unable to generate invoice PDF right now."
        if settings.DEBUG:
            messages.error(request, f"{base_msg} {type(exc).__name__}: {exc}")
        else:
            messages.error(request, base_msg + " Please try again.")
        return redirect("core:invoice-detail", pk=invoice.pk)


@require_POST
@login_required(login_url="/login/")
def resend_invoice_email(request, pk):
    scoped_invoices = scope_queryset_for_user(
        queryset=Invoice.objects.select_related(
            "enrollment__student__prospect__contact",
            "enrollment__course",
        ),
        model=Invoice,
        user=request.user,
    )
    invoice = get_object_or_404(scoped_invoices, pk=pk)

    if send_invoice_email(invoice):
        messages.success(request, f"Invoice email sent for {invoice.invoice_number}.")
    else:
        messages.error(request, f"Invoice email could not be sent for {invoice.invoice_number}.")
    return redirect("core:invoice-detail", pk=invoice.pk)


def _recalculate_invoice_status(invoice: Invoice) -> None:
    total_paid = (
        invoice.payments.filter(
            confirmation_status=PaymentConfirmationStatus.CONFIRMED,
        ).aggregate(
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
        invoice.payments.filter(
            confirmation_status=PaymentConfirmationStatus.CONFIRMED,
        ).aggregate(
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
