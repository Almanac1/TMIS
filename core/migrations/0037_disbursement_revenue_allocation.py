from decimal import Decimal, ROUND_HALF_UP

from django.core.validators import MinValueValidator
from django.db import migrations, models


CENT = Decimal("0.01")


def _money(value):
    return Decimal(value or 0).quantize(CENT, rounding=ROUND_HALF_UP)


def apply_allocation_formula(apps, schema_editor):
    Disbursement = apps.get_model("core", "Disbursement")
    for disbursement in Disbursement.objects.all().iterator():
        amount = _money(disbursement.balance_due_snapshot)
        disbursement.ico_amount = _money(amount * Decimal("0.125"))
        disbursement.national_office_amount = _money(amount * Decimal("0.20"))
        disbursement.teacher_amount = _money(amount * Decimal("0.50"))
        disbursement.marketing_amount = _money(
            amount
            - disbursement.ico_amount
            - disbursement.national_office_amount
            - disbursement.teacher_amount
        )
        disbursement.save(
            update_fields=[
                "ico_amount",
                "national_office_amount",
                "teacher_amount",
                "marketing_amount",
            ]
        )


def restore_legacy_allocation(apps, schema_editor):
    Disbursement = apps.get_model("core", "Disbursement")
    for disbursement in Disbursement.objects.all().iterator():
        amount = _money(disbursement.balance_due_snapshot)
        disbursement.teacher_amount = _money(amount * Decimal("0.50"))
        disbursement.national_office_amount = _money(amount * Decimal("0.20"))
        disbursement.ico_amount = _money(
            amount - disbursement.teacher_amount - disbursement.national_office_amount
        )
        disbursement.save(
            update_fields=["teacher_amount", "national_office_amount", "ico_amount"]
        )


class Migration(migrations.Migration):
    dependencies = [("core", "0036_meditator_integrity_lifecycle")]

    operations = [
        migrations.RenameField(
            model_name="disbursement",
            old_name="location_amount",
            new_name="national_office_amount",
        ),
        migrations.AddField(
            model_name="disbursement",
            name="marketing_amount",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                max_digits=10,
                validators=[MinValueValidator(Decimal("0.00"))],
            ),
        ),
        migrations.RunPython(apply_allocation_formula, restore_legacy_allocation),
    ]
