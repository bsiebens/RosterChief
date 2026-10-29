"""Bug reports and the calendar subscription link (API.md §7.2), club host."""

from django.urls import reverse
from ninja import Router

from api.core.errors import ApiError
from api.core.scope import club_scope
from bugs.forms import BugReportForm
from bugs.models import BugReport
from bugs.services import file_report
from mobile.models import CalendarFeedToken

from ..schemas import BugReportIn, BugReportListOut, BugReportOut, CalendarFeedOut, CalendarFeedStateOut, ErrorOut

router = Router(tags=["support"])

ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}


def _bug_out(bug: BugReport) -> dict:
    # Deliberately narrow (BugReport's docstring): never priority, and a note never names its author.
    return {
        "id": bug.pk,
        "title": bug.title,
        "description": bug.description,
        "status": bug.status,
        "created": bug.created,
        "fixed_version": bug.fixed_version or None if bug.status == BugReport.Status.FIXED else None,
        "notes": [{"body": note.body, "created": note.created} for note in bug.notes.order_by("created")],
    }


@router.get("/bug-reports", response={200: BugReportListOut, **ERRORS}, summary="My bug reports")
def bug_reports(request):
    """Reports this account filed in this club, newest first, with the team's replies."""
    scope = club_scope(request)
    if scope.me is None:
        return {"items": []}
    bugs = BugReport.objects.filter(club=scope.club, reported_by=scope.me).order_by("-created").prefetch_related("notes")
    return {"items": [_bug_out(bug) for bug in bugs]}


@router.post("/bug-reports", response={201: BugReportOut, **ERRORS, 422: ErrorOut}, summary="Report a bug")
def report_bug(request, payload: BugReportIn):
    """Filed as the account's own person (never on behalf of a child)."""
    scope = club_scope(request)
    if scope.me is None:
        raise ApiError(403, "forbidden")
    form = BugReportForm(payload.model_dump())
    if not form.is_valid():
        raise ApiError(422, "validation_error", fields={name: [str(message) for message in messages] for name, messages in form.errors.items()})
    bug = file_report(club=scope.club, reported_by=scope.me, title=form.cleaned_data["title"], description=form.cleaned_data["description"])
    return 201, _bug_out(bug)


@router.get("/calendar-feed", response={200: CalendarFeedStateOut, **ERRORS}, summary="Calendar subscription state")
def calendar_feed_state(request):
    club_scope(request)
    return {"has_feed": CalendarFeedToken.objects.filter(user=request.user).exists()}


@router.post("/calendar-feed", response={200: CalendarFeedOut, **ERRORS}, summary="Create or replace the calendar link")
def calendar_feed_issue(request):
    """Returns the subscription URL, the only time it's ever shown (only a hash is
    stored). Calling it again replaces the link: the old URL stops working at once,
    which is also how a leaked link is revoked. One link covers every club."""
    club_scope(request)
    raw_token = CalendarFeedToken.issue(request.user)
    feed_url = request.build_absolute_uri(reverse("mobile:calendar_feed", kwargs={"token": raw_token}))
    webcal_url = feed_url.replace("https://", "webcal://", 1).replace("http://", "webcal://", 1)
    return {"feed_url": feed_url, "webcal_url": webcal_url}
