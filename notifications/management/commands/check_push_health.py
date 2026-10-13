from features.commands import ScheduledJobCommand
from notifications import alerts


class Command(ScheduledJobCommand):
    help = "Checks push health (providers, retry queue, configuration, outbox) and emails the platform admins once per outage, with reminders and a recovery notice."
    job_name = "notifications.tasks.check_push_health"

    def handle(self, *args, **options):
        counts = alerts.evaluate()
        return "Push health: " + ", ".join(f"{n} {name}" for name, n in counts.items()) + "."
