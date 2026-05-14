from django.conf import settings
from django.db import migrations


def backfill_owners(apps, schema_editor):
    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))
    Prospect = apps.get_model('core', 'Prospect')
    Student = apps.get_model('core', 'Student')
    CourseSession = apps.get_model('core', 'CourseSession')
    Inquiry = apps.get_model('core', 'Inquiry')
    Invoice = apps.get_model('core', 'Invoice')
    Payment = apps.get_model('core', 'Payment')
    Communication = apps.get_model('core', 'Communication')

    fallback_user = (
        User.objects.filter(is_superuser=True).order_by('id').first()
        or User.objects.order_by('id').first()
    )

    if fallback_user:
        Prospect.objects.filter(owner__isnull=True).update(owner_id=fallback_user.id)

    for student in Student.objects.filter(owner__isnull=True).select_related('prospect'):
        owner_id = student.prospect.owner_id or (fallback_user.id if fallback_user else None)
        if owner_id:
            student.owner_id = owner_id
            student.save(update_fields=['owner'])

    for session in CourseSession.objects.filter(owner__isnull=True).select_related('teacher__user'):
        owner_id = session.teacher.user_id or (fallback_user.id if fallback_user else None)
        if owner_id:
            session.owner_id = owner_id
            session.save(update_fields=['owner'])

    for inquiry in Inquiry.objects.filter(owner__isnull=True).select_related('prospect', 'student'):
        owner_id = None
        if inquiry.student_id:
            owner_id = inquiry.student.owner_id
        if not owner_id and inquiry.prospect_id:
            owner_id = inquiry.prospect.owner_id
        if not owner_id and inquiry.assigned_to_id:
            owner_id = inquiry.assigned_to_id
        if not owner_id and fallback_user:
            owner_id = fallback_user.id
        if owner_id:
            inquiry.owner_id = owner_id
            inquiry.save(update_fields=['owner'])

    for invoice in Invoice.objects.filter(owner__isnull=True).select_related('enrollment__student'):
        owner_id = invoice.enrollment.student.owner_id or (fallback_user.id if fallback_user else None)
        if owner_id:
            invoice.owner_id = owner_id
            invoice.save(update_fields=['owner'])

    for payment in Payment.objects.filter(owner__isnull=True).select_related('invoice'):
        owner_id = payment.invoice.owner_id or (fallback_user.id if fallback_user else None)
        if owner_id:
            payment.owner_id = owner_id
            payment.save(update_fields=['owner'])

    for communication in Communication.objects.filter(owner__isnull=True).select_related('prospect', 'student'):
        owner_id = None
        if communication.student_id:
            owner_id = communication.student.owner_id
        if not owner_id and communication.prospect_id:
            owner_id = communication.prospect.owner_id
        if not owner_id and fallback_user:
            owner_id = fallback_user.id
        if owner_id:
            communication.owner_id = owner_id
            communication.save(update_fields=['owner'])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0012_communication_owner_coursesession_owner_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill_owners, noop_reverse),
    ]
