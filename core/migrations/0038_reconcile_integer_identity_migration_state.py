"""Reconcile migration state with the integer-PK schema used by the application.

Migration 0033 changed state only toward UUID primary keys while the model and
database continued using integer primary keys. This migration deliberately has
no database operations; it prevents future schema changes from targeting the
unused UUID shadow columns.
"""

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0037_disbursement_revenue_allocation")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RenameIndex(model_name="communication", old_name="core_commun_prospec_f2b3ce_idx", new_name="core_commun_prospec_a90a4b_idx"),
                migrations.RenameIndex(model_name="communication", old_name="core_commun_student_a91f46_idx", new_name="core_commun_student_3d74cd_idx"),
                migrations.RenameIndex(model_name="enrollment", old_name="core_enroll_student_d53a94_idx", new_name="core_enroll_student_b14473_idx"),
                migrations.RenameIndex(model_name="inquiry", old_name="core_inquir_prospec_ac3fd7_idx", new_name="core_inquir_prospec_4f2059_idx"),
                migrations.RenameIndex(model_name="inquiry", old_name="core_inquir_student_9a1a76_idx", new_name="core_inquir_student_bbaee0_idx"),
                migrations.RenameIndex(model_name="interviewform", old_name="core_interv_student_394cf0_idx", new_name="core_interv_student_9e89b3_idx"),
                migrations.RenameIndex(model_name="meditatortransitionevent", old_name="core_medita_student_d67f43_idx", new_name="core_medita_student_3d15fd_idx"),
                migrations.RemoveField(model_name="contact", name="legacy_int_id"),
                migrations.RemoveField(model_name="inquiry", name="legacy_int_id"),
                migrations.RemoveField(model_name="inquiry", name="uuid"),
                migrations.RemoveField(model_name="meditator", name="legacy_int_id"),
                migrations.RemoveField(model_name="prospect", name="legacy_int_id"),
                migrations.RemoveField(model_name="student", name="legacy_int_id"),
                migrations.AddField(model_name="contact", name="id", field=models.BigAutoField(auto_created=True, default=None, primary_key=True, serialize=False, verbose_name="ID"), preserve_default=False),
                migrations.AddField(model_name="inquiry", name="id", field=models.BigAutoField(auto_created=True, default=None, primary_key=True, serialize=False, verbose_name="ID"), preserve_default=False),
                migrations.AddField(model_name="meditator", name="id", field=models.BigAutoField(auto_created=True, default=None, primary_key=True, serialize=False, verbose_name="ID"), preserve_default=False),
                migrations.AddField(model_name="prospect", name="id", field=models.BigAutoField(auto_created=True, default=None, primary_key=True, serialize=False, verbose_name="ID"), preserve_default=False),
                migrations.AddField(model_name="student", name="id", field=models.BigAutoField(auto_created=True, default=None, primary_key=True, serialize=False, verbose_name="ID"), preserve_default=False),
                migrations.AlterField(model_name="communication", name="delivery_status", field=models.CharField(choices=[("queued", "Queued"), ("sent", "Sent"), ("delivered", "Delivered"), ("failed", "Failed"), ("bounced", "Bounced")], default="queued", max_length=20)),
                migrations.AlterField(model_name="communication", name="prospect", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="communications", to="core.prospect")),
                migrations.AlterField(model_name="communication", name="student", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="communications", to="core.student")),
                migrations.AlterField(model_name="contact", name="uuid", field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                migrations.AlterField(model_name="enrollment", name="student", field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="enrollments", to="core.student")),
                migrations.AlterField(model_name="inquiry", name="prospect", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="inquiries", to="core.prospect")),
                migrations.AlterField(model_name="inquiry", name="student", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="inquiries", to="core.student")),
                migrations.AlterField(model_name="interviewform", name="student", field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="interview_forms", to="core.student")),
                migrations.AlterField(model_name="meditator", name="student", field=models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="meditator_profile", to="core.student")),
                migrations.AlterField(model_name="meditator", name="uuid", field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                migrations.AlterField(model_name="meditatortransitionevent", name="meditator", field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="events", to="core.meditator")),
                migrations.AlterField(model_name="meditatortransitionevent", name="student", field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="meditator_transition_events", to="core.student")),
                migrations.AlterField(model_name="prospect", name="contact", field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="prospect", to="core.contact")),
                migrations.AlterField(model_name="prospect", name="converted_student", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="source_prospects", to="core.student")),
                migrations.AlterField(model_name="prospect", name="uuid", field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                migrations.AlterField(model_name="student", name="prospect", field=models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="student_record", to="core.prospect")),
                migrations.AlterField(model_name="student", name="uuid", field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
            ],
        )
    ]
