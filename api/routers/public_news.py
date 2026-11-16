"""``GET /public/news`` and ``/public/news/{slug}``: the club's published news for its website, no login. Only items that
are published, whose publish date has passed and that are visible outside the club (external or both). Photo URLs are
absolute. The platform host has no news: 404."""

from django_tenants.utils import get_public_schema_name
from ninja import Query, Router
from ninja.errors import HttpError

from api.schemas_news import PublicNewsItemOut, PublicNewsListOut
from news import services as svc

router = Router()

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
EXCERPT_WORDS = 40


def _club_schema_only(request) -> None:
    if request.tenant.schema_name == get_public_schema_name():
        raise HttpError(404, "Not found.")


def _text(title: str, body: str) -> dict:
    return {"title": title, "excerpt": svc.render_body_excerpt(body, words=EXCERPT_WORDS), "body": svc.render_body_html(body)}


def public_news_item(request, item) -> dict:
    return {
        "id": item.pk, "slug": item.slug, "published_at": item.published_at,
        "translations": {"nl": _text(item.title, item.body), "en": _text(item.effective_title_en, item.effective_body_en)},
        "teams": [t.name for t in item.teams.all()],
        "photos": [
            {"url": request.build_absolute_uri(p.image.url), "is_main": p.is_main, "ordering": p.ordering, "focal_x": p.focal_x, "focal_y": p.focal_y, "object_position": p.object_position}
            for p in item.photos.all()
            if p.image
        ],
    }  # fmt: skip


@router.get("", auth=None, response=PublicNewsListOut, summary="The club's published news", operation_id="public_news_list")
def list_news(request, limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT), offset: int = Query(0, ge=0)):
    _club_schema_only(request)
    qs = svc.public_news().prefetch_related("photos", "teams")
    return {"count": qs.count(), "limit": limit, "offset": offset, "results": [public_news_item(request, i) for i in qs[offset : offset + limit]]}


@router.get("/{slug}", auth=None, response=PublicNewsItemOut, summary="One published news item", operation_id="public_news_item")
def get_news_item(request, slug: str):
    _club_schema_only(request)
    item = svc.public_news().filter(slug=slug).prefetch_related("photos", "teams").first()
    if item is None:
        raise HttpError(404, "No such news item.")
    return public_news_item(request, item)
