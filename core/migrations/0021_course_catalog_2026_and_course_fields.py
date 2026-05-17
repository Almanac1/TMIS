from decimal import Decimal

import django.core.validators
from django.db import migrations, models


def seed_official_courses(apps, schema_editor):
    Course = apps.get_model("core", "Course")

    official_courses = [
        {
            "name": "TM - adult",
            "code": "TM-AD",
            "category": "TM",
            "variant": "Adult",
            "base_fee": Decimal("3000.00"),
            "additional_cost_description": "",
            "requires_residence": False,
            "notes": "Official 2026 MFG Services course.",
        },
        {
            "name": "TM - couple",
            "code": "TM-CP",
            "category": "TM",
            "variant": "Couple",
            "base_fee": Decimal("4500.00"),
            "additional_cost_description": "",
            "requires_residence": False,
            "notes": "Official 2026 MFG Services course.",
        },
        {
            "name": "TM - family",
            "code": "TM-FM",
            "category": "TM",
            "variant": "Family",
            "base_fee": Decimal("4500.00"),
            "additional_cost_description": "750 GHS per child under 18.",
            "requires_residence": False,
            "notes": "Official 2026 MFG Services course.",
        },
        {
            "name": "TM - student",
            "code": "TM-ST",
            "category": "TM",
            "variant": "Student",
            "base_fee": Decimal("1500.00"),
            "additional_cost_description": "",
            "requires_residence": False,
            "notes": "Official 2026 MFG Services course.",
        },
        {
            "name": "TM - word of wisdom",
            "code": "TM-WW",
            "category": "TM",
            "variant": "Word of Wisdom",
            "base_fee": Decimal("750.00"),
            "additional_cost_description": "",
            "requires_residence": False,
            "notes": "Official 2026 MFG Services course.",
        },
        {
            "name": "Advanced Technique",
            "code": "AT-ST",
            "category": "AT",
            "variant": "Standard",
            "base_fee": Decimal("2000.00"),
            "additional_cost_description": "100 GHS/day for meals.",
            "requires_residence": False,
            "notes": "Official 2026 MFG Services course.",
        },
        {
            "name": "Advanced Technique - couple",
            "code": "AT-CP",
            "category": "AT",
            "variant": "Couple",
            "base_fee": Decimal("4000.00"),
            "additional_cost_description": "100 GHS/day for meals per person if applicable.",
            "requires_residence": False,
            "notes": "Official 2026 MFG Services course.",
        },
        {
            "name": "TM-Sidhi course",
            "code": "SID",
            "category": "SID",
            "variant": "Standard",
            "base_fee": Decimal("12000.00"),
            "additional_cost_description": "In-residence cost.",
            "requires_residence": True,
            "notes": "Official 2026 MFG Services course.",
        },
        {
            "name": "Knowledge courses",
            "code": "KC",
            "category": "KC",
            "variant": "Standard",
            "base_fee": Decimal("0.00"),
            "additional_cost_description": "Fee TBD.",
            "requires_residence": False,
            "notes": "Official 2026 MFG Services course. Donation/Fee is TBD.",
        },
    ]

    official_codes = {item["code"] for item in official_courses}

    for item in official_courses:
        defaults = {
            "name": item["name"],
            "category": item["category"],
            "variant": item["variant"],
            "base_fee": item["base_fee"],
            "standard_fee": item["base_fee"],
            "currency": "GHS",
            "additional_cost_description": item["additional_cost_description"],
            "is_active": True,
            "requires_residence": item["requires_residence"],
            "notes": item["notes"],
            "status": "active",
        }

        course = Course.objects.filter(code=item["code"]).first()
        if course is None:
            course = Course.objects.filter(name=item["name"]).first()

        if course is None:
            Course.objects.create(code=item["code"], **defaults)
        else:
            for field, value in defaults.items():
                setattr(course, field, value)
            course.code = item["code"]
            course.save()

    Course.objects.exclude(code__in=official_codes).update(is_active=False, status="inactive")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0020_contact_converted_at_contact_converted_prospect_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="course",
            name="additional_cost_description",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="course",
            name="base_fee",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                max_digits=10,
                validators=[django.core.validators.MinValueValidator(Decimal("0.00"))],
            ),
        ),
        migrations.AddField(
            model_name="course",
            name="category",
            field=models.CharField(blank=True, choices=[("TM", "TM"), ("AT", "AT"), ("SID", "SID"), ("KC", "KC")], max_length=10),
        ),
        migrations.AddField(
            model_name="course",
            name="code",
            field=models.CharField(blank=True, max_length=20, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="course",
            name="currency",
            field=models.CharField(default="GHS", max_length=3),
        ),
        migrations.AddField(
            model_name="course",
            name="is_active",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="course",
            name="notes",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="course",
            name="requires_residence",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="course",
            name="variant",
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AddField(
            model_name="enrollment",
            name="number_of_children_under_18",
            field=models.PositiveIntegerField(blank=True, default=0),
        ),
        migrations.AddIndex(
            model_name="course",
            index=models.Index(fields=["code"], name="core_course_code_833bcc_idx"),
        ),
        migrations.AddIndex(
            model_name="course",
            index=models.Index(fields=["category"], name="core_course_categor_1a40ed_idx"),
        ),
        migrations.AddIndex(
            model_name="course",
            index=models.Index(fields=["is_active"], name="core_course_is_acti_bcef00_idx"),
        ),
        migrations.RunPython(seed_official_courses, migrations.RunPython.noop),
    ]
