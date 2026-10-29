"""Management news (``/manage/news``): the API behind the /manage news screens.

Same rules as management.views: anyone with management access may read; creating is
``can_add_news``, editing/deleting/submitting ``can_edit_news`` (broad while a draft, editors and
admins once published) and publishing/unpublishing ``can_publish_news``. Everything is scoped to
the club on the host; another club's item is a 404.

Photos (upload / cover / focal point / delete) are in api/panel/routers/news_photos.py.
"""

import uuid
from typing import Annotated

from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from ninja import Query, Router

from api.core.errors import ApiError
from api.core.forms import form_error
from api.core.idempotency import idempotent
from api.core.pagination import paginate
from api.core.schemas import ErrorOut
from api.panel.guards import panel_club, require
from api.panel.schemas.news import NewsCountsOut, NewsIn, NewsListOut, NewsOut, NewsPublishIn
from api.protected.presenters import team_ref
from club.services.access import can_add_news, can_edit_news, can_publish_news
from management.forms import NewsForm, NewsPublishForm
from members.models import Member
from news.models import News
from news.services import publish_news as publish_news_item
from news.services import render_body_html, submit_news_for_review

router = Router(tags=["manage-news"])

ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
STATUS_FILTERS = ("all", "draft", "scheduled", "published")


def _queryset(club):
    return News.objects.filter(club=club).select_related("created_by").prefetch_related("teams", "photos")


def _filtered(queryset, status: str):
    """The web list's status chips; pending review rolls into ``draft``."""
    now = timezone.now()
    if status == "draft":
        return queryset.filter(status__in=[News.Status.DRAFT, News.Status.PENDING_REVIEW])
    if status == "scheduled":
        return queryset.filter(status=News.Status.PUBLISHED, published_at__gt=now)
    if status == "published":
        return queryset.filter(status=News.Status.PUBLISHED, published_at__lte=now)
    return queryset


def _get(club, news_id: uuid.UUID) -> News:
    item = _queryset(club).filter(pk=news_id).first()
    if item is None:
        raise ApiError(404, "not_found", _("Not found."))
    return item


def _summary(user, club, item: News) -> dict:
    return {
        "id": item.pk,
        "title": item.title,
        "status": item.status,
        "visibility": item.visibility,
        "published_at": item.published_at,
        "is_scheduled": item.is_scheduled,
        "created": item.created,
        "created_by": str(item.created_by) if item.created_by else None,
        "teams": [team_ref(team) for team in item.teams.all()],
        "can_edit": can_edit_news(user, item),
    }


def _out(request, club, item: News) -> dict:
    return {
        **_summary(request.user, club, item),
        "title_en": item.title_en,
        "body": item.body,
        "body_en": item.body_en,
        "body_html": render_body_html(item.body),
        "body_en_html": render_body_html(item.effective_body_en),
        "notified_at": item.notified_at,
        "photos": [{"id": photo.pk, "url": request.build_absolute_uri(photo.image.url), "is_main": photo.is_main, "focal_x": photo.focal_x, "focal_y": photo.focal_y} for photo in item.photos.all()],
        "can_publish": can_publish_news(request.user, club),
    }


def _form(club, payload: NewsIn, instance: News) -> NewsForm:
    data = {"title": payload.title, "body": payload.body, "title_en": payload.title_en, "body_en": payload.body_en, "visibility": payload.visibility, "teams": [str(pk) for pk in payload.teams]}
    form = NewsForm(data, club=club, instance=instance)
    if not form.is_valid():
        raise form_error(form)
    return form


def _editable(request, club, news_id: uuid.UUID) -> News:
    item = _get(club, news_id)
    if not can_edit_news(request.user, item):
        raise ApiError(403, "forbidden", _("You don't have permission to do this."))
    return item


