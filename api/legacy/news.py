from ninja import Router, Schema
from ninja.errors import HttpError

from api.routers.public_news import public_news_item
from api.schemas_news import PublicNewsItemOut
from news.services import public_news

from .scope import require_club

router = Router(tags=["news"])


class NewsItemOut(PublicNewsItemOut):
    title_nl: str
    title_en: str
    excerpt_nl: str
    excerpt_en: str
    body_nl: str
    body_en: str


class NewsListOut(Schema):
    count: int
    limit: int
    offset: int
    results: list[NewsItemOut]


def _legacy_item(request, item):
    data = public_news_item(request, item)
    for language, translation in data["translations"].items():
        for field, text in translation.items():
            data[f"{field}_{language}"] = text
    return data


@router.get("/", response=NewsListOut, summary="Published news", operation_id="news_api_list_news")
def list_news(request, limit: int = 20, offset: int = 0):
    require_club(request)
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    queryset = public_news().prefetch_related("photos", "teams")
    return {"count": queryset.count(), "limit": limit, "offset": offset, "results": [_legacy_item(request, item) for item in queryset[offset : offset + limit]]}


@router.get("/{slug}/", response=NewsItemOut, summary="Single published news item", operation_id="news_api_get_news_item")
def get_news_item(request, slug: str):
    require_club(request)
    item = public_news().filter(slug=slug).prefetch_related("photos", "teams").first()
    if item is None:
        raise HttpError(404, "No such news item.")
    return _legacy_item(request, item)
