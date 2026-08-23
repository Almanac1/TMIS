import uuid

from django.db import migrations, models


def ensure_uuid_values(apps, schema_editor):
    for model_name in ("Contact", "Prospect", "Student", "Meditator", "Inquiry"):
        Model = apps.get_model("core", model_name)
        # Reassign UUIDs for every row to guarantee uniqueness before adding constraint.
        for obj in Model.objects.all().iterator(chunk_size=500):
            obj.uuid = uuid.uuid4()
            obj.save(update_fields=["uuid"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0027_uuid_phase1_add_and_backfill"),
    ]

    operations = [
        migrations.RunPython(ensure_uuid_values, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="contact",
            name="uuid",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
        migrations.AlterField(
            model_name="prospect",
            name="uuid",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
        migrations.AlterField(
            model_name="student",
            name="uuid",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
        migrations.AlterField(
            model_name="meditator",
            name="uuid",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
        migrations.AlterField(
            model_name="inquiry",
            name="uuid",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
    ]
