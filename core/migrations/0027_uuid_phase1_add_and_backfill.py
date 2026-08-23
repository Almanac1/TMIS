import uuid

from django.db import migrations, models


def backfill_uuid_phase1(apps, schema_editor):
    Contact = apps.get_model("core", "Contact")
    Prospect = apps.get_model("core", "Prospect")
    Student = apps.get_model("core", "Student")
    Meditator = apps.get_model("core", "Meditator")
    Inquiry = apps.get_model("core", "Inquiry")
    Communication = apps.get_model("core", "Communication")
    Enrollment = apps.get_model("core", "Enrollment")
    InterviewForm = apps.get_model("core", "InterviewForm")
    MeditatorTransitionEvent = apps.get_model("core", "MeditatorTransitionEvent")

    for model in (Contact, Prospect, Student, Meditator, Inquiry):
        for obj in model.objects.filter(uuid__isnull=True).iterator(chunk_size=500):
            obj.uuid = uuid.uuid4()
            obj.save(update_fields=["uuid"])

    for obj in Prospect.objects.select_related("contact", "converted_student").iterator(chunk_size=500):
        changed = []
        if obj.contact_id and obj.contact and obj.contact.uuid and obj.contact_uuid_ref != obj.contact.uuid:
            obj.contact_uuid_ref = obj.contact.uuid
            changed.append("contact_uuid_ref")
        if (
            obj.converted_student_id
            and obj.converted_student
            and obj.converted_student.uuid
            and obj.converted_student_uuid_ref != obj.converted_student.uuid
        ):
            obj.converted_student_uuid_ref = obj.converted_student.uuid
            changed.append("converted_student_uuid_ref")
        if changed:
            obj.save(update_fields=changed)

    for obj in Student.objects.select_related("prospect").iterator(chunk_size=500):
        if obj.prospect_id and obj.prospect and obj.prospect.uuid and obj.prospect_uuid_ref != obj.prospect.uuid:
            obj.prospect_uuid_ref = obj.prospect.uuid
            obj.save(update_fields=["prospect_uuid_ref"])

    for obj in Meditator.objects.select_related("student").iterator(chunk_size=500):
        if obj.student_id and obj.student and obj.student.uuid and obj.student_uuid_ref != obj.student.uuid:
            obj.student_uuid_ref = obj.student.uuid
            obj.save(update_fields=["student_uuid_ref"])

    for obj in Inquiry.objects.select_related("prospect", "student").iterator(chunk_size=500):
        changed = []
        if obj.prospect_id and obj.prospect and obj.prospect.uuid and obj.prospect_uuid_ref != obj.prospect.uuid:
            obj.prospect_uuid_ref = obj.prospect.uuid
            changed.append("prospect_uuid_ref")
        if obj.student_id and obj.student and obj.student.uuid and obj.student_uuid_ref != obj.student.uuid:
            obj.student_uuid_ref = obj.student.uuid
            changed.append("student_uuid_ref")
        if changed:
            obj.save(update_fields=changed)

    for obj in Communication.objects.select_related("prospect", "student").iterator(chunk_size=500):
        changed = []
        if obj.prospect_id and obj.prospect and obj.prospect.uuid and obj.prospect_uuid_ref != obj.prospect.uuid:
            obj.prospect_uuid_ref = obj.prospect.uuid
            changed.append("prospect_uuid_ref")
        if obj.student_id and obj.student and obj.student.uuid and obj.student_uuid_ref != obj.student.uuid:
            obj.student_uuid_ref = obj.student.uuid
            changed.append("student_uuid_ref")
        if changed:
            obj.save(update_fields=changed)

    for obj in Enrollment.objects.select_related("student").iterator(chunk_size=500):
        if obj.student_id and obj.student and obj.student.uuid and obj.student_uuid_ref != obj.student.uuid:
            obj.student_uuid_ref = obj.student.uuid
            obj.save(update_fields=["student_uuid_ref"])

    for obj in InterviewForm.objects.select_related("student").iterator(chunk_size=500):
        if obj.student_id and obj.student and obj.student.uuid and obj.student_uuid_ref != obj.student.uuid:
            obj.student_uuid_ref = obj.student.uuid
            obj.save(update_fields=["student_uuid_ref"])

    for obj in MeditatorTransitionEvent.objects.select_related("student", "meditator").iterator(chunk_size=500):
        changed = []
        if obj.student_id and obj.student and obj.student.uuid and obj.student_uuid_ref != obj.student.uuid:
            obj.student_uuid_ref = obj.student.uuid
            changed.append("student_uuid_ref")
        if obj.meditator_id and obj.meditator and obj.meditator.uuid and obj.meditator_uuid_ref != obj.meditator.uuid:
            obj.meditator_uuid_ref = obj.meditator.uuid
            changed.append("meditator_uuid_ref")
        if changed:
            obj.save(update_fields=changed)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0026_add_prospect_course_interest"),
    ]

    operations = [
        migrations.AddField(
            model_name="contact",
            name="uuid",
            field=models.UUIDField(blank=True, db_index=True, default=uuid.uuid4, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="prospect",
            name="uuid",
            field=models.UUIDField(blank=True, db_index=True, default=uuid.uuid4, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="student",
            name="uuid",
            field=models.UUIDField(blank=True, db_index=True, default=uuid.uuid4, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="meditator",
            name="uuid",
            field=models.UUIDField(blank=True, db_index=True, default=uuid.uuid4, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="inquiry",
            name="uuid",
            field=models.UUIDField(blank=True, db_index=True, default=uuid.uuid4, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="prospect",
            name="contact_uuid_ref",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="prospect",
            name="converted_student_uuid_ref",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="student",
            name="prospect_uuid_ref",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="meditator",
            name="student_uuid_ref",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="inquiry",
            name="prospect_uuid_ref",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="inquiry",
            name="student_uuid_ref",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="communication",
            name="prospect_uuid_ref",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="communication",
            name="student_uuid_ref",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="enrollment",
            name="student_uuid_ref",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="interviewform",
            name="student_uuid_ref",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="meditatortransitionevent",
            name="student_uuid_ref",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="meditatortransitionevent",
            name="meditator_uuid_ref",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.RunPython(backfill_uuid_phase1, migrations.RunPython.noop),
    ]
