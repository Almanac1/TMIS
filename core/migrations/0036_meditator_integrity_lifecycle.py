from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0035_invoice_pdf_file"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    "ALTER TABLE core_meditator "
                    "ADD COLUMN is_active bool NOT NULL DEFAULT 1;",
                    migrations.RunSQL.noop,
                ),
                migrations.RunSQL(
                    "ALTER TABLE core_meditator "
                    "ADD COLUMN invalidated_at datetime NULL;",
                    migrations.RunSQL.noop,
                ),
                migrations.RunSQL(
                    "ALTER TABLE core_meditator "
                    "ADD COLUMN invalidation_reason text NOT NULL DEFAULT '';",
                    migrations.RunSQL.noop,
                ),
                migrations.RunSQL(
                    "CREATE INDEX core_meditator_is_active_idx "
                    "ON core_meditator (is_active);",
                    migrations.RunSQL.noop,
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="meditator",
                    name="is_active",
                    field=models.BooleanField(db_index=True, default=True),
                ),
                migrations.AddField(
                    model_name="meditator",
                    name="invalidated_at",
                    field=models.DateTimeField(blank=True, editable=False, null=True),
                ),
                migrations.AddField(
                    model_name="meditator",
                    name="invalidation_reason",
                    field=models.TextField(blank=True, editable=False),
                ),
            ],
        ),
    ]
