"""Shared counting semantics for CRM reports.

Lifecycle reports count their domain rows. Person reports count the canonical
Contact reached through those rows. Historical rows without a Contact are
counted individually so legacy data does not silently disappear from reports.
"""


def count_lifecycle_records(queryset) -> int:
    """Count distinct domain rows, even when joins expand the queryset."""
    return queryset.values("pk").distinct().count()


def count_unique_people(queryset, *, contact_field: str) -> int:
    """Count canonical Contacts plus individual unlinked legacy rows."""
    linked_people = (
        queryset.exclude(**{f"{contact_field}__isnull": True})
        .values(contact_field)
        .distinct()
        .count()
    )
    unlinked_legacy_rows = (
        queryset.filter(**{f"{contact_field}__isnull": True})
        .values("pk")
        .distinct()
        .count()
    )
    return linked_people + unlinked_legacy_rows
