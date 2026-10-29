"""Management bug reports (``/manage/bugs``): the JSON of the "Report a bug" screen.

Any member of club staff (``ClubStaffRequiredMixin``). ``GET`` lists the bug reports the caller filed
in this club, newest first; ``POST`` files one through ``bugs.services.file_report`` (the service the
web form calls) with ``bugs.forms.BugReportForm`` doing the validation. Only title, description,
status, notes and the fix date/version are exposed: priority, club and reporter are control-panel
fields. The control panel's own bug screens are not part of this API.

Unlike the web list, which shows everything the person ever filed whichever club they were managing,
the API is scoped to this club's reports like every other club resource.
"""

from django.utils.translation import gettext_lazy as _
from ninja import Router

from api.core.errors import ApiError
from api.core.forms import form_error
from api.core.idempotency import idempotent
from api.core.pagination import paginate
from api.core.schemas import ErrorOut
from api.panel.guards import panel_club
from api.panel.schemas import bugs as s
from bugs.forms import BugReportForm
from bugs.models import BugReport
from bugs.services import file_report
from members.services.lookup import get_request_member

router = Router(tags=["panel: bugs"])

PREFIX = "/manage/bugs"
ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}


def _out(bug: BugReport) -> dict:
    return {
        "id": bug.pk,
        "title": bug.title,
        "description": bug.description,
        "status": bug.status,
        "created": bug.created,
        "fixed_at": bug.fixed_at,
        "fixed_version": bug.fixed_version,
        "notes": [{"id": note.pk, "body": note.body, "created": note.created} for note in bug.notes.all()],
    }


@router.get(PREFIX, response={200: s.BugListOut, **ERRORS}, summary="The caller's bug reports in this club")
def list_bugs(request, cursor: str | None = None, limit: int | None = None):
    """Newest first, cursor paged. Club staff."""
    club = panel_club(request)
    member = get_request_member(request)
    queryset = BugReport.objects.filter(club=club, reported_by=member).prefetch_related("notes") if member is not None else BugReport.objects.none()
    page = paginate(queryset, sort_field="-created", cursor=cursor, limit=limit)
    return {"items": [_out(bug) for bug in page.items], "next_cursor": page.next_cursor}


@router.post(PREFIX, response={201: s.BugOut, 422: ErrorOut, **ERRORS}, summary="Report a bug")
def report_bug(request, payload: s.BugIn):
    """Files a report and tells the platform team. `422` without a member profile on the account. Honours `Idempotency-Key`. Club staff."""
    club = panel_club(request)

    def work():
        member = get_request_member(request)
        if member is None:
            raise ApiError(422, "validation_error", _("No member profile is linked to your account."))
        form = BugReportForm({"title": payload.title, "description": payload.description})
        if not form.is_valid():
            raise form_error(form)
        bug = file_report(club=club, reported_by=member, title=form.cleaned_data["title"], description=form.cleaned_data["description"])
        return 201, _out(bug)

    return idempotent(request, work)
