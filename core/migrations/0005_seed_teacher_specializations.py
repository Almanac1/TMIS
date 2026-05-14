from django.db import migrations


SPECIALIZATIONS = [
    "TM Teacher",
    "Advance Technique 1",
    "Advance Technique 2",
    "Advance Technique 3",
    "Advance Technique 4",
    "Sidhi Administrator",
]


def seed_specializations(apps, schema_editor):
    TeacherSpecialization = apps.get_model("core", "TeacherSpecialization")
    for name in SPECIALIZATIONS:
        TeacherSpecialization.objects.get_or_create(name=name)


def unseed_specializations(apps, schema_editor):
    TeacherSpecialization = apps.get_model("core", "TeacherSpecialization")
    TeacherSpecialization.objects.filter(name__in=SPECIALIZATIONS).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_rename_teacherspecialization_code_to_name"),
    ]

    operations = [
        migrations.RunPython(seed_specializations, unseed_specializations),
    ]
