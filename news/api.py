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
from ninja import Router, Schema
from ninja.errors import HttpError

from api.errors import require_club

from .models import News
from .services import render_body_excerpt, render_body_html

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
    #: Where the subject is, as a percent from the left/top of the *original*
    #: image (NewsPhoto.focal_x/focal_y) -- 50/50 (dead centre) unless staff
    #: set one via the "Focal point" picker. object_position is the same
    #: point already formatted as a CSS object-position/background-position
    #: value ("{x}% {y}%"), for a consumer that just wants to drop it
    #: straight into a style attribute without doing that itself.
    focal_x: int
    focal_y: int
    object_position: str


class NewsItemOut(Schema):
    """Both languages come back in one object -- title_nl/body_nl/excerpt_nl are
    the club's own-language text as authored; title_en/body_en/excerpt_en are the
    English text, falling back to the nl content when no translation was added
    (News.effective_title_en/effective_body_en) -- so these three are never blank,
    even for a news item nobody has translated yet. The consumer picks whichever
    it needs."""

    id: uuid.UUID
    title_nl: str
    title_en: str
    slug: str
    excerpt_nl: str
    excerpt_en: str
    body_nl: str
    body_en: str
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
        title_nl=item.title,
        title_en=item.effective_title_en,
        slug=item.slug,
        excerpt_nl=render_body_excerpt(item.body, words=EXCERPT_WORDS),
        excerpt_en=render_body_excerpt(item.effective_body_en, words=EXCERPT_WORDS),
        body_nl=render_body_html(item.body),
        body_en=render_body_html(item.effective_body_en),
        published_at=item.published_at,
        teams=[team.name for team in item.teams.all()],
        photos=[
            NewsPhotoOut(url=request.build_absolute_uri(photo.image.url), is_main=photo.is_main, ordering=photo.ordering, focal_x=photo.focal_x, focal_y=photo.focal_y, object_position=photo.object_position)
            for photo in item.photos.all()
        ],
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
