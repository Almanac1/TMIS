from django.db import migrations
from django.utils import timezone


REFERENCE_MODELS = (
    ("Contact", "CNT"),
    ("Prospect", "PRO"),
    ("Student", "STU"),
    ("Meditator", "MED"),
)


def _year(value):
    if timezone.is_aware(value):
        value = timezone.localtime(value)
    return value.year


def backfill_lifecycle_crm_references(apps, schema_editor):
    CRMReferenceSequence = apps.get_model("core", "CRMReferenceSequence")

    for model_name, prefix in REFERENCE_MODELS:
        Model = apps.get_model("core", model_name)
        last_number_by_year = {}
        queryset = Model.objects.order_by("created_at", "pk")
        for record in queryset.iterator(chunk_size=500):
            year = _year(record.created_at)
            next_number = last_number_by_year.get(year, 0) + 1
            reference = f"{prefix}-{year}-{next_number:04d}"
            Model.objects.filter(pk=record.pk).update(crm_reference=reference)
            last_number_by_year[year] = next_number

        for year, last_number in last_number_by_year.items():
            CRMReferenceSequence.objects.update_or_create(
                prefix=prefix,
                year=year,
                defaults={"last_number": last_number},
            )


def clear_lifecycle_crm_references(apps, schema_editor):
    CRMReferenceSequence = apps.get_model("core", "CRMReferenceSequence")
    for model_name, prefix in REFERENCE_MODELS:
        Model = apps.get_model("core", model_name)
        Model.objects.update(crm_reference=None)
        CRMReferenceSequence.objects.filter(prefix=prefix).delete()


class Migration(migrations.Migration):
    dependencies = [("core", "0045_add_lifecycle_crm_reference_fields")]

    operations = [
        migrations.RunPython(
            backfill_lifecycle_crm_references,
            clear_lifecycle_crm_references,
        )
    ]
