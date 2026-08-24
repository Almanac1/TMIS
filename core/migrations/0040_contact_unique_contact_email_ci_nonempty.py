import django.db.models.functions.text
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0039_move_person_profile_to_contact")]

    operations = [
        migrations.AddConstraint(
            model_name="contact",
            constraint=models.UniqueConstraint(
                django.db.models.functions.text.Lower("email"),
                condition=models.Q(email__isnull=False) & ~models.Q(email=""),
                name="unique_contact_email_ci_nonempty",
            ),
        )
    ]
