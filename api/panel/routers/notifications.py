"""Management notifications (``/manage/notifications``): the topbar bell of the /manage screens.

Any member of club staff (``ClubStaffRequiredMixin``). These are the caller's *own* notifications in
this club (the web bell is keyed to the signed-in person's member, not to a managed family), served by
``notifications.services`` -- the same service the web bell actions use. Mark-all-read and clear-all
are idempotent by nature (they act on the current state), so they need no ``Idempotency-Key``.
"""

from typing import Annotated

from django.contrib.contenttypes.prefetch import GenericPrefetch
from ninja import Query, Router

from api.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, paginate
from api.core.schemas import ErrorOut
from api.panel.guards import panel_club
from api.protected.presenters import notification_out
from api.protected.schemas import CountOut, NotificationPageOut, UnreadCountOut
from events.models import Event
from formbuilder.models import FormSend
from members.services.lookup import get_request_member
from news.models import News
from notifications.services import clear_all, inbox_for, mark_all_read, unread_count

router = Router(tags=["panel: notifications"])

PREFIX = "/manage/notifications"
ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
PAGING = {400: ErrorOut}


def _people(request) -> list:
    member = get_request_member(request)
    return [member] if member is not None else []


@router.get(PREFIX, response={200: NotificationPageOut, **ERRORS, **PAGING}, summary="The caller's notifications in this club")
def list_notifications(
    request,
    cursor: Annotated[str | None, Query(description="`next_cursor` from the previous page. Opaque.")] = None,
    limit: Annotated[int | None, Query(description=f"Page size, default {DEFAULT_LIMIT}, at most {MAX_LIMIT}.")] = None,
):
    """Newest first, cursor paged. Empty for an account without a member profile. Club staff."""
    club = panel_club(request)
    queryset = inbox_for(club, _people(request)).prefetch_related(GenericPrefetch("source", [News.objects.all(), Event.objects.all(), FormSend.objects.all()]))
    page = paginate(queryset, sort_field="-created", cursor=cursor, limit=limit)
    return {"items": [notification_out(notification) for notification in page.items], "next_cursor": page.next_cursor}


@router.get(PREFIX + "/unread-count", response={200: UnreadCountOut, **ERRORS}, summary="Unread count (the bell badge)")
def notifications_unread(request):
    club = panel_club(request)
    return {"unread": unread_count(club, _people(request))}


@router.post(PREFIX + "/read-all", response={200: CountOut, **ERRORS}, summary="Mark every notification read")
def read_all(request):
    """All of the caller's unread notifications in this club, not just the page shown. Club staff."""
    club = panel_club(request)
    return {"count": mark_all_read(club, _people(request))}


@router.post(PREFIX + "/clear-all", response={200: CountOut, **ERRORS}, summary="Delete every notification")
def clear(request):
    """A hard delete of all the caller's notifications in this club, read or not. Club staff."""
    club = panel_club(request)
    return {"count": clear_all(club, _people(request))}
