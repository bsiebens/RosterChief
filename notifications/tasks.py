from celery import shared_task

from features.celery_support import run_job


@shared_task(name="notifications.retry-failed-deliveries")
def retry_failed_deliveries():
    return run_job("retry_failed_deliveries")


@shared_task(name="notifications.process-outbox")
def process_outbox():
    return run_job("process_outbox")


@shared_task(name="notifications.check-push-health")
def check_push_health():
    return run_job("check_push_health")
