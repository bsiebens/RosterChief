"""Issue the next billing period for clubs whose current one is running out.

Unlike archive_overdue_clubs, this ACTS by default and only previews with --dry-run. The
asymmetry is deliberate and runs the other way: archiving switches off a paying customer, so
not acting is the safe failure. Here, not acting means a club keeps using the platform for
free — and because nothing is owed, no dashboard number goes red and the archive job never
fires either. A missed renewal is silent, and silence is the expensive failure.
"""

from django.core.management.base import CommandError

from billing.models import RENEWAL_LEAD_DAYS
from billing.services import BillingError
from billing.services.dues import renew, subscriptions_due_for_renewal
from features.commands import MaintenanceAwareCommand


class Command(MaintenanceAwareCommand):
    help = "Open the next billing period for clubs whose current period ends soon."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report what would be issued, and issue nothing.")
        parser.add_argument("--lead-days", type=int, default=RENEWAL_LEAD_DAYS, help=f"Issue this many days before the period ends (default {RENEWAL_LEAD_DAYS}).")

    def handle(self, *args, **options):
        due_for_renewal = subscriptions_due_for_renewal(lead_days=options["lead_days"])

        if not due_for_renewal:
            self.stdout.write(self.style.SUCCESS("Nothing to renew."))
            return

        failures = []
        for subscription in due_for_renewal:
            club = subscription.club

            if options["dry_run"]:
                self.stdout.write(f"would renew {club} — {subscription.tier}, current period ends {subscription.latest_period_end or 'never opened'}")
                continue

            try:
                due = renew(subscription)
            except BillingError as error:
                # One unpriced tier must not stop every other club from being billed.
                failures.append(f"{club}: {error}")
                self.stdout.write(self.style.ERROR(f"{club} — {error}"))
                continue

            self.stdout.write(self.style.SUCCESS(f"{club} — {due.period_start} to {due.period_end}, {due.amount} ({due.invoice.number})"))

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING(f"\nDry run: {len(due_for_renewal)} club(s) would be renewed."))
            return

        if failures:
            # Non-zero, so cron mails you: a club that could not be billed is revenue quietly
            # not being collected.
            raise CommandError(f"{len(failures)} club(s) could not be renewed:\n  " + "\n  ".join(failures))
