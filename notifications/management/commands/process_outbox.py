from features.commands import ScheduledJobCommand
from notifications import outbox


class Command(ScheduledJobCommand):
    help = "Works off outbox entries (notifications, game pushes) a restart or crash left behind, then purges old entries."
    job_name = "notifications.tasks.process_outbox"

    def handle(self, *args, **options):
        processed = outbox.recover()
        purged = outbox.purge()
        return f"Processed {processed} outbox entry(ies), purged {purged} old one(s)."
