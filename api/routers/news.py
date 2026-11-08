"""News (``/news``): any member writes drafts and sees their own items; holders of ``news.publish_newsitems`` see and
edit all, publish (optionally scheduled), unpublish, approve and send back. An item the user may not see is 404, one
they see but may not change is 403. Rules live in ``news.services`` and ``members.services.access``."""

import uuid

from django.utils import timezone
from ninja import File, Query, Router, UploadedFile
from ninja.errors import HttpError

from api.auth import AUTHENTICATORS
from api.schemas_news import NewsDetail, NewsFocalIn, NewsIn, NewsListOut, NewsPatch, NewsPhotoOut, NewsPublishIn, NewsTeamOptionOut
from members.services import access
from news import services as svc
from news.models import News
from teams.models import Team

router = Router(auth=AUTHENTICATORS)  # management door; the rules below and in the services decide who may do what


def _name(member) -> str:
    return str(member)


def _photo_url(photo) -> str | None:
    return photo.image.url if photo is not None and photo.image else None


def _list_item(user, item: News) -> dict:
    return {
        "id": item.pk, "title": item.title, "slug": item.slug, "status": item.status, "visibility": item.visibility,
        "published_at": item.published_at, "is_scheduled": item.is_scheduled, "created": item.created,
        "created_by": {"id": item.created_by_id, "name": _name(item.created_by)} if item.created_by_id else None,
        "team_ids": [t.pk for t in item.teams.all()], "main_photo_url": _photo_url(item.main_photo),
        "can_edit": access.can_edit_news(user, item),
    }  # fmt: skip


def _photo_out(photo) -> dict:
    return {"id": photo.pk, "url": _photo_url(photo) or "", "is_main": photo.is_main, "ordering": photo.ordering, "focal_x": photo.focal_x, "focal_y": photo.focal_y}


def _detail(user, item: News) -> dict:
    item = svc.get_news(user, item.pk) or item  # fresh prefetches
    return {
        **_list_item(user, item),
        "body": item.body, "title_en": item.title_en, "body_en": item.body_en, "body_html": svc.render_body_html(item.body),
        "notified_at": item.notified_at, "photos": [_photo_out(p) for p in item.photos.all()],
        "teams": [{"id": t.pk, "name": t.name} for t in item.teams.all()],
        "can_publish": access.can_publish_news(user), "can_delete": access.can_edit_news(user, item),
    }  # fmt: skip


def _visible(request, news_id: uuid.UUID) -> News:
    item = svc.get_news(request.user, news_id)
    if item is None:
        raise HttpError(404, "Not found.")
    return item


def _photo(item: News, photo_id: uuid.UUID):
    photo = svc.get_photo(item, photo_id)
    if photo is None:
        raise HttpError(404, "Not found.")
    return photo


