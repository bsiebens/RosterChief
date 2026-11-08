"""News: markdown rendering, the visibility rules and the draft -> review -> published workflow.

Rules: any member may write a draft and submit it for review; they see, edit and delete only their own items, and only
while draft or pending review. Holders of ``news.publish_newsitems`` see and edit everything, publish (optionally
scheduled), unpublish, approve and send back. Notifications live in ``news.services.notifications``."""

import uuid

import markdown as _markdown
import nh3
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.html import strip_tags
from django.utils.text import Truncator

from members.models import Member
from members.services import access
from members.services.errors import Invalid
from news.models import News, NewsPhoto

_EXTENSIONS = ["nl2br", "sane_lists", "fenced_code", "tables"]
_EXTENSION_CONFIGS = {"tables": {"use_align_attribute": True}}
_ALLOWED_TAGS = {"p", "br", "strong", "em", "b", "i", "u", "a", "ul", "ol", "li", "blockquote", "code", "pre", "h2", "h3", "h4", "img", "hr", "table", "thead", "tbody", "tr", "th", "td"}
_ALLOWED_ATTRIBUTES = {"a": {"href", "title"}, "img": {"src", "alt", "title"}, "th": {"align"}, "td": {"align"}}
_ALLOWED_URL_SCHEMES = {"http", "https", "mailto"}

MAX_PHOTO_BYTES = 10 * 1024 * 1024
PHOTO_FORMATS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
FIELDS = ("title", "body", "title_en", "body_en", "visibility")


def render_body_html(body: str) -> str:
    """Markdown source -> sanitized HTML."""
    html = _markdown.markdown(body or "", extensions=_EXTENSIONS, extension_configs=_EXTENSION_CONFIGS)
    return nh3.clean(html, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRIBUTES, url_schemes=_ALLOWED_URL_SCHEMES)


def render_body_excerpt(body: str, *, words: int) -> str:
    """Plain-text teaser from the rendered HTML (so no Markdown syntax shows up in it)."""
    return Truncator(strip_tags(render_body_html(body))).words(words, truncate=" …")


def live_news(now=None):
    """Every published item that is actually live (``published_at`` passed); a scheduled one stays invisible until then."""
    return News.objects.filter(status=News.Status.PUBLISHED, published_at__lte=now or timezone.now())


def member_news(now=None):
    """The members' feed: live items meant for members (internal or both, never external-only), newest first. Club-wide on
    purpose: news is what the club wants its members to know, not a per-person feed. Runs in the club schema."""
    return live_news(now).filter(visibility__in=[News.Visibility.INTERNAL, News.Visibility.BOTH]).order_by("-published_at")


def public_news(now=None):
    """The public website feed: live items visible outside the club (external or both), newest first."""
    return live_news(now).filter(visibility__in=[News.Visibility.EXTERNAL, News.Visibility.BOTH]).order_by("-published_at")


def localized_news(item: News, language: str | None) -> tuple[str, str, str]:
    """``(title, markdown body, language served)``: the English fields (falling back to the native text) for English,
    the native text otherwise."""
    if (language or "").split("-")[0] == "en":
        return item.effective_title_en, item.effective_body_en, "en"
    return item.title, item.body, "default"


def visible_news(user):
    """The news items ``user`` may see: everything for publishers; for everyone else the live published items (publish date
    passed) plus their own items in any state. Drafts, items in review and scheduled items are only for their author and publishers."""
    qs = News.objects.select_related("created_by").prefetch_related("photos", "teams")
    if access.can_publish_news(user):
        return qs
    if user is None or not user.is_authenticated:
        return qs.none()
    return qs.filter(Q(status=News.Status.PUBLISHED, published_at__lte=timezone.now()) | Q(created_by__user=user))


def get_news(user, news_id) -> News | None:
    return visible_news(user).filter(pk=news_id).first()


def _check(payload: dict) -> None:
    errors = {}
    if "title" in payload and not (payload["title"] or "").strip():
        errors["title"] = ["This field is required."]
    if "body" in payload and not (payload["body"] or "").strip():
        errors["body"] = ["This field is required."]
    if "visibility" in payload and payload["visibility"] not in News.Visibility.values:
        errors["visibility"] = ["Unknown visibility."]
    if errors:
        raise Invalid("Check the news item.", errors)


def _set_teams(item: News, team_ids) -> None:
    from teams.models import Team

    ids = set(team_ids)
    teams = list(Team.objects.filter(pk__in=ids))
    if len(teams) != len(ids):
        raise Invalid("Unknown team.", {"team_ids": ["Unknown team."]})
    item.teams.set(teams)


@transaction.atomic
def create_news(actor, *, team_ids=(), **fields) -> News:
    """A new draft, written by the actor's own member record."""
    access.require_add_news(actor)
    values = {name: fields[name] for name in FIELDS if name in fields and fields[name] is not None}
    values.setdefault("title", "")
    values.setdefault("body", "")
    _check(values)
    item = News.objects.create(created_by=Member.objects.filter(user=actor).first(), status=News.Status.DRAFT, **values)
    _set_teams(item, team_ids)
    return item


@transaction.atomic
def update_news(actor, item: News, *, team_ids=None, **fields) -> News:
    access.require_edit_news(actor, item)
    values = {name: fields[name] for name in FIELDS if name in fields and fields[name] is not None}
    _check(values)
    for name, value in values.items():
        setattr(item, name, value)
    if values:
        item.save(update_fields=[*values, "modified"])
    if team_ids is not None:
        _set_teams(item, team_ids)
    return item


