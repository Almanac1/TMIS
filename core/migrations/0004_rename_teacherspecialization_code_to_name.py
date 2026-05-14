from django.db import migrations, models


CODE_TO_NAME = {
    "tm_teacher": "TM Teacher",
    "advance_technique_1": "Advance Technique 1",
    "advance_technique_2": "Advance Technique 2",
    "advance_technique_3": "Advance Technique 3",
    "advance_technique_4": "Advance Technique 4",
    "sidhi_administrator": "Sidhi Administrator",
}

NAME_TO_CODE = {value: key for key, value in CODE_TO_NAME.items()}


def forwards_map_codes_to_names(apps, schema_editor):
    TeacherSpecialization = apps.get_model("core", "TeacherSpecialization")
    for obj in TeacherSpecialization.objects.order_by().all().only("id", "name"):
        if obj.name in CODE_TO_NAME:
            obj.name = CODE_TO_NAME[obj.name]
            obj.save(update_fields=["name"])


def backwards_map_names_to_codes(apps, schema_editor):
    TeacherSpecialization = apps.get_model("core", "TeacherSpecialization")
    for obj in TeacherSpecialization.objects.order_by().all().only("id", "name"):
        if obj.name in NAME_TO_CODE:
            obj.name = NAME_TO_CODE[obj.name]
            obj.save(update_fields=["name"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_teacherspecialization_remove_teacher_specialization_and_more"),
    ]

    operations = [
        migrations.RenameField(
            model_name="teacherspecialization",
            old_name="code",
            new_name="name",
        ),
        migrations.RunPython(forwards_map_codes_to_names, backwards_map_names_to_codes),
        migrations.AlterField(
            model_name="teacherspecialization",
            name="name",
            field=models.CharField(
                choices=[
                    ("TM Teacher", "TM Teacher"),
                    ("Advance Technique 1", "Advance Technique 1"),
                    ("Advance Technique 2", "Advance Technique 2"),
                    ("Advance Technique 3", "Advance Technique 3"),
                    ("Advance Technique 4", "Advance Technique 4"),
                    ("Sidhi Administrator", "Sidhi Administrator"),
                ],
                max_length=50,
                unique=True,
            ),
        ),
        migrations.AlterModelOptions(
            name="teacherspecialization",
            options={
                "ordering": ["name"],
                "verbose_name": "Teacher Specialization",
                "verbose_name_plural": "Teacher Specializations",
            },
        ),
    ]
