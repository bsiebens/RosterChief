from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from rosterchief.base import UUIDModel


class PushDevice(UUIDModel):
    """One native-app install that can receive push notifications (API.md §9).

    Keyed per *install*, not per Member or club: one install serves every club the
    account belongs to, and the APNs token is per install. ``installation_id`` is a
    random id the app generates once and keeps; registering it again (a new push token,
    a new app version, or another account signing in on the same phone) updates this row
    rather than adding a second one, so a device never gets the previous user's pushes.
    """

    class Platform(models.TextChoices):
        IOS = "ios", _("iOS")
        ANDROID = "android", _("Android")

    class Environment(models.TextChoices):
        #: App Store / TestFlight builds.
        PRODUCTION = "production", _("production")
        #: Debug builds run from Xcode, which get APNs sandbox tokens.
        SANDBOX = "sandbox", _("sandbox")

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="push_devices", verbose_name=_("user"))
    installation_id = models.CharField(_("installation id"), max_length=64, unique=True)
    platform = models.CharField(_("platform"), max_length=16, choices=Platform.choices)
    environment = models.CharField(_("push environment"), max_length=16, choices=Environment.choices, default=Environment.PRODUCTION)
    push_token = models.CharField(_("push token"), max_length=512)
    app_version = models.CharField(_("app version"), max_length=32, blank=True)
    locale = models.CharField(_("locale"), max_length=16, blank=True)
    enabled = models.BooleanField(_("enabled"), default=True, help_text=_("Turned off when the push service reports the token as no longer valid."))
    last_seen = models.DateTimeField(_("last seen"), auto_now=True)

    class Meta:
        verbose_name = _("push device")
        verbose_name_plural = _("push devices")
        ordering = ["-last_seen"]

    def __str__(self):
        return f"{self.get_platform_display()} {self.installation_id[:8]} ({self.user})"


class IdempotencyRecord(UUIDModel):
    """A stored response for a retried write (API.md §6, "Idempotency").

    A client sends ``Idempotency-Key`` on writes a flaky network may repeat; the first
    response is kept for ``appapi.idempotency.TTL`` and replayed for the same key, so a
    double-tap never creates two orders. Keys are per user.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+", verbose_name=_("user"))
    key = models.CharField(_("key"), max_length=128)
    method = models.CharField(_("method"), max_length=8)
    path = models.CharField(_("path"), max_length=255)
    status_code = models.PositiveSmallIntegerField(_("status code"))
    body = models.JSONField(_("body"), null=True, blank=True)

    class Meta:
        verbose_name = _("idempotency record")
        verbose_name_plural = _("idempotency records")
        constraints = [models.UniqueConstraint(fields=["user", "key"], name="appapi_unique_idempotency_key_per_user")]
