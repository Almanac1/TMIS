from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("login/", views.EmailLoginView.as_view(), name="login"),
    path("", views.HomeView.as_view(), name="home"),
    path("meditators/", views.MeditatorListView.as_view(), name="meditator-list"),
    path(
        "pipeline/prospects/dashboard/",
        views.ProspectDashboardView.as_view(),
        name="prospect-dashboard",
    ),
    path(
        "pipeline/prospects/",
        views.ProspectPipelineListView.as_view(),
        name="prospect-pipeline-list",
    ),
    path(
        "pipeline/prospects/<int:pk>/",
        views.ProspectPipelineDetailView.as_view(),
        name="prospect-pipeline-detail",
    ),
    path(
        "pipeline/prospects/<int:pk>/convert/",
        views.ProspectConvertToStudentView.as_view(),
        name="prospect-pipeline-convert",
    ),
    path(
        "pipeline/prospects/<int:pk>/follow-up/",
        views.ProspectFollowUpCreateView.as_view(),
        name="prospect-pipeline-follow-up",
    ),
    path(
        "reporting/teacher-earnings/",
        views.TeacherEarningsDashboardView.as_view(),
        name="teacher-earnings-dashboard",
    ),
    path(
        "reporting/disbursements/",
        views.DisbursementReportingView.as_view(),
        name="disbursement-reporting",
    ),
    path(
        "students/<int:pk>/archive/",
        views.StudentArchiveView.as_view(),
        name="student-archive",
    ),
    path(
        "invoices/<int:pk>/add-payment/",
        views.add_invoice_payment,
        name="add_invoice_payment",
    ),
    path(
        "contacts/<int:pk>/convert-to-prospect/",
        views.ContactConvertToProspectView.as_view(),
        name="contact-convert-to-prospect",
    ),
    path(
        "prospects/<int:pk>/convert-to-student/",
        views.ProspectListConvertToStudentView.as_view(),
        name="prospect-convert-to-student",
    ),
    path(
        "payments/invoices-for-student/<int:student_id>/",
        views.PaymentInvoicesForStudentView.as_view(),
        name="payment-invoices-for-student",
    ),
    path(
        "enrollments/person-search/",
        views.EnrollmentPersonSearchView.as_view(),
        name="enrollment-person-search",
    ),
    path(
        "enrollments/sessions-for-course/<int:course_id>/",
        views.EnrollmentSessionsForCourseView.as_view(),
        name="enrollment-sessions-for-course",
    ),
    path(
        "contacts/autocomplete/",
        views.ContactAutocompleteView.as_view(),
        name="contact-autocomplete",
    ),
]


def _model_collection_path(model):
    """
    Build a clean plural URL segment per model.
    Examples: student -> students, inquiry -> inquiries.
    """
    base = model._meta.model_name.replace("_", "-")
    if base.endswith("y"):
        return f"{base[:-1]}ies"
    if base.endswith("s"):
        return f"{base}es"
    return f"{base}s"


for model in views.CRUD_MODELS:
    model_name = model.__name__
    slug = model._meta.model_name
    collection = _model_collection_path(model)
    model_ui_options = views.get_model_ui_options(model)
    model_patterns = [
        path(
            f"{collection}/",
            getattr(views, f"{model_name}ListView").as_view(),
            name=f"{slug}-list",
        ),
        path(
            f"{collection}/create/",
            getattr(views, f"{model_name}CreateView").as_view(),
            name=f"{slug}-create",
        ),
        path(
            f"{collection}/<int:pk>/",
            getattr(views, f"{model_name}DetailView").as_view(),
            name=f"{slug}-detail",
        ),
        path(
            f"{collection}/<int:pk>/edit/",
            getattr(views, f"{model_name}UpdateView").as_view(),
            name=f"{slug}-update",
        ),
    ]
    if model_ui_options["allow_delete"]:
        model_patterns.append(
            path(
                f"{collection}/<int:pk>/delete/",
                getattr(views, f"{model_name}DeleteView").as_view(),
                name=f"{slug}-delete",
            )
        )
    urlpatterns += model_patterns
