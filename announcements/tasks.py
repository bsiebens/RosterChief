from celery import shared_task

from features.celery_support import run_job


@shared_task(name="announcements.publish-scheduled-announcements")
def publish_scheduled_announcements():
    return run_job("publish_scheduled_announcements")
