"""Email club admins about platform fees they owe.

Reports by default and only sends with --commit, the same posture as archive_overdue_clubs
and for a related reason: this one mails paying customers, and a bad clock, a bad import or a
rehearsal against production data should cost you a confusing dry-run listing rather than a
mailshot you cannot recall.

Idempotent across runs by design -- one reminder per due per escalation level, tracked on
Due.last_reminder_level -- so a daily cron does not produce a daily email.
"""

from django.core.management.base import CommandError

from billing.services.reminders import reminders_to_send, send_reminder
from club.models import Club
from features.commands import ScheduledJobCommand


class Command(ScheduledJobCommand):
    help = "Email club admins about outstanding platform fees (dry run unless --commit)."
    job_name = "billing.tasks.send_billing_reminders"

    def add_arguments(self, parser):
        parser.add_argument("--commit", action="store_true", help="Actually send. Without this the command only reports.")
        parser.add_argument("--force", action="store_true", help="Re-send even where a reminder already went out at this level.")

    def handle(self, *args, **options):
        clubs = Club.objects.active().select_related("subscription", "subscription__plan").order_by("name")
        results = reminders_to_send(clubs, force=options["force"])

        sendable = [result for result in results if result.sent]
        skipped = [result for result in results if not result.sent]

        if not results:
            self.stdout.write(self.style.SUCCESS("Nothing owing. No reminders to send."))
            return "Nothing owing. No reminders to send."

        for result in skipped:
            style = self.style.WARNING if result.recipients else self.style.ERROR
            self.stdout.write(style(f"{result.club} — skipped: {result.skipped_reason}"))

        for result in sendable:
            self.stdout.write(f"{result.notice.level:>7} · {result.club} — €{result.notice.amount_outstanding} owed, {result.notice.days_until_archive}d to archive → {', '.join(result.recipients)}")

        if not options["commit"]:
            message = f"Dry run: {len(sendable)} reminder(s) would be sent. Re-run with --commit to send them."
            self.stdout.write(self.style.WARNING(f"\n{message}"))
            return message

        failures = []
        for result in sendable:
            try:
                send_reminder(result.club, result.notice, recipients=result.recipients)
            except OSError as error:
                # One bad address or a momentary SMTP failure must not stop the rest of the
                # run: the clubs further down the list are the ones closest to being archived.
                failures.append(f"{result.club}: {error}")
                self.stdout.write(self.style.ERROR(f"{result.club} — {error}"))

        sent = len(sendable) - len(failures)
        self.stdout.write(self.style.SUCCESS(f"\nSent {sent} reminder(s)."))

        if failures:
            # Non-zero so cron mails you: a club that could not be warned is a club that gets
            # archived without notice.
            raise CommandError(f"{len(failures)} reminder(s) could not be sent:\n  " + "\n  ".join(failures))

        return f"Sent {sent} reminder(s)."
