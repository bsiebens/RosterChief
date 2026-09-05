from django.utils import timezone

from announcements.models import Announcement
from announcements.services import publish
from features.commands import ScheduledJobCommand


class Command(ScheduledJobCommand):
    """The periodic sweep behind a scheduled (not-immediate) announcement --
    announcements.services.create_and_confirm only publishes straight away when
    nothing was scheduled or the chosen time has already arrived; anything
    scheduled further out is left PENDING for this sweep to catch once its
    scheduled_for time arrives. Same "fire at a future moment, no worker left to
    hold a delayed task" shape as news.tasks.notify_news_published.
    """

    help = "Push any scheduled announcement whose scheduled time has arrived."
    job_name = "announcements.tasks.publish_scheduled_announcements"

    def handle(self, *args, **options):
        due = Announcement.objects.filter(status=Announcement.Status.PENDING, scheduled_for__lte=timezone.now())

        published = 0
        for announcement in due:
            publish(announcement)
            published += 1

        return f"Published {published} announcement(s)."
