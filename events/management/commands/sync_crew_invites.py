from events.services.crew_invites import resync_upcoming_invites
from features.commands import ScheduledJobCommand


class Command(ScheduledJobCommand):
    help = "Create any missing referee/official invites for upcoming home games."
    job_name = "events.tasks.sync_crew_invites"

    def handle(self, *args, **options):
        """The safety net behind events.services.crew_invites: invites are created when a
        game is saved, but eligibility can change afterwards (a new referee or official, a
        level's teams, the officials feature switched on) without any game being touched.
        Idempotent -- only rows that are missing are created."""
        created = resync_upcoming_invites()
        return f"Created {created['referee']} referee and {created['official']} official invite(s)."
