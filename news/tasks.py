import logging

from celery import shared_task
from django.utils import timezone
from django_tenants.utils import get_public_schema_name, get_tenant_model, schema_context

logger = logging.getLogger(__name__)


@shared_task(name="news.notify_published")
def notify_published(news_id: str) -> str:
    """Notify the audience of one published item and mark it notified. Runs in the club schema it was queued from."""
    from .services import notifications

    return f"told {notifications.notify_now(news_id)} account(s)"


@shared_task(name="news.notify_editors_of_pending_review")
def notify_editors_of_pending_review(news_id: str) -> str:
    from .models import News
    from .services import notifications

    item = News.objects.select_related("created_by").filter(pk=news_id).first()
    return f"told {notifications.notify_editors_of_pending_review(item) if item else 0} editor(s)"


@shared_task(name="news.notify_due_news")
def notify_due_news() -> str:
    """The sweep behind scheduled publishing: notify every published, live item nobody was told about yet, in every
    active club. Scheduled every minute."""
    from .services import notifications

    now = timezone.now()
    items = told = 0
    failed = []
    for club in get_tenant_model().objects.filter(archived_at__isnull=True).exclude(schema_name=get_public_schema_name()):
        try:
            with schema_context(club.schema_name):
                counts = notifications.notify_due(now)
                items += counts["items"]
                told += counts["told"]
        except Exception:  # one broken club must not stop the others
            logger.exception("Notifying published news failed for club %s", club.slug)
            failed.append(club.slug)
    return f"notified {told} account(s) for {items} news item(s)" + (f"; failed: {', '.join(failed)}" if failed else "")
