"""News and notifications (API.md §7.2), club host. Queries come from news.services and
notifications.services, the same ones the member web app uses."""

import uuid
from typing import Annotated

from django.contrib.contenttypes.prefetch import GenericPrefetch
from django.shortcuts import get_object_or_404
from django.utils.translation import get_language
from ninja import Query, Router

from events.models import Event
from formbuilder.models import FormSend
from news.models import News
from news.services import live_news, localized_news, member_news, render_body_html
from notifications.services import clear_all, inbox_for, mark_all_read, mark_read, unread_count

from ..errors import ApiError
from ..pagination import DEFAULT_LIMIT, MAX_LIMIT, paginate
from ..presenters import news_summary, notification_out
from ..schemas import CountOut, ErrorOut, NewsDetailOut, NewsPageOut, NotificationOut, NotificationPageOut, UnreadCountOut
from ..scope import club_scope

router = Router(tags=["news & notifications"])

ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
PAGING = {400: ErrorOut}
LIMIT_HELP = f"Page size, default {DEFAULT_LIMIT}, at most {MAX_LIMIT}."
CURSOR_HELP = "`next_cursor` from the previous page. Opaque."


@router.get("/news", response={200: NewsPageOut, **ERRORS, **PAGING}, summary="News feed")
def news_list(request, cursor: Annotated[str | None, Query(description=CURSOR_HELP)] = None, limit: Annotated[int | None, Query(description=LIMIT_HELP)] = None):
    """Published news for members, newest first. Titles and excerpts follow
    `Accept-Language` (English when asked for and written, the club's language otherwise)."""
    scope = club_scope(request)
    page = paginate(member_news(scope.club).prefetch_related("teams", "photos"), sort_field="-published_at", cursor=cursor, limit=limit)
    return {"items": [news_summary(request, item) for item in page.items], "next_cursor": page.next_cursor}


@router.get("/news/{slug}", response={200: NewsDetailOut, **ERRORS}, summary="One news item")
def news_detail(request, slug: str):
    scope = club_scope(request)
    item = get_object_or_404(live_news(scope.club).prefetch_related("teams", "photos"), slug=slug)
    _title, body, language = localized_news(item, get_language())
    photos = sorted(item.photos.all(), key=lambda photo: (not photo.is_main, photo.ordering))
    return {
        **news_summary(request, item),
        "language": language,
        "body_html": render_body_html(body),
        "photos": [{"url": request.build_absolute_uri(photo.image.url), "is_main": photo.is_main, "focal_x": photo.focal_x, "focal_y": photo.focal_y} for photo in photos],
    }


@router.get("/notifications", response={200: NotificationPageOut, **ERRORS, **PAGING}, summary="Notifications inbox")
def notifications(
    request,
    member_id: Annotated[str | None, Query(description="A managed person's id, or `all` (the default).")] = None,
    cursor: Annotated[str | None, Query(description=CURSOR_HELP)] = None,
    limit: Annotated[int | None, Query(description=LIMIT_HELP)] = None,
):
    """Newest first. A parent sees their children's notifications too; `member_id` says
    whose each one is. `source` points at what to open."""
    scope = club_scope(request)
    queryset = inbox_for(scope.club, scope.people_for(member_id)).prefetch_related(GenericPrefetch("source", [News.objects.all(), Event.objects.all(), FormSend.objects.all()]))
    page = paginate(queryset, sort_field="-created", cursor=cursor, limit=limit)
    return {"items": [notification_out(notification) for notification in page.items], "next_cursor": page.next_cursor}


@router.get("/notifications/unread-count", response={200: UnreadCountOut, **ERRORS}, summary="Unread count")
def notifications_unread(request):
    """Across every managed person: use it for the app icon badge."""
    scope = club_scope(request)
    return {"unread": unread_count(scope.club, scope.people)}


@router.post("/notifications/read-all", response={200: CountOut, **ERRORS}, summary="Mark all read")
def notifications_read_all(request):
    scope = club_scope(request)
    return {"count": mark_all_read(scope.club, scope.people)}


@router.post("/notifications/{notification_id}/read", response={200: NotificationOut, **ERRORS}, summary="Mark one read")
def notification_read(request, notification_id: uuid.UUID):
    """Safe to repeat; returns the notification with its `read_at`."""
    scope = club_scope(request)
    notification = mark_read(scope.club, scope.people, notification_id)
    if notification is None:
        raise ApiError(404, "not_found")
    return notification_out(notification)


@router.delete("/notifications", response={200: CountOut, **ERRORS}, summary="Clear the inbox")
def notifications_clear(request):
    """Deletes every notification of every managed person (not just marks them read)."""
    scope = club_scope(request)
    return {"count": clear_all(scope.club, scope.people)}
