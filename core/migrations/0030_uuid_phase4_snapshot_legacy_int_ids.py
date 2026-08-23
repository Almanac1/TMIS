from django.db import migrations, models


def snapshot_legacy_ids(apps, schema_editor):
    for model_name in ("Contact", "Prospect", "Student", "Meditator", "Inquiry"):
        Model = apps.get_model("core", model_name)
        for obj in Model.objects.filter(legacy_int_id__isnull=True).iterator(chunk_size=500):
            obj.legacy_int_id = obj.pk
            obj.save(update_fields=["legacy_int_id"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0029_uuid_phase3_resync_uuid_refs"),
    ]

    operations = [
        migrations.AddField(
            model_name="contact",
            name="legacy_int_id",
            field=models.PositiveBigIntegerField(blank=True, db_index=True, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="prospect",
            name="legacy_int_id",
            field=models.PositiveBigIntegerField(blank=True, db_index=True, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="student",
            name="legacy_int_id",
            field=models.PositiveBigIntegerField(blank=True, db_index=True, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="meditator",
            name="legacy_int_id",
            field=models.PositiveBigIntegerField(blank=True, db_index=True, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="inquiry",
            name="legacy_int_id",
            field=models.PositiveBigIntegerField(blank=True, db_index=True, null=True, unique=True),
        ),
        migrations.RunPython(snapshot_legacy_ids, migrations.RunPython.noop),
    ]
