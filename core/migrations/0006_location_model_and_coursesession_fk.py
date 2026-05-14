from django.db import migrations, models
import django.db.models.deletion
from django.utils.text import slugify


DEFAULT_LOCATIONS = [
    "Accra East",
    "Accra North",
    "Orgle Lodge",
    "Damatonu",
]


def _unique_code(Location, base_name):
    base = slugify(base_name)[:40] or "location"
    code = base
    i = 1
    while Location.objects.filter(code=code).exists():
        i += 1
        code = f"{base[:38]}-{i}"
    return code


def forwards_create_locations_and_migrate_sessions(apps, schema_editor):
    Location = apps.get_model("core", "Location")
    CourseSession = apps.get_model("core", "CourseSession")

    canonical_by_key = {}
    for loc_name in DEFAULT_LOCATIONS:
        key = loc_name.strip().casefold()
        loc, _ = Location.objects.get_or_create(
            name=loc_name,
            defaults={
                "code": _unique_code(Location, loc_name),
                "country": "Ghana",
                "is_active": True,
            },
        )
        canonical_by_key[key] = loc

    default_location = canonical_by_key["accra east"]

    for session in CourseSession.objects.order_by().all().only("id", "legacy_location"):
        raw = (session.legacy_location or "").strip()
        if not raw:
            session.location_id = default_location.id
            session.save(update_fields=["location"])
            continue

        key = raw.casefold()
        if key in canonical_by_key:
            target = canonical_by_key[key]
        else:
            target, _ = Location.objects.get_or_create(
                name=raw,
                defaults={
                    "code": _unique_code(Location, raw),
                    "country": "Ghana",
                    "is_active": True,
                },
            )

        session.location_id = target.id
        session.save(update_fields=["location"])


def backwards_restore_legacy_location(apps, schema_editor):
    CourseSession = apps.get_model("core", "CourseSession")
    for session in CourseSession.objects.select_related("location").order_by().all():
        session.legacy_location = session.location.name if session.location_id else ""
        session.save(update_fields=["legacy_location"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0005_seed_teacher_specializations"),
    ]

    operations = [
        migrations.CreateModel(
            name="Location",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=150, unique=True)),
                ("code", models.SlugField(blank=True, max_length=50, unique=True)),
                ("address_line1", models.CharField(blank=True, max_length=255)),
                ("address_line2", models.CharField(blank=True, max_length=255)),
                ("city", models.CharField(blank=True, max_length=100)),
                ("province_state", models.CharField(blank=True, max_length=100)),
                ("country", models.CharField(default="Ghana", max_length=100)),
                ("is_active", models.BooleanField(default=True)),
                ("notes", models.TextField(blank=True)),
            ],
            options={
                "ordering": ["name"],
            },
        ),
        migrations.AddIndex(
            model_name="location",
            index=models.Index(fields=["name"], name="core_locati_name_1aa503_idx"),
        ),
        migrations.AddIndex(
            model_name="location",
            index=models.Index(fields=["is_active"], name="core_locati_is_acti_66e1de_idx"),
        ),
        migrations.AddIndex(
            model_name="location",
            index=models.Index(fields=["city"], name="core_locati_city_9ce40a_idx"),
        ),
        migrations.RenameField(
            model_name="coursesession",
            old_name="location",
            new_name="legacy_location",
        ),
        migrations.AddField(
            model_name="coursesession",
            name="location",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="course_sessions",
                to="core.location",
            ),
        ),
        migrations.RunPython(
            forwards_create_locations_and_migrate_sessions,
            backwards_restore_legacy_location,
        ),
        migrations.RemoveField(
            model_name="coursesession",
            name="legacy_location",
        ),
        migrations.AlterField(
            model_name="coursesession",
            name="location",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="course_sessions",
                to="core.location",
            ),
        ),
        migrations.AddIndex(
            model_name="coursesession",
            index=models.Index(fields=["location", "start_date"], name="core_course_locatio_9ef9fc_idx"),
        ),
    ]
