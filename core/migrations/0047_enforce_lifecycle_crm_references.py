from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0046_backfill_lifecycle_crm_references")]

    operations = [
        migrations.AlterField(
            model_name="contact",
            name="crm_reference",
            field=models.CharField(editable=False, max_length=30, unique=True),
        ),
        migrations.AlterField(
            model_name="prospect",
            name="crm_reference",
            field=models.CharField(editable=False, max_length=30, unique=True),
        ),
        migrations.AlterField(
            model_name="student",
            name="crm_reference",
            field=models.CharField(editable=False, max_length=30, unique=True),
        ),
        migrations.AlterField(
            model_name="meditator",
            name="crm_reference",
            field=models.CharField(editable=False, max_length=30, unique=True),
        ),
    ]
