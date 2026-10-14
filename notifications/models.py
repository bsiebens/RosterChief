import datetime
import uuid

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone
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


class NotificationPriority(models.TextChoices):
    NORMAL = "normal", _("Normal")
    #: Something people must not miss (a cancelled or same-day changed event): it is delivered
    #: during a person's quiet hours too. Category switches still apply.
    CRITICAL = "critical", _("Critical")


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
    priority = models.CharField(_("priority"), max_length=8, choices=NotificationPriority.choices, default=NotificationPriority.NORMAL)
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
        HELD = "held", _("Held until quiet hours end")
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
    #: Set for a switch about one person only (a parent's "everything for Lou, only results for Sam"):
    #: it applies to notifications *about* that member and needs a club. Beats the club-wide row.
    member = models.ForeignKey("members.Member", on_delete=models.CASCADE, null=True, blank=True, related_name="+", verbose_name=_("member"))
    category = models.CharField(_("category"), max_length=16, choices=NotificationCategory.choices)
    channel = models.CharField(_("channel"), max_length=8, choices=Channel.choices)
    enabled = models.BooleanField(_("enabled"), default=True)
    modified = models.DateTimeField(_("modified"), auto_now=True)

    class Meta:
        verbose_name = _("notification preference")
        verbose_name_plural = _("notification preferences")
        constraints = [
            # Three scopes, each unique on its own (NULLs never collide in a plain unique constraint).
            models.UniqueConstraint(fields=["user", "category", "channel"], condition=models.Q(club__isnull=True, member__isnull=True), name="unique_global_notification_preference"),
            models.UniqueConstraint(fields=["user", "club", "category", "channel"], condition=models.Q(club__isnull=False, member__isnull=True), name="unique_club_notification_preference"),
            models.UniqueConstraint(fields=["user", "club", "member", "category", "channel"], condition=models.Q(member__isnull=False), name="unique_member_notification_preference"),
            models.CheckConstraint(condition=models.Q(member__isnull=True) | models.Q(club__isnull=False), name="member_preference_needs_club"),
        ]

    def __str__(self):
        return f"{self.user} {self.category}/{self.channel}={'on' if self.enabled else 'off'}"


class NotificationMasterSwitch(models.Model):
    """The "All push" / "All emails" master toggle for one scope (same three scopes as
    ``NotificationPreference``: one person in a club, one club, every club). ``enabled`` False means
    *nothing* on that channel in that scope, whatever the per-category switches say; True (or no row)
    leaves the per-category switches to decide. The most specific scope's row wins."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_master_switches", verbose_name=_("account"))
    club = models.ForeignKey("club.Club", on_delete=models.CASCADE, null=True, blank=True, related_name="notification_master_switches", verbose_name=_("club"))
    member = models.ForeignKey("members.Member", on_delete=models.CASCADE, null=True, blank=True, related_name="+", verbose_name=_("member"))
    channel = models.CharField(_("channel"), max_length=8, choices=NotificationPreference.Channel.choices)
    enabled = models.BooleanField(_("enabled"), default=True)
    modified = models.DateTimeField(_("modified"), auto_now=True)

    class Meta:
        verbose_name = _("notification master switch")
        verbose_name_plural = _("notification master switches")
        constraints = [
            models.UniqueConstraint(fields=["user", "channel"], condition=models.Q(club__isnull=True, member__isnull=True), name="unique_global_notification_master"),
            models.UniqueConstraint(fields=["user", "club", "channel"], condition=models.Q(club__isnull=False, member__isnull=True), name="unique_club_notification_master"),
            models.UniqueConstraint(fields=["user", "club", "member", "channel"], condition=models.Q(member__isnull=False), name="unique_member_notification_master"),
            models.CheckConstraint(condition=models.Q(member__isnull=True) | models.Q(club__isnull=False), name="member_master_needs_club"),
        ]

    def __str__(self):
        return f"{self.user} all {self.channel}={'on' if self.enabled else 'off'}"


class QuietHours(models.Model):
    """A daily window in which an account's *pushes* are held until it ends (``HELD`` deliveries),
    or dropped for categories that go stale (live games). ``Critical`` notifications and email are
    not affected. ``start`` > ``end`` crosses midnight (22:00 -> 07:00); ``start`` == ``end`` is no window."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="quiet_hours", verbose_name=_("account"))
    enabled = models.BooleanField(_("enabled"), default=True)
    start = models.TimeField(_("from"), default=datetime.time(22, 0))
    end = models.TimeField(_("until"), default=datetime.time(7, 0))
    #: IANA name; the account's own, since the server serves several time zones.
    timezone = models.CharField(_("time zone"), max_length=64, default="Europe/Brussels")
    modified = models.DateTimeField(_("modified"), auto_now=True)

    class Meta:
        verbose_name = _("quiet hours")
        verbose_name_plural = _("quiet hours")

    def __str__(self):
        return f"{self.user}: {self.start:%H:%M}-{self.end:%H:%M} ({'on' if self.enabled else 'off'})"