@router.get("/manage/news", response={200: NewsListOut, **ERRORS}, summary="News items of the club")
def list_news(request, status: Annotated[str, Query()] = "all", cursor: Annotated[str | None, Query()] = None, limit: int | None = None):
    """Newest first. `status`: `all`, `draft` (includes pending review), `scheduled` or `published`."""
    club = panel_club(request)
    if status not in STATUS_FILTERS:
        raise ApiError(422, "validation_error", fields={"status": [_("Choose one of: all, draft, scheduled, published.")]})
    page = paginate(_filtered(_queryset(club), status), sort_field="-created", cursor=cursor, limit=limit)
    return {"items": [_summary(request.user, club, item) for item in page.items], "next_cursor": page.next_cursor}


@router.get("/manage/news/counts", response={200: NewsCountsOut, **ERRORS}, summary="Item counts per status chip")
def counts(request):
    club = panel_club(request)
    base = News.objects.filter(club=club)
    return {status: _filtered(base, status).count() for status in STATUS_FILTERS}


@router.post("/manage/news", response={201: NewsOut, **ERRORS, 422: ErrorOut}, summary="Create a draft")
def create_news(request, payload: NewsIn):
    """Needs `can_add_news` (admin, editor, coach manager). The item starts as a draft. Honours `Idempotency-Key`."""
    club = panel_club(request)
    require(request, club, can_add_news)

    def work():
        form = _form(club, payload, News(club=club, created_by=Member.objects.filter(user=request.user).first()))
        item = form.save()
        return 201, _out(request, club, _get(club, item.pk))

    return idempotent(request, work)


@router.get("/manage/news/{news_id}", response={200: NewsOut, **ERRORS}, summary="One news item")
def get_news(request, news_id: uuid.UUID):
    club = panel_club(request)
    return _out(request, club, _get(club, news_id))


@router.put("/manage/news/{news_id}", response={200: NewsOut, **ERRORS, 422: ErrorOut}, summary="Update a news item")
def update_news(request, news_id: uuid.UUID, payload: NewsIn):
    """Replaces title, body, teams and visibility; status and publish date change only through publish/unpublish."""
    club = panel_club(request)
    item = _editable(request, club, news_id)
    _form(club, payload, item).save()
    return _out(request, club, _get(club, news_id))


@router.delete("/manage/news/{news_id}", response={204: None, **ERRORS}, summary="Delete a news item")
def delete_news(request, news_id: uuid.UUID):
    club = panel_club(request)
    _editable(request, club, news_id).delete()
    return 204, None


@router.post("/manage/news/{news_id}/submit-for-review", response={200: NewsOut, **ERRORS}, summary="Hand a draft to the editors")
def submit_for_review(request, news_id: uuid.UUID):
    """For authors who can't publish: the item becomes `pending_review` and the editors are told."""
    club = panel_club(request)
    item = _editable(request, club, news_id)
    submit_news_for_review(item)
    return _out(request, club, _get(club, news_id))


@router.post("/manage/news/{news_id}/publish", response={200: NewsOut, **ERRORS, 422: ErrorOut}, summary="Publish or schedule a news item")
def publish_news(request, news_id: uuid.UUID, payload: NewsPublishIn):
    """Editors and admins only. No `published_at` publishes now; a future one schedules it.
    `notify_members` emails the audience when it goes live (immediately, or via the periodic sweep when scheduled)."""
    club = panel_club(request)
    require(request, club, can_publish_news)
    item = _get(club, news_id)
    form = NewsPublishForm({"published_at": (payload.published_at or timezone.now()).isoformat(), "notify_members": payload.notify_members})
    if not form.is_valid():
        raise form_error(form)
    publish_news_item(item, published_at=form.cleaned_data["published_at"], notify_members=form.cleaned_data["notify_members"])
    return _out(request, club, _get(club, news_id))


@router.post("/manage/news/{news_id}/unpublish", response={200: NewsOut, **ERRORS}, summary="Take a news item back to draft")
def unpublish_news(request, news_id: uuid.UUID):
    club = panel_club(request)
    require(request, club, can_publish_news)
    _get(club, news_id).unpublish()
    return _out(request, club, _get(club, news_id))