@transaction.atomic
def delete_news(actor, item: News) -> None:
    access.require_edit_news(actor, item)
    storage_names = [(p.image.storage, p.image.name) for p in item.photos.all() if p.image]
    item.delete()
    for storage, name in storage_names:
        storage.delete(name)


@transaction.atomic
def submit_for_review(actor, item: News) -> News:
    """Draft -> pending review, by someone who may edit the item (a publisher can too)."""
    access.require_edit_news(actor, item)
    if item.status != News.Status.DRAFT:
        raise Invalid("Only a draft can be submitted for review.")
    item.status = News.Status.PENDING_REVIEW
    item.save(update_fields=["status", "modified"])
    from news.services import notifications

    transaction.on_commit(lambda: notifications.dispatch_notify_editors_of_pending_review(item.pk))
    return item


@transaction.atomic
def publish(actor, item: News, *, at=None, notify: bool = True) -> News:
    """Publish now, or at a future ``at`` (a scheduled item). With ``notify=False`` ``notified_at`` is set at once so the
    notification sweep skips it; an immediate publish with notify sends right away, a scheduled one is left to the sweep."""
    access.require_publish_news(actor)
    if item.status == News.Status.PUBLISHED and not item.is_scheduled:
        raise Invalid("This news item is already published.")
    now = timezone.now()
    item.status, item.published_at = News.Status.PUBLISHED, at or now
    item.notified_at = None if notify else now
    item.save(update_fields=["status", "published_at", "notified_at", "modified"])
    if notify and item.published_at <= now:
        from news.services import notifications

        transaction.on_commit(lambda: notifications.dispatch_publish_notification(item.pk))
    return item


@transaction.atomic
def unpublish(actor, item: News) -> News:
    access.require_publish_news(actor)
    if item.status != News.Status.PUBLISHED:
        raise Invalid("Only a published news item can be unpublished.")
    item.status, item.published_at = News.Status.DRAFT, None
    item.save(update_fields=["status", "published_at", "modified"])
    return item


@transaction.atomic
def send_back(actor, item: News) -> News:
    access.require_publish_news(actor)
    if item.status != News.Status.PENDING_REVIEW:
        raise Invalid("Only an item pending review can be sent back.")
    item.status = News.Status.DRAFT
    item.save(update_fields=["status", "modified"])
    return item


def _verify_image(image) -> str:
    from PIL import Image, UnidentifiedImageError

    if image.size > MAX_PHOTO_BYTES:
        raise Invalid("The picture is larger than 10 MB.", {"image": ["The picture is larger than 10 MB."]})
    try:
        with Image.open(image) as img:
            fmt = img.format
            img.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as e:
        raise Invalid("This is not a valid image.", {"image": ["This is not a valid image."]}) from e
    if fmt not in PHOTO_FORMATS:
        raise Invalid("Use a JPEG, PNG or WebP picture.", {"image": ["Use a JPEG, PNG or WebP picture."]})
    image.seek(0)
    return PHOTO_FORMATS[fmt]


@transaction.atomic
def add_photo(actor, item: News, image) -> NewsPhoto:
    """Add a picture (JPEG/PNG/WebP up to 10 MB, verified by decoding). The first one becomes the main picture."""
    access.require_edit_news(actor, item)
    ext = _verify_image(image)
    photos = list(item.photos.all())
    photo = NewsPhoto(news_item=item, is_main=not photos, ordering=max((p.ordering for p in photos), default=-1) + 1)
    photo.image.save(f"photo-{uuid.uuid4().hex[:12]}.{ext}", image, save=False)
    photo.save()
    return photo


def get_photo(item: News, photo_id) -> NewsPhoto | None:
    return item.photos.filter(pk=photo_id).first()


@transaction.atomic
def set_main_photo(actor, item: News, photo: NewsPhoto) -> News:
    access.require_edit_news(actor, item)
    item.photos.filter(is_main=True).exclude(pk=photo.pk).update(is_main=False)
    NewsPhoto.objects.filter(pk=photo.pk).update(is_main=True)
    return item


@transaction.atomic
def set_focal_point(actor, item: News, photo: NewsPhoto, *, focal_x: int, focal_y: int) -> NewsPhoto:
    access.require_edit_news(actor, item)
    if not (0 <= focal_x <= 100 and 0 <= focal_y <= 100):
        raise Invalid("The focal point must be between 0 and 100.", {"focal_x": ["Must be between 0 and 100."], "focal_y": ["Must be between 0 and 100."]})
    photo.focal_x, photo.focal_y = focal_x, focal_y
    photo.save(update_fields=["focal_x", "focal_y", "modified"])
    return photo


@transaction.atomic
def delete_photo(actor, item: News, photo: NewsPhoto) -> None:
    """Delete a picture; deleting the main one promotes the next (lowest ordering) so a cover remains."""
    access.require_edit_news(actor, item)
    was_main, storage, name = photo.is_main, photo.image.storage, photo.image.name
    photo.delete()
    if name:
        storage.delete(name)
    if was_main:
        nxt = item.photos.order_by("ordering", "created").first()
        if nxt is not None:
            NewsPhoto.objects.filter(pk=nxt.pk).update(is_main=True)
