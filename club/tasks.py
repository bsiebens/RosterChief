from celery import shared_task

from features.celery_support import run_job


@shared_task(name="club.generate-seasons")
def generate_seasons():
    return run_job("generate_seasons")


# --- Management API jobs (api.core.jobs): started by api/panel/routers/fees.py, polled at GET /manage/jobs/{id}.
# They live here so the Celery worker's autodiscovery registers them without importing the API.

from api.core.jobs import JobContext, job_task  # noqa: E402


@job_task(name="club.send-dues-invoices")
def send_dues_invoices_job(ctx: JobContext, membership_ids: list[str], due_in_days: int):
    from club.services.invoicing import send_dues_invoices

    sent, failed, unreachable = send_dues_invoices(ctx.club, membership_ids, due_in_days=due_in_days)
    return {"sent": sent, "failed": failed, "unreachable": unreachable}


@job_task(name="club.send-invoice-reminders")
def send_invoice_reminders_job(ctx: JobContext):
    from club.services.invoicing import send_all_reminders

    sent, failed = send_all_reminders(ctx.club)
    return {"sent": sent, "failed": failed}
