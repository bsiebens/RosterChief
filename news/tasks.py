from celery import shared_task

from features.celery_support import run_job


@shared_task(name="news.notify-published-news")
def notify_published_news():
    return run_job("notify_published_news")
