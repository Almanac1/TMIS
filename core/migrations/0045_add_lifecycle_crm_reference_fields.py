from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0044_inquiry_archiving")]

    operations = [
        migrations.CreateModel(
            name="CRMReferenceSequence",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("prefix", models.CharField(max_length=10)),
                ("year", models.PositiveSmallIntegerField()),
                ("last_number", models.PositiveIntegerField(default=0)),
            ],
            options={
                "verbose_name": "CRM reference sequence",
                "verbose_name_plural": "CRM reference sequences",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("prefix", "year"),
                        name="unique_crm_reference_sequence_prefix_year",
                    )
                ],
            },
        ),
        migrations.AddField(
            model_name="contact",
            name="crm_reference",
            field=models.CharField(
                blank=True,
                editable=False,
                max_length=30,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="prospect",
            name="crm_reference",
            field=models.CharField(
                blank=True,
                editable=False,
                max_length=30,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="student",
            name="crm_reference",
            field=models.CharField(
                blank=True,
                editable=False,
                max_length=30,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="meditator",
            name="crm_reference",
            field=models.CharField(
                blank=True,
                editable=False,
                max_length=30,
                null=True,
            ),
        ),
    ]
