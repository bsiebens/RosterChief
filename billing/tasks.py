from celery import shared_task

from features.celery_support import run_job


@shared_task(name="billing.renew-subscriptions")
def renew_subscriptions():
    return run_job("renew_subscriptions")


@shared_task(name="billing.send-billing-reminders")
def send_billing_reminders():
    return run_job("send_billing_reminders", "--commit")


@shared_task(name="billing.archive-overdue-clubs")
def archive_overdue_clubs():
    return run_job("archive_overdue_clubs", "--commit")
