"""Public read-only news endpoint -- see api/urls.py for how this is mounted.

Pagination is hand-rolled (limit/offset params) rather than Ninja's built-in
@paginate: a photo's URL has to be made absolute against `request`
(api/urls.py's docstring explains why), and Ninja's response-schema resolvers
don't have request access, so building the page manually here is simpler than
fighting that.
"""

import uuid
from datetime import datetime

from django.utils import timezone
from django.utils.text import Truncator
from ninja import Router, Schema
from ninja.errors import HttpError

from api.errors import require_club

from .models import News

router = Router(tags=["news"])

DEFAULT_LIMIT = 20
MAX_LIMIT = 100

#: Words, not characters -- reads more naturally cut off mid-list than a hard
#: character count, and body is plain text so there's no markup to worry about
#: truncating mid-tag.
EXCERPT_WORDS = 40


class NewsPhotoOut(Schema):
    url: str
    is_main: bool
    ordering: int


class NewsItemOut(Schema):
    id: uuid.UUID
    title: str
    slug: str
    excerpt: str
    body: str
    published_at: datetime
    teams: list[str]
    photos: list[NewsPhotoOut]


class NewsListOut(Schema):
    count: int
    limit: int
    offset: int
    results: list[NewsItemOut]


def _visible_news(club):
    return News.objects.filter(
        club=club,
        status=News.Status.PUBLISHED,
        published_at__lte=timezone.now(),
        visibility__in=[News.Visibility.EXTERNAL, News.Visibility.BOTH],
    )


def _to_news_item_out(item, request) -> NewsItemOut:
    return NewsItemOut(
        id=item.pk,
        title=item.title,
        slug=item.slug,
        excerpt=Truncator(item.body).words(EXCERPT_WORDS, truncate=" …"),
        body=item.body,
        published_at=item.published_at,
        teams=[team.name for team in item.teams.all()],
        photos=[NewsPhotoOut(url=request.build_absolute_uri(photo.image.url), is_main=photo.is_main, ordering=photo.ordering) for photo in item.photos.all()],
    )


@router.get("/", response=NewsListOut, summary="Published news")
def list_news(request, limit: int = DEFAULT_LIMIT, offset: int = 0):
    """Published news items whose release date has passed and that are marked
    visible outside the club (`external` or `both`) -- newest first."""
    club = require_club(request)
    limit = max(1, min(limit, MAX_LIMIT))
    offset = max(0, offset)

    queryset = _visible_news(club).prefetch_related("photos", "teams").order_by("-published_at")

    count = queryset.count()
    page = queryset[offset : offset + limit]

    return NewsListOut(count=count, limit=limit, offset=offset, results=[_to_news_item_out(item, request) for item in page])


@router.get("/{slug}/", response=NewsItemOut, summary="Single published news item")
def get_news_item(request, slug: str):
    club = require_club(request)
    item = _visible_news(club).filter(slug=slug).prefetch_related("photos", "teams").first()
    if item is None:
        raise HttpError(404, "No such news item.")

    return _to_news_item_out(item, request)
