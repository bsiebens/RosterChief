from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _

from members.models import Member
from rosterchief.base import ClubScopedModel, validate_club_scope


class NotificationCategory(models.TextChoices):
    """What a notification is *about*: the unit a person switches on or off
    (``NotificationPreference``). The value is stored and sent to the apps, the label is for people."""

    NEWS = "news", _("News")
    EVENT = "event", _("New and changed events")
    REMINDER = "reminder", _("Reminders to respond")
    LINEUP = "lineup", _("Line-ups")
    CREW = "crew", _("Referee and official duties")
    FORM = "form", _("Forms")
    SHOP = "shop", _("Shop orders")
    COACH = "coach", _("Messages from coaches")
    ADMIN = "admin", _("Club administration")
    ANNOUNCEMENT = "announcement", _("Platform announcements")
    GAME = "game", _("Live game updates")
    GENERAL = "general", _("Other")


class Notification(ClubScopedModel):
    """One member's copy of a notification -- "this member should know about
    X" -- built for news publishing first (see news.tasks.notify_news_published),
    but `source` is generic on purpose: the whole point is reusing this for
    other kinds of activity later without a new model each time.

    Always keyed to the Member it's *about*, even though actual delivery may
    go to a parent/guardian's email instead of (or as well as) the member's
    own -- see notifications.services.notify_members. That's what a future
    in-app feed for this member would list, regardless of who was actually
    emailed; `sent_to_emails` is the audit trail of who that was.
    """

    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="notifications", verbose_name=_("member"))
    category = models.CharField(_("category"), max_length=16, choices=NotificationCategory.choices, default=NotificationCategory.GENERAL)
    title = models.CharField(_("title"), max_length=255)
    body = models.TextField(_("body"))
    #: ``{language: {"title": ..., "body": ...}}`` for every language in settings.LANGUAGES,
    #: filled by notify_members when the text is translatable. ``title``/``body`` above stay the
    #: English rendering (what emails use). Empty on rows from before this existed.
    translations = models.JSONField(_("translations"), default=dict, blank=True, editable=False)

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, null=True, blank=True)
    object_id = models.CharField(max_length=255, null=True, blank=True)  # noqa: DJ001 -- GenericForeignKey needs a real NULL, not "", for "no source"
    source = GenericForeignKey("content_type", "object_id")

    target_type = models.CharField(
        max_length=32,
        blank=True,
        help_text=_('For notifications with no source object that still point at a place in the app (e.g. "calendar"): the native app opens that screen. Ignored when there is a source.'),
    )

    sent_at = models.DateTimeField(_("sent at"), null=True, blank=True)
    sent_to_emails = models.JSONField(_("sent to"), default=list, blank=True, help_text=_("Every email address this actually went to -- the member's own, and/or a parent/guardian's. Empty means nobody reachable was found."))
    read_at = models.DateTimeField(_("read at"), null=True, blank=True, help_text=_("Set once there's somewhere for a member to read this -- not used yet."))

    class Meta:
        verbose_name = _("notification")
        verbose_name_plural = _("notifications")
        ordering = ["-created"]
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
            # The unread-count badge query runs on every management and mobile
            # request via a global context processor/mixin -- see
            # members.services.lookup/club.services.access's request-scoped
            # caching of the surrounding lookups.
            models.Index(fields=["club", "member", "read_at"]),
        ]

    def __str__(self):
        return f"{self.member} — {self.title}"

    def clean(self):
        validate_club_scope(self, self.club_id, member_fields=("member",))

    @property
    def is_sent(self) -> bool:
        return self.sent_at is not None

    def text_for(self, language: str) -> dict:
        """``{"title", "body"}`` in ``language``; the stored English text when that language
        has no translation (older rows, or untranslated user-written content)."""
        return self.translations.get(language) or {"title": self.title, "body": self.body}


