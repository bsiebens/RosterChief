from celery import shared_task
from django.utils import timezone

from .models import Announcement
from .services import publish


@shared_task(name="announcements.publish-scheduled-announcements")
def publish_scheduled_announcements():
    due = Announcement.objects.filter(status=Announcement.Status.PENDING, scheduled_for__lte=timezone.now())

    published = 0
    for announcement in due:
        publish(announcement)
        published += 1

    return {
        "status": "SUCCESS",
        "result": f"Published {published} announcement(s)",
        "context": {
            "periodic_task_name": "publish scheduled announcements",
        },
    }
