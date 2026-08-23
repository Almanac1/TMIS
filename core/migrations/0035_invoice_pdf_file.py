from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0034_alter_communication_delivery_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="invoice",
            name="pdf_file",
            field=models.FileField(blank=True, null=True, upload_to="invoices/pdfs/"),
        ),
    ]
