from django.db import migrations, models
import django.db.models.deletion


def backfill_enrollment_course(apps, schema_editor):
    Enrollment = apps.get_model('core', 'Enrollment')
    for enrollment in Enrollment.objects.select_related('session__course').all():
        if enrollment.course_id is None and enrollment.session_id and enrollment.session.course_id:
            enrollment.course_id = enrollment.session.course_id
            enrollment.save(update_fields=['course'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0023_enrollment_teacher_studentgovernorassignment_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='enrollment',
            name='course',
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='enrollments',
                to='core.course',
            ),
        ),
        migrations.RunPython(backfill_enrollment_course, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='enrollment',
            name='course',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='enrollments',
                to='core.course',
            ),
        ),
    ]
