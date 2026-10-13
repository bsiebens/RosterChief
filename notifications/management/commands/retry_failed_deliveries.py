from features.commands import ScheduledJobCommand
from notifications import delivery_log


class Command(ScheduledJobCommand):
    help = "Retries notification deliveries (push, email) that failed transiently and are due, then purges delivery-log rows past their retention."
    job_name = "notifications.tasks.retry_failed_deliveries"

    def handle(self, *args, **options):
        retried = delivery_log.retry_due()
        purged = delivery_log.purge()
        return f"Retried {retried} delivery(ies), purged {purged} old log row(s)."
