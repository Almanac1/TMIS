from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone


REFERENCE_PREFIXES = {
    "contact": "CNT",
    "prospect": "PRO",
    "student": "STU",
    "meditator": "MED",
}


def _reference_year(reference_date=None) -> int:
    effective_date = reference_date or timezone.now()
    if timezone.is_aware(effective_date):
        effective_date = timezone.localtime(effective_date)
    return effective_date.year


def generate_crm_reference(*, model, prefix: str, reference_date=None) -> str:
    """Allocate a unique, staff-facing CRM reference without exposing a PK or UUID."""

    from core.models import CRMReferenceSequence

    normalized_prefix = (prefix or "").strip().upper()
    if normalized_prefix not in REFERENCE_PREFIXES.values():
        raise ValueError(f"Unsupported CRM reference prefix: {normalized_prefix!r}")

    year = _reference_year(reference_date)
    # The retry handles two transactions attempting to create a year's first
    # sequence row at the same time. Existing rows are serialized by the lock.
    for _attempt in range(3):
        try:
            with transaction.atomic():
                sequence, _created = (
                    CRMReferenceSequence.objects.select_for_update().get_or_create(
                        prefix=normalized_prefix,
                        year=year,
                        defaults={"last_number": 0},
                    )
                )
                next_number = sequence.last_number + 1
                candidate = f"{normalized_prefix}-{year}-{next_number:04d}"
                while model.objects.filter(crm_reference=candidate).exists():
                    next_number += 1
                    candidate = f"{normalized_prefix}-{year}-{next_number:04d}"
                sequence.last_number = next_number
                sequence.save(update_fields=["last_number"])
                return candidate
        except IntegrityError:
            continue
    raise RuntimeError(
        f"Could not allocate a {normalized_prefix} CRM reference after concurrent retries."
    )


def prepare_crm_reference(instance, *, prefix: str, reference_date=None) -> None:
    """Assign a reference to new rows and reject changes to an existing reference."""

    if instance._state.adding:
        if not instance.crm_reference:
            instance.crm_reference = generate_crm_reference(
                model=type(instance),
                prefix=prefix,
                reference_date=reference_date,
            )
        return

    original_reference = (
        type(instance).objects.filter(pk=instance.pk)
        .values_list("crm_reference", flat=True)
        .first()
    )
    if original_reference and instance.crm_reference != original_reference:
        raise ValidationError(
            {"crm_reference": "CRM reference cannot be changed after creation."}
        )
