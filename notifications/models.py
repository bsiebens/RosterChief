"""Push notifications. A shared app: every table lives in the public schema because devices, preferences and quiet hours belong to
the *account* (club.User), which signs in to any number of clubs. A club only appears as an optional scope on a preference or a delivery."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from rosterchief.base import UUIDModel

USER = settings.AUTH_USER_MODEL


class Category(models.TextChoices):
    NEWS = "news", _("News")
    EVENT = "event", _("Events")
    REMINDER = "reminder", _("Reminders")
    LINEUP = "lineup", _("Line-ups")
    CREW = "crew", _("Crew")
    FORM = "form", _("Forms")
    SHOP = "shop", _("Shop")
    COACH = "coach", _("Coach")
    ADMIN = "admin", _("Admin")
    ANNOUNCEMENT = "announcement", _("Announcements")
    GAME = "game", _("Game moments")
    GENERAL = "general", _("General")


class Channel(models.TextChoices):
    """What a user can switch on or off."""

    PUSH = "push", _("Push")
    EMAIL = "email", _("Email")


class Priority(models.TextChoices):
    NORMAL = "normal", _("Normal")
    CRITICAL = "critical", _("Critical")  # bypasses quiet hours; category and master switches still apply


# --- devices ----------------------------------------------------------------------------------------------------------


class PushDevice(UUIDModel):
    """One app install (iOS via APNs, Android via FCM). Belongs to the account, not to a club."""

    class Platform(models.TextChoices):
        IOS = "ios", _("iOS")
        ANDROID = "android", _("Android")

    class Environment(models.TextChoices):
        PRODUCTION = "production", _("Production")
        SANDBOX = "sandbox", _("Sandbox")  # the platform's own test environment (APNs sandbox host); not the control panel sandbox

    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="push_devices", verbose_name=_("user"))
    installation_id = models.CharField(_("installation id"), max_length=200, unique=True)
    platform = models.CharField(_("platform"), max_length=10, choices=Platform.choices)
    environment = models.CharField(_("environment"), max_length=10, choices=Environment.choices, default=Environment.PRODUCTION)
    push_token = models.TextField(_("push token"))
    push_to_start_token = models.TextField(_("push-to-start token"), blank=True)
    app_version = models.CharField(_("app version"), max_length=50, blank=True)
    locale = models.CharField(_("locale"), max_length=20, blank=True)
    enabled = models.BooleanField(_("enabled"), default=True, help_text=_("Switched off automatically when the provider reports the token as gone."))
    disabled_at = models.DateTimeField(_("disabled at"), null=True, blank=True)
    last_seen = models.DateTimeField(_("last seen"), auto_now=True)

    class Meta:
        verbose_name = _("push device")
        verbose_name_plural = _("push devices")
        ordering = ["-last_seen"]

    def __str__(self):
        return f"{self.get_platform_display()} {self.installation_id[:8]}"

    @property
    def provider(self) -> str:
        return "apns" if self.platform == self.Platform.IOS else "fcm"


class WebPushSubscription(UUIDModel):
    """A browser/PWA subscription (VAPID web push). Optionally tied to the club it was subscribed from."""

    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="web_push_subscriptions", verbose_name=_("user"))
    club = models.ForeignKey("club.Club", on_delete=models.CASCADE, null=True, blank=True, related_name="+", verbose_name=_("club"))
    endpoint = models.TextField(_("endpoint"), unique=True)
    p256dh = models.CharField(_("p256dh key"), max_length=255)
    auth = models.CharField(_("auth secret"), max_length=255)
    user_agent = models.CharField(_("user agent"), max_length=500, blank=True)
    last_seen = models.DateTimeField(_("last seen"), auto_now=True)

    class Meta:
        verbose_name = _("web push subscription")
        verbose_name_plural = _("web push subscriptions")
        ordering = ["-last_seen"]

    def __str__(self):
        return f"web push {self.endpoint[:30]}"


# --- user preferences -------------------------------------------------------------------------------------------------


class NotificationPreference(UUIDModel):
    """Category x channel switch. ``club`` empty = all clubs. Most specific wins (club row, then all-clubs row, then default on)."""

    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="notification_preferences", verbose_name=_("user"))
    club = models.ForeignKey("club.Club", on_delete=models.CASCADE, null=True, blank=True, related_name="+", verbose_name=_("club"))
    category = models.CharField(_("category"), max_length=20, choices=Category.choices)
    channel = models.CharField(_("channel"), max_length=10, choices=Channel.choices)
    enabled = models.BooleanField(_("enabled"), default=True)

    class Meta:
        verbose_name = _("notification preference")
        verbose_name_plural = _("notification preferences")
        constraints = [
            models.UniqueConstraint(fields=["user", "club", "category", "channel"], name="unique_preference_per_scope", nulls_distinct=False),
        ]

    def __str__(self):
        return f"{self.user_id} {self.category}/{self.channel}={'on' if self.enabled else 'off'}"


class MasterSwitch(UUIDModel):
    """'All push' / 'All email', on top of the categories. ``club`` empty = all clubs."""

    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="notification_master_switches", verbose_name=_("user"))
    club = models.ForeignKey("club.Club", on_delete=models.CASCADE, null=True, blank=True, related_name="+", verbose_name=_("club"))
    channel = models.CharField(_("channel"), max_length=10, choices=Channel.choices)
    enabled = models.BooleanField(_("enabled"), default=True)

    class Meta:
        verbose_name = _("master switch")
        verbose_name_plural = _("master switches")
        constraints = [
            models.UniqueConstraint(fields=["user", "club", "channel"], name="unique_master_switch_per_scope", nulls_distinct=False),
        ]


class UserNotificationSettings(UUIDModel):
    """Account-wide settings that are not a switch."""

    class EmailMode(models.TextChoices):
        ALWAYS = "always", _("Always send email")
        UNLESS_APP = "unless_app", _("Only email me when the app can't notify me")

    user = models.OneToOneField(USER, on_delete=models.CASCADE, related_name="notification_settings", verbose_name=_("user"))
    email_mode = models.CharField(_("email mode"), max_length=12, choices=EmailMode.choices, default=EmailMode.ALWAYS)

    class Meta:
        verbose_name = _("notification settings")
        verbose_name_plural = _("notification settings")


class QuietHours(UUIDModel):
    """One daily window per account. Pushes inside it are held until it ends (or dropped for categories that must not arrive late)."""

    user = models.OneToOneField(USER, on_delete=models.CASCADE, related_name="quiet_hours", verbose_name=_("user"))
    enabled = models.BooleanField(_("enabled"), default=True)
    start = models.TimeField(_("start"), help_text=_("Local time in the chosen time zone. The window may cross midnight."))
    end = models.TimeField(_("end"))
    timezone = models.CharField(_("time zone"), max_length=64, default="Europe/Brussels", help_text=_("IANA name, e.g. Europe/Brussels."))

    class Meta:
        verbose_name = _("quiet hours")
        verbose_name_plural = _("quiet hours")

    def clean(self):
        if self.start == self.end:
            raise ValidationError({"end": _("The window can't be empty.")})


# --- delivery ---------------------------------------------------------------------------------------------------------


class NotificationDelivery(UUIDModel):
    """One attempt-chain to one target: the log monitoring reads, and the retry queue."""

    class Status(models.TextChoices):
        SENT = "sent", _("Sent")
        RETRYING = "retrying", _("Retrying")
        HELD = "held", _("Held (quiet hours)")
        FAILED = "failed", _("Failed")
        DEAD = "dead", _("Target gone")

    class DeliveryChannel(models.TextChoices):
        NATIVE = "native", _("Native app")
        WEB = "web", _("Web push")
        EMAIL = "email", _("Email")

    class Provider(models.TextChoices):
        APNS = "apns", _("APNs (iOS)")
        FCM = "fcm", _("FCM (Android)")
        WEBPUSH = "webpush", _("Web push")
        EMAIL = "email", _("Email")

    user = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="notification_deliveries", verbose_name=_("user"))
    club = models.ForeignKey("club.Club", on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name=_("club"))
    notification_id = models.UUIDField(_("notification id"), null=True, blank=True, db_index=True)
    category = models.CharField(_("category"), max_length=20, choices=Category.choices, default=Category.GENERAL)
    title = models.CharField(_("title"), max_length=255, blank=True)
    channel = models.CharField(_("channel"), max_length=10, choices=DeliveryChannel.choices)
    provider = models.CharField(_("provider"), max_length=10, choices=Provider.choices)
    target = models.CharField(_("target"), max_length=500, help_text=_("Installation id, endpoint or email address."))
    status = models.CharField(_("status"), max_length=10, choices=Status.choices, db_index=True)
    duration_ms = models.PositiveIntegerField(_("duration (ms)"), null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(_("attempts"), default=1)
    last_error = models.CharField(_("last error"), max_length=500, blank=True)
    next_attempt_at = models.DateTimeField(_("next attempt"), null=True, blank=True, db_index=True)
    payload = models.JSONField(_("payload"), null=True, blank=True, help_text=_("Kept only while the delivery is retrying or held."))

    class Meta:
        verbose_name = _("notification delivery")
        verbose_name_plural = _("notification deliveries")
        ordering = ["-created"]
        indexes = [models.Index(fields=["provider", "status", "created"])]


class OutboxEntry(UUIDModel):
    """Work to do, written in the same transaction as whatever caused it and processed by a Celery task (at-least-once)."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        PROCESSING = "processing", _("Processing")
        DONE = "done", _("Done")
        FAILED = "failed", _("Failed")

    kind = models.CharField(_("kind"), max_length=30, default="notification")
    payload = models.JSONField(_("payload"), default=dict)
    status = models.CharField(_("status"), max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    attempts = models.PositiveSmallIntegerField(_("attempts"), default=0)
    available_at = models.DateTimeField(_("available at"), db_index=True)
    locked_at = models.DateTimeField(_("locked at"), null=True, blank=True)
    last_error = models.CharField(_("last error"), max_length=500, blank=True)

    class Meta:
        verbose_name = _("outbox entry")
        verbose_name_plural = _("outbox entries")
        ordering = ["available_at"]


# --- configuration, alerts, sandbox -----------------------------------------------------------------------------------


class PushSettings(models.Model):
    """The editable, operational push configuration. A single row (pk=1); credentials stay in the environment."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    apns_enabled = models.BooleanField(_("APNs (iOS) enabled"), default=True)
    fcm_enabled = models.BooleanField(_("FCM (Android) enabled"), default=True)
    webpush_enabled = models.BooleanField(_("Web push enabled"), default=True)
    retry_delays_minutes = models.JSONField(_("retry delays (minutes)"), default=list, help_text=_("Wait before retry 1, 2, ... A delivery is given up after the last one."))
    outbox_max_attempts = models.PositiveSmallIntegerField(_("outbox attempts"), default=5)
    sent_retention_days = models.PositiveSmallIntegerField(_("keep sent log rows (days)"), default=14)
    failed_retention_days = models.PositiveSmallIntegerField(_("keep failed log rows (days)"), default=60)
    stale_device_days = models.PositiveSmallIntegerField(_("stale device after (days)"), default=60)
    disabled_device_retention_days = models.PositiveSmallIntegerField(_("delete disabled devices after (days)"), default=30)
    unseen_device_retention_days = models.PositiveSmallIntegerField(_("delete unseen devices after (days)"), default=180)
    degraded_below_percent = models.PositiveSmallIntegerField(_("degraded below (% success)"), default=95)
    down_below_percent = models.PositiveSmallIntegerField(_("down below (% success)"), default=50)
    health_min_requests = models.PositiveSmallIntegerField(_("minimum requests to judge health"), default=5)
    alert_emails = models.TextField(_("alert recipients"), blank=True, help_text=_("Comma separated. Empty = the platform admins."))

    class Meta:
        verbose_name = _("push settings")
        verbose_name_plural = _("push settings")

    def __str__(self):
        return "Push settings"

    DEFAULT_RETRY_DELAYS = (1, 5, 30)

    def save(self, *args, **kwargs):
        self.pk = 1
        if not self.retry_delays_minutes:
            self.retry_delays_minutes = list(self.DEFAULT_RETRY_DELAYS)
        super().save(*args, **kwargs)

    @classmethod
    def get(cls) -> PushSettings:
        obj, _created = cls.objects.get_or_create(pk=1, defaults={"retry_delays_minutes": list(cls.DEFAULT_RETRY_DELAYS)})
        return obj


class PushAlert(UUIDModel):
    """An open or resolved problem the platform admins are told about (one per outage, keyed e.g. ``provider:apns``)."""

    key = models.CharField(_("key"), max_length=100, db_index=True)
    message = models.CharField(_("message"), max_length=500)
    first_seen = models.DateTimeField(_("first seen"), auto_now_add=True)
    resolved_at = models.DateTimeField(_("resolved at"), null=True, blank=True)
    notifications_sent = models.PositiveSmallIntegerField(_("emails sent"), default=0)
    last_notified_at = models.DateTimeField(_("last emailed"), null=True, blank=True)

    class Meta:
        verbose_name = _("push alert")
        verbose_name_plural = _("push alerts")
        ordering = ["first_seen"]


class SandboxState(models.Model):
    """The control panel's sending sandbox: while on, every provider is replaced by a fake one that captures instead of sending."""

    class Scenario(models.TextChoices):
        OK = "ok", _("Delivered")
        THROTTLE = "throttle", _("Throttled (429, retried)")
        DEAD = "dead", _("Target gone")
        ERROR = "error", _("Refused (no retry)")
        TIMEOUT = "timeout", _("Timeout (retried)")

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    enabled = models.BooleanField(_("enabled"), null=True, blank=True, help_text=_("Empty follows the PUSH_SANDBOX setting."))
    scenario = models.CharField(_("scenario"), max_length=10, choices=Scenario.choices, default=Scenario.OK)
    latency_ms = models.PositiveIntegerField(_("latency (ms)"), default=0)
    expires_at = models.DateTimeField(_("switches back to live at"), null=True, blank=True)

    class Meta:
        verbose_name = _("sandbox state")
        verbose_name_plural = _("sandbox state")

    def __str__(self):
        return "Sandbox state"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)


class SandboxMessage(UUIDModel):
    """A message the fake provider captured instead of sending. Kept for 7 days."""

    channel = models.CharField(_("channel"), max_length=10)
    provider = models.CharField(_("provider"), max_length=10)
    user = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name=_("user"))
    club = models.ForeignKey("club.Club", on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name=_("club"))
    target = models.CharField(_("target"), max_length=500)
    title = models.CharField(_("title"), max_length=255, blank=True)
    body = models.TextField(_("body"), blank=True)
    request = models.JSONField(_("request"), default=dict, help_text=_("What would have been sent (credentials never included)."))
    outcome = models.CharField(_("outcome"), max_length=10)

    class Meta:
        verbose_name = _("sandbox message")
        verbose_name_plural = _("sandbox messages")
        ordering = ["-created"]


class SandboxEvent(UUIDModel):
    """Audit trail of switching the sandbox on and off, and of changing its scenario."""

    user = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name=_("user"))
    action = models.CharField(_("action"), max_length=30)
    detail = models.CharField(_("detail"), max_length=300, blank=True)

    class Meta:
        verbose_name = _("sandbox event")
        verbose_name_plural = _("sandbox events")
        ordering = ["-created"]
