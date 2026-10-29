# Management API jobs (api.core.jobs): started by api/panel/routers/members_import.py, polled at GET /manage/jobs/{id}.
# They live here so the Celery worker's autodiscovery registers them without importing the API.

from api.core.jobs import JobContext, job_task


@job_task(name="members.import")
def import_members_job(ctx: JobContext, rows: list, can_set_status_and_fee: bool, family_choices: dict):
    """Creates the members of the validated ``rows`` (members.services.member_import.import_members)."""
    from members.services.member_import import import_members

    return import_members(ctx.club, rows, can_set_status_and_fee=can_set_status_and_fee, family_choices=family_choices, on_progress=ctx.progress)
