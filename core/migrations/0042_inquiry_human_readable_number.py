from django.db import migrations, models
from django.utils import timezone


def backfill_inquiry_numbers(apps, schema_editor):
    Inquiry = apps.get_model("core", "Inquiry")
    InquiryNumberSequence = apps.get_model("core", "InquiryNumberSequence")

    last_number_by_year = {}
    inquiries = Inquiry.objects.order_by("inquiry_date", "created_at", "uuid")
    for inquiry in inquiries.iterator(chunk_size=500):
        received_at = inquiry.inquiry_date
        year = (
            timezone.localtime(received_at).year
            if timezone.is_aware(received_at)
            else received_at.year
        )
        next_number = last_number_by_year.get(year, 0) + 1
        inquiry.inquiry_number = f"INQ-{year}-{next_number:04d}"
        inquiry.save(update_fields=["inquiry_number"])
        last_number_by_year[year] = next_number

    for year, last_number in last_number_by_year.items():
        InquiryNumberSequence.objects.update_or_create(
            year=year,
            defaults={"last_number": last_number},
        )


def clear_inquiry_numbers(apps, schema_editor):
    Inquiry = apps.get_model("core", "Inquiry")
    Inquiry.objects.update(inquiry_number=None)


class Migration(migrations.Migration):
    dependencies = [("core", "0041_inquiry_uuid_primary_and_contact")]

    operations = [
        migrations.CreateModel(
            name="InquiryNumberSequence",
            fields=[
                (
                    "year",
                    models.PositiveSmallIntegerField(primary_key=True, serialize=False),
                ),
                ("last_number", models.PositiveIntegerField(default=0)),
            ],
            options={
                "verbose_name": "Inquiry number sequence",
                "verbose_name_plural": "Inquiry number sequences",
            },
        ),
        migrations.AddField(
            model_name="inquiry",
            name="inquiry_number",
            field=models.CharField(
                blank=True,
                editable=False,
                max_length=30,
                null=True,
                unique=True,
            ),
        ),
        migrations.RunPython(backfill_inquiry_numbers, clear_inquiry_numbers),
        migrations.AlterField(
            model_name="inquiry",
            name="inquiry_number",
            field=models.CharField(editable=False, max_length=30, unique=True),
        ),
    ]
