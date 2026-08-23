from django.utils import timezone

from features.commands import ScheduledJobCommand
from news.models import News
from news.services import send_publish_notification


class Command(ScheduledJobCommand):
    """The periodic sweep behind News.publish()'s "notify members" option --
    catches any published news item whose publish_at has arrived and hasn't
    been notified yet, and sends it. Replaces what used to be a Celery task
    scheduled with an ETA matching published_at (fire once, at an arbitrary
    future moment) -- there's no worker left to hold a delayed task, so this
    runs every 15 minutes instead and checks what's actually due, same shape
    as events.management.commands.publish_scheduled_lineups's own sweep for
    an analogous "fire at a future moment" problem.

    News.notified_at is the idempotency marker: null means "not sent yet",
    set means "done" -- either because this sweep already sent it, or
    because management.views.NewsPublishView.form_valid set it immediately
    at publish time when the publisher unchecked "notify members" (opting
    out entirely, not just delaying) -- either way, this sweep has nothing
    left to do for that item and skips it.
    """

    help = "Notify the audience of any published news item whose publish time has arrived and hasn't been notified yet."
    job_name = "news.tasks.notify_news_published"

    def handle(self, *args, **options):
        due = News.objects.filter(status=News.Status.PUBLISHED, published_at__lte=timezone.now(), notified_at__isnull=True).select_related("club").prefetch_related("teams")

        items_notified = 0
        members_notified = 0
        for news_item in due:
            notifications = send_publish_notification(news_item)
            members_notified += len(notifications)
            items_notified += 1

            news_item.notified_at = timezone.now()
            news_item.save(update_fields=["notified_at"])

        return f"Notified {members_notified} member(s) across {items_notified} news item(s)."