class OutboxEntry(models.Model):
    """Work still to dispatch, written in the same transaction as what it is about (a batch of
    notifications, a game's new moments), so a restart between commit and delivery cannot lose it.
    Worked off right after commit on a background thread, and by the ``process_outbox`` job for
    anything left behind. At-least-once: an entry is claimed atomically, but a crash mid-dispatch
    can repeat the sends that had not been marked yet."""

    class Kind(models.TextChoices):
        NOTIFICATIONS = "notifications", _("Notifications")
        GAME_MOMENTS = "game_moments", _("Game moments")

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        PROCESSING = "processing", _("Processing")
        DONE = "done", _("Done")
        FAILED = "failed", _("Failed")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(_("kind"), max_length=16, choices=Kind.choices)
    payload = models.JSONField(_("payload"), default=dict)
    status = models.CharField(_("status"), max_length=12, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(_("attempts"), default=0)
    available_at = models.DateTimeField(_("available at"), default=timezone.now)
    locked_at = models.DateTimeField(_("locked at"), null=True, blank=True)
    last_error = models.CharField(_("last error"), max_length=500, blank=True)
    created = models.DateTimeField(_("created"), auto_now_add=True)
    modified = models.DateTimeField(_("modified"), auto_now=True)

    class Meta:
        verbose_name = _("outbox entry")
        verbose_name_plural = _("outbox entries")
        ordering = ["created"]
        indexes = [models.Index(fields=["status", "available_at"])]

    def __str__(self):
        return f"{self.kind} ({self.status})"


class PushAlert(models.Model):
    """An open (or resolved) problem the platform admins were told about -- a provider down, a stuck
    retry queue, a configuration mismatch -- so one outage is one alert, a reminder every few hours
    while it lasts, and a recovery notice, not an email per check. ``key`` is e.g. ``provider:apns``."""

    key = models.CharField(_("key"), max_length=64, unique=True)
    severity = models.CharField(_("severity"), max_length=16)
    message = models.CharField(_("message"), max_length=500)
    first_seen = models.DateTimeField(_("first seen"), default=timezone.now)
    last_notified_at = models.DateTimeField(_("last notified"), null=True, blank=True)
    notifications_sent = models.PositiveSmallIntegerField(_("alerts sent"), default=0)
    resolved_at = models.DateTimeField(_("resolved"), null=True, blank=True)

    class Meta:
        verbose_name = _("push alert")
        verbose_name_plural = _("push alerts")
        ordering = ["-first_seen"]

    def __str__(self):
        return f"{self.key} ({'open' if self.is_open else 'resolved'})"

    @property
    def is_open(self) -> bool:
        return self.resolved_at is None


class ProviderConfigState(models.Model):
    """Whether a push provider's credentials were set the last time the health check looked (``apns``, ``fcm``,
    ``webpush``). A provider that was never configured is not a problem -- many installs run without one --
    but one that *was* and no longer is (a key dropped from the environment, a bad deploy) is: ``lost_at``
    marks that moment and keeps the alert open until the credentials are back."""

    provider = models.CharField(_("provider"), max_length=16, unique=True)
    configured = models.BooleanField(_("configured"), default=False)
    lost_at = models.DateTimeField(_("lost at"), null=True, blank=True)
    changed_at = models.DateTimeField(_("changed at"), default=timezone.now)

    class Meta:
        verbose_name = _("provider configuration state")
        verbose_name_plural = _("provider configuration states")

    def __str__(self):
        return f"{self.provider}: {'configured' if self.configured else 'not configured'}"


class SandboxMessage(models.Model):
    """A push or email the platform *would* have sent, captured instead of handed to Apple, Google, a
    browser push service or the mail server (``settings.PUSH_SANDBOX``). Holds the exact request the real
    provider would have received, plus what the sandbox's failure scenario made of it, so the sending side
    -- payloads, headers, policy, retries, the delivery log, monitoring and alerts -- can be tested
    without devices. Nothing here says anything about the receiving end."""

    class Outcome(models.TextChoices):
        DELIVERED = "delivered", _("Delivered")
        THROTTLED = "throttled", _("Throttled (429)")
        DEAD = "dead", _("Target gone")
        ERROR = "error", _("Refused")
        TIMEOUT = "timeout", _("Timed out")

    created = models.DateTimeField(_("created"), auto_now_add=True, db_index=True)
    channel = models.CharField(_("channel"), max_length=16, choices=NotificationDelivery.Channel.choices)
    provider = models.CharField(_("provider"), max_length=16)
    target = models.CharField(_("target"), max_length=255)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name=_("account"))
    club = models.ForeignKey("club.Club", on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name=_("club"))
    title = models.CharField(_("title"), max_length=255, blank=True)
    body = models.TextField(_("body"), blank=True)
    #: The request body exactly as the provider would have got it (APNs JSON, the FCM message, the web push payload, the mail's parts).
    request = models.JSONField(_("request"), default=dict, blank=True)
    #: The HTTP headers it would have carried (apns-expiration, apns-priority, collapse id, ...), without secrets.
    headers = models.JSONField(_("headers"), default=dict, blank=True)
    outcome = models.CharField(_("outcome"), max_length=12, choices=Outcome.choices, default=Outcome.DELIVERED)

    class Meta:
        verbose_name = _("sandbox message")
        verbose_name_plural = _("sandbox messages")
        ordering = ["-created"]

    def __str__(self):
        return f"{self.provider} → {self.target} ({self.outcome})"


