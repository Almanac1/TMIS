from django.core.management.base import BaseCommand

from core.services.meditator_transitions import backfill_fictitious_meditator_transitions


class Command(BaseCommand):
    help = "Backfill system-managed Student -> Meditator transitions for fictitious data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--target-ratio",
            type=float,
            default=0.30,
            help="Minimum fraction of fictitious students to ensure as eligible meditators (default: 0.30).",
        )

    def handle(self, *args, **options):
        target_ratio = options["target_ratio"]
        summary = backfill_fictitious_meditator_transitions(target_ratio=target_ratio)

        self.stdout.write(self.style.SUCCESS("Meditator backfill complete."))
        for key, value in summary.items():
            self.stdout.write(f"- {key}: {value}")
