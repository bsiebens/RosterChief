from django.core.management import call_command


def run_job(command, *args):
    """Run a ScheduledJobCommand from a Celery task, so the task goes through the same
    maintenance / JobToggle checks and JobRun bookkeeping as the control panel's "Run now".
    A CommandError (maintenance, disabled job, partial failure) propagates, so Celery
    records the task as FAILURE."""
    result = call_command(command, *args)

    return {
        "status": "SUCCESS",
        "result": str(result or ""),
    }
