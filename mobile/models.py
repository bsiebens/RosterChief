from django.db import models
from django.utils.translation import gettext_lazy as _

from members.models import Member
from rosterchief.base import ClubScopedModel, validate_club_scope


class PushSubscription(ClubScopedModel):
    """One browser/device's Web Push registration for one member -- created by
    the subscribe flow in mobile/static/mobile/app.js, sent to whenever
    mobile.signals pushes a new notifications.Notification for that member.

    `endpoint` (the URL the browser's own push service gave it) is globally
    unique by construction -- a browser only ever has one push registration
    per origin -- so it doubles as the natural "already subscribed on this
    device" key: re-subscribing replaces the row rather than creating a
    second one for the same browser (see mobile.views.PushSubscribeView).
    """

    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="push_subscriptions", verbose_name=_("member"))
    endpoint = models.URLField(_("endpoint"), max_length=500, unique=True)
    p256dh = models.CharField(_("p256dh key"), max_length=255)
    auth = models.CharField(_("auth key"), max_length=255)
    user_agent = models.CharField(_("user agent"), max_length=255, blank=True)

    class Meta:
        verbose_name = _("push subscription")
        verbose_name_plural = _("push subscriptions")
        ordering = ["-created"]

    def __str__(self):
        return f"{self.member} — {self.user_agent or self.endpoint[:40]}"

    def clean(self):
        validate_club_scope(self, self.club_id, member_fields=("member",))

    def as_subscription_info(self) -> dict:
        return {"endpoint": self.endpoint, "keys": {"p256dh": self.p256dh, "auth": self.auth}}
