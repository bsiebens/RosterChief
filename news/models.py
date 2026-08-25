from django.core.validators import MaxValueValidator, MinValueValidator
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
        PENDING_REVIEW = "pending_review", _("pending review")
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

    title_en = models.CharField(_("title (English)"), max_length=255, blank=True, help_text=_("Optional. Falls back to the title above when left blank."))
    body_en = models.TextField(_("body (English)"), blank=True, help_text=_("Optional. Supports the same Markdown as the body above. Falls back to the body above when left blank."))

    teams = models.ManyToManyField(Team, related_name="news_items", blank=True, verbose_name=_("teams"), help_text=_("Leave empty for club-wide news."))

    visibility = models.CharField(_("visibility"), max_length=10, choices=Visibility.choices, default=Visibility.INTERNAL)
    status = models.CharField(_("status"), max_length=20, choices=Status.choices, default=Status.DRAFT)
    published_at = models.DateTimeField(_("publish date"), null=True, blank=True, help_text=_("When this goes live. In the future to schedule it ahead of time."))
    notified_at = models.DateTimeField(
        _("notified at"),
        null=True,
        blank=True,
        help_text=_("When the audience notification was sent -- or, if the publisher opted out of notifying, set immediately at publish time so the sweep never sends one."),
    )

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

    def submit_for_review(self):
        """A non-editor author (see club.services.access.can_add_news vs
        can_publish_news) hands a draft off to an editor/admin instead of
        publishing it themselves -- see news.services.notify_editors_of_pending_review,
        which this doesn't call itself: the notification is the view's job,
        same as publish()/unpublish() never send anything either."""
        self.status = self.Status.PENDING_REVIEW
        self.save(update_fields=["status"])

    def publish(self, at=None):
        self.status, self.published_at = self.Status.PUBLISHED, at or timezone.now()
        self.save(update_fields=["status", "published_at"])

    def unpublish(self):
        self.status, self.published_at = self.Status.DRAFT, None
        self.save(update_fields=["status", "published_at"])

    @property
    def effective_title_en(self) -> str:
        """The English title to show -- `title_en` when the editor set one,
        else the Dutch `title`. Computed on read rather than copied into
        `title_en` at save time, so editing the Dutch title later keeps this
        current instead of leaving a stale English copy behind."""
        return self.title_en or self.title

    @property
    def effective_body_en(self) -> str:
        """See `effective_title_en` -- same fallback, for the body."""
        return self.body_en or self.body

    @property
    def is_scheduled(self):
        """PUBLISHED (past the editor's release gate) but its publish date hasn't
        arrived yet -- not actually live. A later consumer (member feed, public
        API) filters `status=PUBLISHED, published_at__lte=now()`; nothing here
        needs a cron job to "flip" it at the scheduled moment."""
        return self.status == self.Status.PUBLISHED and self.published_at is not None and self.published_at > timezone.now()

    @property
    def main_photo(self):
        """The cover photo for the article preview, if one's been marked as
        such. Filters ``self.photos.all()`` in Python rather than a fresh
        ``.filter(is_main=True)`` query, so a prefetch_related("photos") on
        the calling queryset is actually honoured instead of bypassed."""
        return next((photo for photo in self.photos.all() if photo.is_main), None)


class NewsPhoto(UUIDModel):
    news_item = models.ForeignKey(News, on_delete=models.CASCADE, related_name="photos", verbose_name=_("news item"))
    image = models.ImageField(_("image"), upload_to=news_photo_path)
    is_main = models.BooleanField(_("main picture"), default=False)
    ordering = models.PositiveSmallIntegerField(_("ordering"), default=0)

    #: Where the crop centres when this photo is shown cropped (the Home teaser,
    #: the article hero) -- percent from the left/top of the *original* image, not
    #: the cropped result. Defaults to dead centre, CSS's own default object-position,
    #: so an untouched photo behaves exactly as before this existed. object-fit:cover
    #: (or a CSS background-position equivalent) always shows the same aspect-ratio
    #: box regardless of the source photo's own shape; a subject that isn't centred
    #: -- a crest occupying most of a portrait photo, a face lower in the frame --
    #: needs this told explicitly rather than guessed by a single global default.
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
        return f"{self.news_item} — photo"

    @property
    def object_position(self) -> str:
        """``object-position``/``background-position`` value for this photo's
        stored focal point -- the one thing every render site (mobile's Home
        teaser and article hero, management's own preview) needs, so none of
        them re-derive the ``"{x}% {y}%"`` string themselves."""
        return f"{self.focal_x}% {self.focal_y}%"