class NotificationDelivery(ClubScopedModel):
    """One attempt trail for one send on one channel to one target -- "the native push of this
    notification to that phone", "this email to that address". Written by the channels
    (notifications.delivery_log), so there is a record of what went out, what failed and why,
    instead of nothing. A transient failure (throttling, a server error, a network blip) stays
    ``RETRYING`` with the ``payload`` kept, and the ``retry_failed_deliveries`` job tries again
    with a growing delay; once it succeeds, is refused for good or runs out of attempts the
    payload is dropped. Rows are purged after a retention period by the same job."""

    class Channel(models.TextChoices):
        NATIVE = "native", _("Native push")
        WEB = "web", _("Web push")
        EMAIL = "email", _("Email")

    class Status(models.TextChoices):
        SENT = "sent", _("Sent")
        RETRYING = "retrying", _("Waiting to retry")
        FAILED = "failed", _("Failed")
        DEAD = "dead", _("Target gone")

    notification = models.ForeignKey(Notification, on_delete=models.SET_NULL, null=True, blank=True, related_name="deliveries", verbose_name=_("notification"))
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name=_("account"))
    channel = models.CharField(_("channel"), max_length=16, choices=Channel.choices)
    #: What was sent to: a native install id, a web push subscription id, an email address.
    target = models.CharField(_("target"), max_length=255)
    status = models.CharField(_("status"), max_length=16, choices=Status.choices)
    #: Who actually carried it: apns, fcm, webpush, email (monitoring groups by this).
    provider = models.CharField(_("provider"), max_length=16, blank=True)
    #: How long the provider took to answer, for monitoring.
    duration_ms = models.PositiveIntegerField(_("duration (ms)"), null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(_("attempts"), default=1)
    last_error = models.CharField(_("last error"), max_length=500, blank=True)
    next_attempt_at = models.DateTimeField(_("next attempt"), null=True, blank=True)
    #: What a retry needs to send again (channel-specific JSON); kept only while RETRYING.
    payload = models.JSONField(_("payload"), default=dict, blank=True)

    class Meta:
        verbose_name = _("notification delivery")
        verbose_name_plural = _("notification deliveries")
        ordering = ["-created"]
        indexes = [
            models.Index(fields=["status", "next_attempt_at"]),
            models.Index(fields=["notification"]),
            models.Index(fields=["club", "created"]),
            models.Index(fields=["created", "provider", "status"]),
        ]

    def __str__(self):
        return f"{self.get_channel_display()} → {self.target} ({self.get_status_display()})"


class NotificationPreference(models.Model):
    """One switch: "this account does (not) want ``category`` on ``channel``", for one club or --
    with ``club`` empty -- for every club. Absence of a row means *on*: nothing is stored until
    someone changes a default. A club-specific row beats the all-clubs row. The in-app inbox is
    never switchable: the notification is always written, a preference only decides whether it
    also buzzes a phone or lands in a mailbox."""

    class Channel(models.TextChoices):
        PUSH = "push", _("Push (app and browser)")
        EMAIL = "email", _("Email")

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_preferences", verbose_name=_("account"))
    club = models.ForeignKey("club.Club", on_delete=models.CASCADE, null=True, blank=True, related_name="notification_preferences", verbose_name=_("club"))
    category = models.CharField(_("category"), max_length=16, choices=NotificationCategory.choices)
    channel = models.CharField(_("channel"), max_length=8, choices=Channel.choices)
    enabled = models.BooleanField(_("enabled"), default=True)
    modified = models.DateTimeField(_("modified"), auto_now=True)

    class Meta:
        verbose_name = _("notification preference")
        verbose_name_plural = _("notification preferences")
        constraints = [
            models.UniqueConstraint(fields=["user", "club", "category", "channel"], name="unique_notification_preference"),
            # NULL clubs never collide in a plain unique constraint: one all-clubs row per switch.
            models.UniqueConstraint(fields=["user", "category", "channel"], condition=models.Q(club__isnull=True), name="unique_global_notification_preference"),
        ]

    def __str__(self):
        return f"{self.user} {self.category}/{self.channel}={'on' if self.enabled else 'off'}"
