"""Management API: importing members from a spreadsheet (API.md §17.3, phase A).

The JSON counterpart of the ``/manage/members/import`` screens (management.views.MemberImport*):

* ``GET /manage/members/import/template`` -- the blank .xlsx (any management staff, as the web);
* ``POST /manage/members/import`` -- the filled-in template as multipart. ``dry_run=true`` answers
  the preview the web shows between upload and confirm (what would be created, which rows are
  skipped and why, which existing families a ``family_group`` might be); without it the import runs
  as a Celery job (202 ``JobOut``, polled at ``GET /manage/jobs/{id}``) whose ``result`` is
  ``ImportResultOut``. Member admins; ``status``/``fee_status`` columns count for club admins only.

Parsing and creating are the web's: management.bulk_import (read / validate) and
members.services.member_import (create). Nothing is kept between preview and import -- the file is
sent again, and the rows are re-validated by the job, so what is created is what the rows say.
"""

import json
from typing import Annotated

from django.http import HttpResponse
from django.utils.translation import gettext as _
from ninja import File, Form, Router, UploadedFile

from api.core.errors import ApiError
from api.core.jobs import start_job
from api.core.schemas import ErrorOut, JobOut
from api.core.uploads import SPREADSHEET_TYPES, validate_upload
from api.panel.guards import panel_club, require
from api.panel.schemas import members_import as s
from club.services.access import can_manage_members, current_season, is_club_admin
from management.bulk_import import build_member_import_template, match_candidate_families, parse_member_import_rows, read_member_import_workbook
from management.forms import MEMBER_IMPORT_MAX_FILE_SIZE
from members.tasks import import_members_job

router = Router(tags=["panel: member import"])

PREFIX = "/manage/members/import"
ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
UPLOAD_ERRORS = {413: ErrorOut, 415: ErrorOut, 422: ErrorOut}
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _family_choices(raw: str) -> dict:
    """``{"Doe family": "<family uuid>" | "new"}`` as JSON text -> dict (422 on ``family_choices`` otherwise)."""
    if not raw or not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except ValueError:
        value = None
    if not isinstance(value, dict) or not all(isinstance(key, str) and isinstance(choice, str) for key, choice in value.items()):
        raise ApiError(422, "validation_error", fields={"family_choices": [_('Send a JSON object of family group to family id (or "new").')]})
    return value


def _preview(club, rows: list, *, can_set_status_and_fee: bool) -> dict:
    results = parse_member_import_rows(rows, club, can_set_status_and_fee=can_set_status_and_fee)
    season = current_season(club)
    out_rows = [
        {
            "line_number": result["line_number"],
            "valid": result["member"] is not None,
            "errors": [str(error) for error in result["errors"]],
            "first_name": result["raw"].get("first_name", ""),
            "last_name": result["raw"].get("last_name", ""),
            "email": result["raw"].get("email", ""),
            "family_group": result["family_group"] or None,
            "family_role": result["family_role"] or None,
            "membership_kind": result["membership_kwargs"]["kind"],
        }
        for result in results
    ]
    groups = [
        {
            "family_group": group["family_group"],
            "candidates": [{"family_id": family.pk, "name": str(family), "members": [str(link.member) for link in family.memberships.all()]} for family in group["candidates"]],
        }
        for group in match_candidate_families(results, club)
    ]
    valid = sum(1 for row in out_rows if row["valid"])
    return {"season": str(season) if season else None, "valid_count": valid, "skipped_count": len(out_rows) - valid, "rows": out_rows, "family_groups": groups}


@router.get(PREFIX + "/template", response={200: None, **ERRORS}, summary="Download the blank import template (.xlsx)")
def import_template(request):
    """The spreadsheet to fill in: one row per member, `family_group` + `family_role` to link a household. Any management staff."""
    panel_club(request)
    response = HttpResponse(content_type=XLSX)
    response["Content-Disposition"] = 'attachment; filename="member_import_template.xlsx"'
    build_member_import_template().save(response)
    return response


@router.post(PREFIX, response={200: s.ImportPreviewOut, 202: JobOut, **ERRORS, **UPLOAD_ERRORS}, summary="Preview or start a member import")
def import_upload(
    request,
    file: File[UploadedFile],
    dry_run: Annotated[bool, Form(description="Only validate and preview; nothing is created.")] = False,
    family_choices: Annotated[str, Form(description='JSON object `{family_group: family_id | "new"}` for groups that may match an existing family; unlisted groups get a new family.')] = "",
):
    """`multipart/form-data` with the filled-in template as `file` (.xlsx, at most 5 MB, 2000 rows). A file that is
    not the template is 422 on `file`. Without `dry_run` the import is a job: 202 with the job, whose `result` is
    `{created, skipped, rostered, skipped_rows}`. Invalid rows are skipped, not fatal. Member admins."""
    club = panel_club(request)
    require(request, club, can_manage_members)
    choices = _family_choices(family_choices)
    upload = validate_upload(file, field="file", max_bytes=MEMBER_IMPORT_MAX_FILE_SIZE, content_types=SPREADSHEET_TYPES, extensions=(".xlsx",), generic_ok=True)
    try:
        rows = read_member_import_workbook(upload)
    except ValueError as error:
        raise ApiError(422, "validation_error", fields={"file": [str(error)]}) from None
    except Exception:
        raise ApiError(422, "validation_error", fields={"file": [_("This file could not be read as an Excel workbook.")]}) from None

    admin = is_club_admin(request.user, club)
    if dry_run:
        return 200, _preview(club, rows, can_set_status_and_fee=admin)
    return 202, start_job(request, import_members_job, rows, admin, choices)
