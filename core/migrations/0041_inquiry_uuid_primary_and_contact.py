import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def backfill_inquiry_contact_and_preserve_ids(apps, schema_editor):
    Inquiry = apps.get_model("core", "Inquiry")
    for inquiry in Inquiry.objects.select_related(
        "prospect__contact",
        "student__prospect__contact",
    ).iterator(chunk_size=500):
        prospect_contact_id = inquiry.prospect.contact_id if inquiry.prospect_id else None
        student_contact_id = (
            inquiry.student.prospect.contact_id if inquiry.student_id else None
        )
        if (
            prospect_contact_id
            and student_contact_id
            and prospect_contact_id != student_contact_id
        ):
            raise RuntimeError(
                f"Inquiry legacy ID {inquiry.pk} links lifecycle records from different Contacts."
            )
        contact_id = student_contact_id or prospect_contact_id
        if not contact_id:
            raise RuntimeError(
                f"Inquiry legacy ID {inquiry.pk} has no canonical Contact; migration stopped."
            )
        inquiry.contact_id = contact_id
        inquiry.save(update_fields=["contact"])


def ensure_database_uuid_primary_key(apps, schema_editor):
    """Promote the retained UUID unique column at the database layer too."""
    Inquiry = apps.get_model("core", "Inquiry")
    connection = schema_editor.connection
    table_name = Inquiry._meta.db_table
    constraints = connection.introspection.get_constraints(
        connection.cursor(), table_name
    )
    existing_primary = [
        name for name, details in constraints.items() if details.get("primary_key")
    ]
    uuid_is_primary = any(
        constraints[name].get("columns") == ["uuid"] for name in existing_primary
    )
    if uuid_is_primary:
        return
    if connection.vendor != "postgresql":
        raise RuntimeError(
            "Inquiry UUID primary-key promotion requires SQLite table recreation "
            "or PostgreSQL constraint support; unsupported database backend."
        )
    quoted_table = schema_editor.quote_name(table_name)
    with connection.cursor() as cursor:
        for constraint_name in existing_primary:
            cursor.execute(
                f"ALTER TABLE {quoted_table} DROP CONSTRAINT "
                f"{schema_editor.quote_name(constraint_name)}"
            )
        cursor.execute(f"ALTER TABLE {quoted_table} ADD PRIMARY KEY (uuid)")


class Migration(migrations.Migration):
    dependencies = [("core", "0040_contact_unique_contact_email_ci_nonempty")]

    operations = [
        # Migrations 0027-0030 populated these physical columns; 0038 removed
        # only their Django state. Map them back before any SQLite table
        # recreation so every historical value survives unchanged.
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RemoveField(model_name="inquiry", name="id"),
                migrations.AddField(
                    model_name="inquiry",
                    name="legacy_int_id",
                    field=models.PositiveBigIntegerField(
                        blank=True,
                        editable=False,
                        null=True,
                        unique=True,
                    ),
                ),
                migrations.AddField(
                    model_name="inquiry",
                    name="uuid",
                    field=models.UUIDField(
                        db_column="uuid",
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
            ],
        ),
        migrations.RemoveConstraint(
            model_name="inquiry",
            name="inquiry_requires_prospect_or_student",
        ),
        migrations.RunPython(
            ensure_database_uuid_primary_key,
            migrations.RunPython.noop,
        ),
        migrations.AddField(
            model_name="inquiry",
            name="contact",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="inquiries",
                to="core.contact",
            ),
        ),
        migrations.RunPython(
            backfill_inquiry_contact_and_preserve_ids,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="inquiry",
            name="contact",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="inquiries",
                to="core.contact",
            ),
        ),
        migrations.AlterField(
            model_name="inquiry",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="owned_inquiries",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="inquiry",
            name="prospect",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="inquiries",
                to="core.prospect",
            ),
        ),
        migrations.AlterField(
            model_name="inquiry",
            name="student",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="inquiries",
                to="core.student",
            ),
        ),
        migrations.AddIndex(
            model_name="inquiry",
            index=models.Index(
                fields=["contact", "inquiry_date"],
                name="core_inquir_contact_87e6fa_idx",
            ),
        ),
    ]