@router.get("", response=NewsListOut, summary="List news items (publishers: all; others: their own)", operation_id="list_news")
def list_news(request, status: str | None = None, mine: bool = False, live: bool = False, q: str | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    """Newest first. ``status`` filters on draft / pending_review / published, ``mine`` on the user's own items, ``q`` on title and body.
    ``live`` keeps only published items whose publish date has passed (not the scheduled ones) and orders them by publish date."""
    qs = svc.visible_news(request.user)
    if status:
        qs = qs.filter(status=status)
    if live:
        from django.utils import timezone

        qs = qs.filter(status="published", published_at__lte=timezone.now()).order_by("-published_at")
    if mine:
        qs = qs.filter(created_by__user=request.user)
    if q:
        from django.db.models import Q

        qs = qs.filter(Q(title__icontains=q) | Q(body__icontains=q))
    count = qs.count()
    return {"count": count, "results": [_list_item(request.user, i) for i in qs[offset : offset + limit]]}


@router.get("/team-options", response=list[NewsTeamOptionOut], summary="Teams to pick from for a news item", operation_id="news_team_options")
def team_options(request):
    """All teams (id, name, short name) for the team picker and team names; open to everyone who may use news."""
    if not access.can_add_news(request.user):
        raise HttpError(403, "Not allowed.")
    return [{"id": t.pk, "name": t.name, "short_name": t.short_name} for t in Team.objects.order_by("name")]


@router.post("", response={201: NewsDetail}, summary="Write a news item (starts as a draft)", operation_id="create_news")
def create_news(request, payload: NewsIn):
    item = svc.create_news(request.user, **payload.dict())
    return 201, _detail(request.user, item)


@router.get("/{news_id}", response=NewsDetail, summary="One news item", operation_id="get_news")
def get_news(request, news_id: uuid.UUID):
    return _detail(request.user, _visible(request, news_id))


@router.patch("/{news_id}", response=NewsDetail, summary="Edit a news item", operation_id="update_news")
def update_news(request, news_id: uuid.UUID, payload: NewsPatch):
    """Publishers always; the author only while draft or pending review (403 once published)."""
    item = _visible(request, news_id)
    svc.update_news(request.user, item, **payload.dict(exclude_unset=True))
    return _detail(request.user, item)


@router.delete("/{news_id}", response={204: None}, summary="Delete a news item", operation_id="delete_news")
def delete_news(request, news_id: uuid.UUID):
    svc.delete_news(request.user, _visible(request, news_id))
    return 204, None


@router.post("/{news_id}/submit-for-review", response=NewsDetail, summary="Hand a draft to the publishers", operation_id="submit_news_for_review")
def submit_for_review(request, news_id: uuid.UUID):
    """422 unless the item is a draft."""
    item = _visible(request, news_id)
    svc.submit_for_review(request.user, item)
    return _detail(request.user, item)


@router.post("/{news_id}/publish", response=NewsDetail, summary="Publish now or at a future moment (publishers)", operation_id="publish_news")
def publish_news(request, news_id: uuid.UUID, payload: NewsPublishIn):
    """``published_at`` in the future schedules it; empty or past publishes now. ``notify=false`` skips the notification."""
    access.require_publish_news(request.user)
    item = _visible(request, news_id)
    at = payload.published_at
    if at is not None and timezone.is_naive(at):
        at = timezone.make_aware(at)
    if at is not None and at <= timezone.now():
        at = None
    svc.publish(request.user, item, at=at, notify=payload.notify)
    return _detail(request.user, item)


@router.post("/{news_id}/unpublish", response=NewsDetail, summary="Back to draft (publishers)", operation_id="unpublish_news")
def unpublish_news(request, news_id: uuid.UUID):
    access.require_publish_news(request.user)
    item = _visible(request, news_id)
    svc.unpublish(request.user, item)
    return _detail(request.user, item)


@router.post("/{news_id}/send-back", response=NewsDetail, summary="Send an item pending review back to draft (publishers)", operation_id="send_news_back")
def send_back(request, news_id: uuid.UUID):
    access.require_publish_news(request.user)
    item = _visible(request, news_id)
    svc.send_back(request.user, item)
    return _detail(request.user, item)


@router.post("/{news_id}/photos", response={201: NewsPhotoOut}, summary="Add a picture", operation_id="add_news_photo")
def add_photo(request, news_id: uuid.UUID, image: File[UploadedFile]):
    """Multipart, field ``image``: JPEG, PNG or WebP up to 10 MB (422 on field ``image`` otherwise). The first picture becomes the main one."""
    item = _visible(request, news_id)
    return 201, _photo_out(svc.add_photo(request.user, item, image))


@router.post("/{news_id}/photos/{photo_id}/set-main", response=NewsDetail, summary="Make a picture the main one", operation_id="set_main_news_photo")
def set_main_photo(request, news_id: uuid.UUID, photo_id: uuid.UUID):
    item = _visible(request, news_id)
    svc.set_main_photo(request.user, item, _photo(item, photo_id))
    return _detail(request.user, item)


@router.patch("/{news_id}/photos/{photo_id}", response=NewsPhotoOut, summary="Set the focal point of a picture", operation_id="update_news_photo")
def update_photo(request, news_id: uuid.UUID, photo_id: uuid.UUID, payload: NewsFocalIn):
    item = _visible(request, news_id)
    return _photo_out(svc.set_focal_point(request.user, item, _photo(item, photo_id), **payload.dict()))


@router.delete("/{news_id}/photos/{photo_id}", response={204: None}, summary="Delete a picture", operation_id="delete_news_photo")
def delete_photo(request, news_id: uuid.UUID, photo_id: uuid.UUID):
    item = _visible(request, news_id)
    svc.delete_photo(request.user, item, _photo(item, photo_id))
    return 204, None
