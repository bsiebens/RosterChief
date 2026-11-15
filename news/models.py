from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from members.models import Member
from rosterchief.base import UUIDModel
from teams.models import Team


def news_photo_path(instance, filename):
    # Keyed on the item's id, not its slug: a slug is built from the title and can run long, which together with the
    # rest of the path and the file name blows past ImageField's max_length.
    return f"news/{instance.news_item_id}/{filename}"


def unique_news_slug(instance, value: str) -> str:
    """A slug from ``value`` that no other news item has: ``-2``, ``-3``, ... on collision, cut to the field length."""
    max_length = instance._meta.get_field("slug").max_length
    base = slugify(value)[:max_length] or "news"
    others = News.objects.exclude(pk=instance.pk)
    slug, suffix = base, 2
    while others.filter(slug=slug).exists():
        tail = f"-{suffix}"
        slug = f"{base[: max_length - len(tail)]}{tail}"
        suffix += 1
    return slug


class News(UUIDModel):
    class Visibility(models.TextChoices):
        INTERNAL = "internal", _("internal")
        EXTERNAL = "external", _("external")
        BOTH = "both", _("both")

    class Status(models.TextChoices):
        DRAFT = "draft", _("draft")
        PENDING_REVIEW = "pending_review", _("pending review")
        PUBLISHED = "published", _("published")

    title = models.CharField(_("title"), max_length=255)
    slug = models.SlugField(_("slug"), max_length=255, unique=True, blank=True)
    body = models.TextField(_("body"), help_text=_("Supports Markdown."))
    title_en = models.CharField(_("title (English)"), max_length=255, blank=True, help_text=_("Optional. Falls back to the title when left blank."))
    body_en = models.TextField(_("body (English)"), blank=True, help_text=_("Optional. Falls back to the body when left blank."))

    teams = models.ManyToManyField(Team, related_name="news_items", blank=True, verbose_name=_("teams"), help_text=_("Leave empty for club-wide news."))

    visibility = models.CharField(_("visibility"), max_length=10, choices=Visibility.choices, default=Visibility.INTERNAL)
    status = models.CharField(_("status"), max_length=20, choices=Status.choices, default=Status.DRAFT)
    #: Why a reviewer sent the item back to its author; cleared when it is submitted again.
    review_note = models.TextField(_("review note"), blank=True)
    sent_back_at = models.DateTimeField(_("sent back at"), null=True, blank=True)
    published_at = models.DateTimeField(_("publish date"), null=True, blank=True, help_text=_("When this goes live. In the future to schedule it ahead of time."))
    notified_at = models.DateTimeField(_("notified at"), null=True, blank=True, help_text=_("When the audience notification was sent, or set at publish time when the publisher opted out of notifying."))

    created_by = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True, blank=True, related_name="news_items", verbose_name=_("created by"))

    class Meta:
        verbose_name = _("news item")
        verbose_name_plural = _("news items")
        ordering = ["-created"]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_news_slug(self, self.title)
        super().save(*args, **kwargs)

    @property
    def effective_title_en(self) -> str:
        return self.title_en or self.title

    @property
    def effective_body_en(self) -> str:
        return self.body_en or self.body

    @property
    def is_scheduled(self) -> bool:
        """Published, but the publish date has not arrived yet: not live."""
        return self.status == self.Status.PUBLISHED and self.published_at is not None and self.published_at > timezone.now()

    @property
    def main_photo(self):
        """The cover photo. Filters in Python so a ``prefetch_related("photos")`` is honoured."""
        return next((photo for photo in self.photos.all() if photo.is_main), None)


class NewsPhoto(UUIDModel):
    news_item = models.ForeignKey(News, on_delete=models.CASCADE, related_name="photos", verbose_name=_("news item"))
    image = models.ImageField(_("image"), upload_to=news_photo_path, max_length=255)
    is_main = models.BooleanField(_("main picture"), default=False)
    ordering = models.PositiveSmallIntegerField(_("ordering"), default=0)

    #: Where a cropped display centres, in percent from the left/top of the original image.
    focal_x = models.PositiveSmallIntegerField(_("focal point (x)"), default=50, validators=[MinValueValidator(0), MaxValueValidator(100)])
    focal_y = models.PositiveSmallIntegerField(_("focal point (y)"), default=50, validators=[MinValueValidator(0), MaxValueValidator(100)])

    class Meta:
        verbose_name = _("news photo")
        verbose_name_plural = _("news photos")
        ordering = ["ordering", "created"]
        constraints = [
            models.UniqueConstraint(fields=["news_item"], condition=Q(is_main=True), name="unique_main_photo_per_news_item"),
        ]

    def __str__(self):
        return f"{self.news_item} - photo"

    @property
    def object_position(self) -> str:
        return f"{self.focal_x}% {self.focal_y}%"