class SandboxState(models.Model):
    """The sandbox switch and scenario, one row (``pk=1``), editable from the control panel so the
    platform can be flipped between *live* and *sandbox* without a deploy -- in production too. ``enabled``
    ``None`` follows ``settings.PUSH_SANDBOX`` (the default, off). ``expires_at`` switches it back to live
    on its own, so a forgotten sandbox cannot silently swallow every push."""

    enabled = models.BooleanField(_("sandbox on"), null=True, blank=True, default=None, help_text=_("Empty follows the PUSH_SANDBOX setting."))
    scenario = models.CharField(_("scenario"), max_length=12, default="ok")
    latency_ms = models.PositiveIntegerField(_("latency (ms)"), default=0)
    expires_at = models.DateTimeField(_("switches back to live at"), null=True, blank=True)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name=_("changed by"))
    changed_at = models.DateTimeField(_("changed at"), auto_now=True)

    class Meta:
        verbose_name = _("sandbox state")
        verbose_name_plural = _("sandbox state")

    def __str__(self):
        return f"sandbox {self.enabled} ({self.scenario})"


class SandboxEvent(models.Model):
    """Append-only audit trail of who switched the sandbox or its scenario, and when -- shown on the control panel."""

    at = models.DateTimeField(_("at"), auto_now_add=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name=_("account"))
    action = models.CharField(_("action"), max_length=32)
    detail = models.CharField(_("detail"), max_length=255, blank=True)

    class Meta:
        verbose_name = _("sandbox event")
        verbose_name_plural = _("sandbox events")
        ordering = ["-at"]

    def __str__(self):
        return f"{self.at:%Y-%m-%d %H:%M} {self.action}"
