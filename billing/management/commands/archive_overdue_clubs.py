"""Archive clubs whose billing period has gone unpaid past its grace period.

Reports by default and only acts with --commit. That asymmetry is the point: this command
switches off paying customers, and a cron misconfiguration, a clock skew or a bad import
should cost you a confusing email, not a morning of angry clubs.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from billing.services.dues import archivable_clubs


class Command(BaseCommand):
    help = "Archive clubs that are unpaid past their grace period (dry run unless --commit)."

    def add_arguments(self, parser):
        parser.add_argument("--commit", action="store_true", help="Actually archive them. Without this the command only reports.")

    def handle(self, *args, **options):
        today = timezone.localdate()
        overdue = list(archivable_clubs(today))

        if not overdue:
            self.stdout.write(self.style.SUCCESS("Nothing overdue past grace."))
            return

        for due in overdue:
            days = (today - due.grace_until).days
            self.stdout.write(f"{due.club} — {due.tier}, {due.balance} owed, grace ended {due.grace_until} ({days} day{'s'[: days != 1]} ago)")

        if not options["commit"]:
            self.stdout.write(self.style.WARNING(f"\nDry run: {len(overdue)} club(s) would be archived. Re-run with --commit to do it."))
            return

        for due in overdue:
            due.club.archive()
        self.stdout.write(self.style.SUCCESS(f"\nArchived {len(overdue)} club(s). Their data is kept; restoring re-opens billing."))
