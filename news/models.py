from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from members.models import Member
from rosterchief.base import ClubScopedModel, UUIDModel
from teams.models import Team


def news_photo_path(instance, filename):
    return f"clubs/{instance.news_item.club.slug}/news/{instance.news_item.slug}/{filename}"


class News(ClubScopedModel):
    class Visibility(models.TextChoices):
        INTERNAL = "internal", _("internal")
        EXTERNAL = "external", _("external")
        BOTH = "both", _("both")

    class Status(models.TextChoices):
        DRAFT = "draft", _("draft")
        PUBLISHED = "published", _("published")

    title = models.CharField(_("title"), max_length=255)
    slug = models.SlugField(_("slug"), max_length=255, blank=True)
    slug_source = "title"

    body = models.TextField(
        _("body"),
        help_text=_(
            "Supports Markdown: **bold**, *italic*, [link text](https://example.com), "
            "# heading, - list item, > quote. Rendered to HTML for the public website; "
            "shown as plain text here in the control panel."
        ),
    )
    teams = models.ManyToManyField(Team, related_name="news_items", blank=True, verbose_name=_("teams"), help_text=_("Leave empty for club-wide news."))

    visibility = models.CharField(_("visibility"), max_length=10, choices=Visibility.choices, default=Visibility.INTERNAL)
    status = models.CharField(_("status"), max_length=10, choices=Status.choices, default=Status.DRAFT)
    published_at = models.DateTimeField(_("publish date"), null=True, blank=True, help_text=_("When this goes live. In the future to schedule it ahead of time."))

    created_by = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True, blank=True, related_name="news_items", verbose_name=_("created by"))

    class Meta:
        verbose_name = _("news item")
        verbose_name_plural = _("news items")
        ordering = ["-created"]
        constraints = [
            models.UniqueConstraint(fields=["club", "slug"], name="unique_news_slug_per_club"),
        ]

    def __str__(self):
        return self.title

    def publish(self, at=None):
        self.status, self.published_at = self.Status.PUBLISHED, at or timezone.now()
        self.save(update_fields=["status", "published_at"])

    def unpublish(self):
        self.status, self.published_at = self.Status.DRAFT, None
        self.save(update_fields=["status", "published_at"])

    @property
    def is_scheduled(self):
        """PUBLISHED (past the editor's release gate) but its publish date hasn't
        arrived yet -- not actually live. A later consumer (member feed, public
        API) filters `status=PUBLISHED, published_at__lte=now()`; nothing here
        needs a cron job to "flip" it at the scheduled moment."""
        return self.status == self.Status.PUBLISHED and self.published_at is not None and self.published_at > timezone.now()


class NewsPhoto(UUIDModel):
    news_item = models.ForeignKey(News, on_delete=models.CASCADE, related_name="photos", verbose_name=_("news item"))
    image = models.ImageField(_("image"), upload_to=news_photo_path)
    is_main = models.BooleanField(_("main picture"), default=False)
    ordering = models.PositiveSmallIntegerField(_("ordering"), default=0)

    class Meta:
        verbose_name = _("news photo")
        verbose_name_plural = _("news photos")
        ordering = ["ordering", "created"]
        constraints = [
            models.UniqueConstraint(fields=["news_item"], condition=Q(is_main=True), name="unique_main_photo_per_news_item"),
        ]

    def __str__(self):
        return f"{self.news_item} — photo"
