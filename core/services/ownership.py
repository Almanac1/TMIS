from __future__ import annotations

from django.db.models import Q
from django.db.models import QuerySet

from core.models import (
    Communication,
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
    MeditatorTransitionEvent,
    Payment,
    Prospect,
    Student,
    Teacher,
    TeacherSpecialization,
)


# Direct owner is preferred for top-level tenant entities.
# Some dependent records inherit ownership from parent entities to avoid duplicate sources of truth.
OWNERSHIP_FILTERS = {
    Prospect: "owner",
    Student: "owner",
    CourseSession: "owner",
    Inquiry: "owner",
    Invoice: "owner",
    Payment: "owner",
    Communication: "owner",
    Contact: lambda user: Q(),
    Enrollment: "student__owner",
    InterviewForm: "student__owner",
    Disbursement: "enrollment__student__owner",
    Teacher: "user",
    Meditator: "student__owner",
    MeditatorTransitionEvent: "student__owner",
}

# Shared catalog/reference entities are global for authenticated product users.
PUBLIC_MODELS = {
    Course,
    TeacherSpecialization,
    Location,
}


def scope_queryset_for_user(*, queryset: QuerySet, model, user) -> QuerySet:
    """Apply tenant scoping. Superusers/staff see all records."""
    if not user or not getattr(user, "is_authenticated", False):
        return queryset.none()
    if user.is_superuser or user.is_staff:
        return queryset

    if model in PUBLIC_MODELS:
        return queryset

    owner_lookup = OWNERSHIP_FILTERS.get(model)
    if not owner_lookup:
        return queryset.none()
    if callable(owner_lookup):
        return queryset.filter(owner_lookup(user)).distinct()
    return queryset.filter(**{owner_lookup: user})
