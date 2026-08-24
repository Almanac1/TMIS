from django.db import migrations, models


PROFILE_FIELDS = ("date_of_birth", "address", "city", "province_state", "country")


def move_student_profile_to_contact(apps, schema_editor):
    Contact = apps.get_model("core", "Contact")
    Student = apps.get_model("core", "Student")

    changed_contacts = []
    for student in Student.objects.select_related("prospect__contact").iterator(chunk_size=500):
        contact = student.prospect.contact
        changed = False
        for field in PROFILE_FIELDS:
            student_value = getattr(student, field)
            if getattr(contact, field) != student_value:
                setattr(contact, field, student_value)
                changed = True
        if changed:
            changed_contacts.append(contact)

    if changed_contacts:
        Contact.objects.bulk_update(changed_contacts, PROFILE_FIELDS, batch_size=500)

    for student in Student.objects.select_related("prospect__contact").iterator(chunk_size=500):
        mismatched = [
            field
            for field in PROFILE_FIELDS
            if getattr(student, field) != getattr(student.prospect.contact, field)
        ]
        if mismatched:
            raise RuntimeError(
                f"Student #{student.pk} profile backfill failed for: {', '.join(mismatched)}"
            )


def move_contact_profile_to_student(apps, schema_editor):
    Student = apps.get_model("core", "Student")
    students = []
    for student in Student.objects.select_related("prospect__contact").iterator(chunk_size=500):
        contact = student.prospect.contact
        for field in PROFILE_FIELDS:
            setattr(student, field, getattr(contact, field))
        students.append(student)
    if students:
        Student.objects.bulk_update(students, PROFILE_FIELDS, batch_size=500)


class Migration(migrations.Migration):
    dependencies = [("core", "0038_reconcile_integer_identity_migration_state")]

    operations = [
        migrations.AddField(model_name="contact", name="address", field=models.TextField(blank=True)),
        migrations.AddField(model_name="contact", name="city", field=models.CharField(blank=True, max_length=100)),
        migrations.AddField(model_name="contact", name="country", field=models.CharField(blank=True, max_length=100)),
        migrations.AddField(model_name="contact", name="date_of_birth", field=models.DateField(blank=True, null=True)),
        migrations.AddField(model_name="contact", name="province_state", field=models.CharField(blank=True, max_length=100)),
        migrations.RunPython(move_student_profile_to_contact, move_contact_profile_to_student),
        migrations.RemoveField(model_name="student", name="address"),
        migrations.RemoveField(model_name="student", name="city"),
        migrations.RemoveField(model_name="student", name="country"),
        migrations.RemoveField(model_name="student", name="date_of_birth"),
        migrations.RemoveField(model_name="student", name="province_state"),
    ]
