from django.db import transaction
from django.utils import timezone

from core.models import InquiryNumberSequence


def generate_inquiry_number(inquiry_date=None) -> str:
    """Return the next concurrency-safe, staff-facing Inquiry reference."""

    effective_date = inquiry_date or timezone.now()
    if timezone.is_aware(effective_date):
        effective_date = timezone.localtime(effective_date)
    year = effective_date.year

    with transaction.atomic():
        sequence, _ = (
            InquiryNumberSequence.objects.select_for_update().get_or_create(
                year=year,
                defaults={"last_number": 0},
            )
        )
        sequence.last_number += 1
        sequence.save(update_fields=["last_number"])
        return f"INQ-{year}-{sequence.last_number:04d}"
