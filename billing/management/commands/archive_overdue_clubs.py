"""Archive clubs whose billing period has gone unpaid past its grace period.

Reports by default and only acts with --commit. That asymmetry is the point: this command
switches off paying customers, and a cron misconfiguration, a clock skew or a bad import
should cost you a confusing email, not a morning of angry clubs.
"""

from django.utils import timezone

from billing.services.dues import archivable_clubs
from features.commands import ScheduledJobCommand


class Command(ScheduledJobCommand):
    help = "Archive clubs that are unpaid past their grace period (dry run unless --commit)."
    job_name = "billing.tasks.archive_overdue_clubs"

    def add_arguments(self, parser):
        parser.add_argument("--commit", action="store_true", help="Actually archive them. Without this the command only reports.")

    def handle(self, *args, **options):
        today = timezone.localdate()
        overdue = list(archivable_clubs(today))

        if not overdue:
            self.stdout.write(self.style.SUCCESS("Nothing overdue past grace."))
            return "Nothing overdue past grace."

        for due in overdue:
            days = (today - due.grace_until).days
            self.stdout.write(f"{due.club} — {due.plan}, {due.balance} owed, grace ended {due.grace_until} ({days} day{'s'[: days != 1]} ago)")

        if not options["commit"]:
            message = f"Dry run: {len(overdue)} club(s) would be archived. Re-run with --commit to do it."
            self.stdout.write(self.style.WARNING(f"\n{message}"))
            return message

        for due in overdue:
            due.club.archive()

        message = f"Archived {len(overdue)} club(s). Their data is kept; restoring re-opens billing."
        self.stdout.write(self.style.SUCCESS(f"\n{message}"))
        return message
