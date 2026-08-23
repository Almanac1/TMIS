from django.db import migrations


def resync_uuid_refs(apps, schema_editor):
    Prospect = apps.get_model("core", "Prospect")
    Student = apps.get_model("core", "Student")
    Meditator = apps.get_model("core", "Meditator")
    Inquiry = apps.get_model("core", "Inquiry")
    Communication = apps.get_model("core", "Communication")
    Enrollment = apps.get_model("core", "Enrollment")
    InterviewForm = apps.get_model("core", "InterviewForm")
    MeditatorTransitionEvent = apps.get_model("core", "MeditatorTransitionEvent")

    for obj in Prospect.objects.select_related("contact", "converted_student").iterator(chunk_size=500):
        changed = []
        value = obj.contact.uuid if obj.contact_id and obj.contact else None
        if obj.contact_uuid_ref != value:
            obj.contact_uuid_ref = value
            changed.append("contact_uuid_ref")
        value = obj.converted_student.uuid if obj.converted_student_id and obj.converted_student else None
        if obj.converted_student_uuid_ref != value:
            obj.converted_student_uuid_ref = value
            changed.append("converted_student_uuid_ref")
        if changed:
            obj.save(update_fields=changed)

    for obj in Student.objects.select_related("prospect").iterator(chunk_size=500):
        value = obj.prospect.uuid if obj.prospect_id and obj.prospect else None
        if obj.prospect_uuid_ref != value:
            obj.prospect_uuid_ref = value
            obj.save(update_fields=["prospect_uuid_ref"])

    for obj in Meditator.objects.select_related("student").iterator(chunk_size=500):
        value = obj.student.uuid if obj.student_id and obj.student else None
        if obj.student_uuid_ref != value:
            obj.student_uuid_ref = value
            obj.save(update_fields=["student_uuid_ref"])

    for obj in Inquiry.objects.select_related("prospect", "student").iterator(chunk_size=500):
        changed = []
        value = obj.prospect.uuid if obj.prospect_id and obj.prospect else None
        if obj.prospect_uuid_ref != value:
            obj.prospect_uuid_ref = value
            changed.append("prospect_uuid_ref")
        value = obj.student.uuid if obj.student_id and obj.student else None
        if obj.student_uuid_ref != value:
            obj.student_uuid_ref = value
            changed.append("student_uuid_ref")
        if changed:
            obj.save(update_fields=changed)

    for obj in Communication.objects.select_related("prospect", "student").iterator(chunk_size=500):
        changed = []
        value = obj.prospect.uuid if obj.prospect_id and obj.prospect else None
        if obj.prospect_uuid_ref != value:
            obj.prospect_uuid_ref = value
            changed.append("prospect_uuid_ref")
        value = obj.student.uuid if obj.student_id and obj.student else None
        if obj.student_uuid_ref != value:
            obj.student_uuid_ref = value
            changed.append("student_uuid_ref")
        if changed:
            obj.save(update_fields=changed)

    for obj in Enrollment.objects.select_related("student").iterator(chunk_size=500):
        value = obj.student.uuid if obj.student_id and obj.student else None
        if obj.student_uuid_ref != value:
            obj.student_uuid_ref = value
            obj.save(update_fields=["student_uuid_ref"])

    for obj in InterviewForm.objects.select_related("student").iterator(chunk_size=500):
        value = obj.student.uuid if obj.student_id and obj.student else None
        if obj.student_uuid_ref != value:
            obj.student_uuid_ref = value
            obj.save(update_fields=["student_uuid_ref"])

    for obj in MeditatorTransitionEvent.objects.select_related("student", "meditator").iterator(chunk_size=500):
        changed = []
        value = obj.student.uuid if obj.student_id and obj.student else None
        if obj.student_uuid_ref != value:
            obj.student_uuid_ref = value
            changed.append("student_uuid_ref")
        value = obj.meditator.uuid if obj.meditator_id and obj.meditator else None
        if obj.meditator_uuid_ref != value:
            obj.meditator_uuid_ref = value
            changed.append("meditator_uuid_ref")
        if changed:
            obj.save(update_fields=changed)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0028_uuid_phase2_enforce_unique_nonnull"),
    ]

    operations = [
        migrations.RunPython(resync_uuid_refs, migrations.RunPython.noop),
    ]
