from django.db import migrations, models


def deduplicate_invoice_enrollments(apps, schema_editor):
    Invoice = apps.get_model('core', 'Invoice')
    duplicates = []
    for row in (
        Invoice.objects.values('enrollment_id')
        .order_by()
        .annotate(total=models.Count('id'))
        .filter(total__gt=1)
    ):
        enrollment_id = row['enrollment_id']
        invoices = list(
            Invoice.objects.filter(enrollment_id=enrollment_id).order_by('-issue_date', '-created_at', '-id')
        )
        keep = invoices[0]
        remove = invoices[1:]
        duplicates.append((enrollment_id, keep.id, [inv.id for inv in remove]))
        Invoice.objects.filter(id__in=[inv.id for inv in remove]).delete()

    if duplicates:
        print('Invoice duplicate cleanup summary:')
        for enrollment_id, keep_id, removed_ids in duplicates:
            print(
                f"- enrollment_id={enrollment_id}: kept invoice_id={keep_id}; removed invoice_ids={removed_ids}"
            )
    else:
        print('Invoice duplicate cleanup summary: no duplicates found.')


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0021_course_catalog_2026_and_course_fields'),
    ]

    operations = [
        migrations.RunPython(deduplicate_invoice_enrollments, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='invoice',
            name='enrollment',
            field=models.OneToOneField(on_delete=models.CASCADE, related_name='invoice', to='core.enrollment'),
        ),
        migrations.AlterField(
            model_name='payment',
            name='invoice',
            field=models.ForeignKey(on_delete=models.CASCADE, related_name='payments', to='core.invoice'),
        ),
    ]
