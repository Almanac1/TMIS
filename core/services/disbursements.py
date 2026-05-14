from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import Disbursement, DisbursementStatus, Enrollment


def generate_disbursement_for_enrollment(enrollment: Enrollment):
    """
    Idempotently generate a disbursement snapshot for an enrollment.

    Returns:
        tuple[Disbursement, bool]: (disbursement, created)
    """
    try:
        with transaction.atomic():
            enrollment_locked = Enrollment.objects.select_for_update().select_related(
                "session",
                "session__teacher",
                "session__location",
            ).get(pk=enrollment.pk)

            try:
                return enrollment_locked.disbursement, False
            except Disbursement.DoesNotExist:
                pass

            disbursement = Disbursement.objects.create(
                enrollment=enrollment_locked,
                teacher=enrollment_locked.session.teacher,
                location=enrollment_locked.session.location,
                balance_due_snapshot=enrollment_locked.balance_due or Decimal("0.00"),
                teacher_amount=Decimal("0.00"),
                location_amount=Decimal("0.00"),
                ico_amount=Decimal("0.00"),
                disbursement_date=timezone.localdate(),
                status=DisbursementStatus.PENDING,
            )
            return disbursement, True
    except IntegrityError:
        # Handles rare race conditions where another process created it first.
        return Disbursement.objects.get(enrollment=enrollment), False
