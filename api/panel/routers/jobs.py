"""Progress of long-running actions started by other endpoints (api.core.jobs)."""

import uuid

from django.shortcuts import get_object_or_404
from ninja import Router

from api.core.auth import require_club_member
from api.core.jobs import job_out
from api.core.schemas import ErrorOut, JobOut
from api.models import Job

router = Router(tags=["panel: jobs"])

ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}


@router.get("/manage/jobs/{uuid:job_id}", response={200: JobOut, **ERRORS}, summary="Status of a background job")
def get_job(request, job_id: uuid.UUID):
    """`pending` -> `running` -> `succeeded` (with `result`) or `failed` (with `error`). Only the
    user who started the job, in the same club, can see it; anyone else gets 404."""
    club = require_club_member(request)
    return job_out(get_object_or_404(Job, pk=job_id, club=club, user=request.user))
