from celery import shared_task

from features.celery_support import run_job


@shared_task(name="formbuilder.send-form-reminders")
def send_form_reminders():
    return run_job("send_form_reminders")
