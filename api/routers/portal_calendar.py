"""The member's personal calendar link: ``/portal/me/calendar-feed`` to read, enable/replace and switch it off, and the public
``/public/calendar/<token>.ics`` that calendar apps subscribe to (no login: the secret token is the credential)."""

from django.http import HttpResponse
from django_tenants.utils import get_public_schema_name
from ninja import Router, Schema
from ninja.errors import HttpError

from api.auth import CookieAuth, TokenAuth
from api.routers.portal import _people, _viewer
from events.services import calendar_feed
from members.models import Member

router = Router(auth=[TokenAuth(door=False), CookieAuth(door=False)])
public_router = Router()


class CalendarFeedOut(Schema):
    enabled: bool
    #: The secret in the link (``/api/v1/public/calendar/<url_token>.ics``); null while the link is off.
    url_token: str | None = None


def _out(feed) -> dict:
    return {"enabled": feed is not None, "url_token": feed.token if feed is not None else None}


@router.get("/calendar-feed", response=CalendarFeedOut, summary="My personal calendar link and whether it is on", operation_id="portal_calendar_feed")
def get_feed(request):
    _viewer(request)
    return _out(calendar_feed.get(request.user))


@router.put("/calendar-feed", response=CalendarFeedOut, summary="Switch the calendar link on, or replace it (the old link stops working)", operation_id="portal_calendar_feed_enable")
def enable_feed(request, rotate: bool = False):
    viewer = _viewer(request)
    return _out(calendar_feed.enable(request.user, viewer, rotate=rotate))


@router.delete("/calendar-feed", response=CalendarFeedOut, summary="Switch the calendar link off", operation_id="portal_calendar_feed_disable")
def disable_feed(request):
    viewer = _viewer(request)
    calendar_feed.disable(request.user, viewer)
    return _out(None)


@public_router.get("/{token}.ics", auth=None, summary="A person's calendar feed (iCalendar) through their secret link", operation_id="public_calendar_feed")
def public_feed(request, token: str):
    """The signed-in person's events and those of the children they manage, from a month back to a year ahead. Unknown or switched-off
    tokens, accounts that left the club and the platform host all answer 404."""
    club = request.tenant
    if club.schema_name == get_public_schema_name():
        raise HttpError(404, "Not found.")
    feed = calendar_feed.feed_for_token(token)
    if feed is None or not feed.user.tenants.filter(pk=club.pk).exists():
        raise HttpError(404, "Not found.")
    viewer = Member.objects.filter(user=feed.user).first()
    if viewer is None:
        raise HttpError(404, "Not found.")
    body = calendar_feed.build_ics(viewer, _people(viewer), club_name=club.name, host=request.get_host().split(":")[0])
    response = HttpResponse(body, content_type="text/calendar; charset=utf-8")
    response["Cache-Control"] = "private, max-age=300"
    response["Content-Disposition"] = 'inline; filename="calendar.ics"'
    return response
