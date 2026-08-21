import secrets

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from members.models import Member
from rosterchief.base import ClubScopedModel, UUIDModel, validate_club_scope


def _generate_feed_token() -> str:
    return secrets.token_urlsafe(32)


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


class CalendarFeedToken(UUIDModel):
    """The secret key one account's combined iCal subscription feed
    (mobile.views.CalendarFeedView) is served under -- one per account, every
    managed person's events combined into it (see that view's own docstring).

    Not club-scoped, unlike PushSubscription: ``User`` is the one global
    model in this platform (CLAUDE.md), and the same token works under
    whichever club subdomain the feed URL is fetched from -- the view
    resolves ``request.club`` and this account's Member/managed people
    within it exactly like every other screen already does, so an account
    active in more than one club still needs only the one token.
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="calendar_feed_token", verbose_name=_("user"))
    token = models.CharField(_("token"), max_length=64, unique=True, editable=False, default=_generate_feed_token)

    class Meta:
        verbose_name = _("calendar feed token")
        verbose_name_plural = _("calendar feed tokens")

    def __str__(self):
        return f"Calendar feed — {self.user}"

    def regenerate(self) -> None:
        """Invalidates the old URL immediately -- e.g. a shared/leaked link
        someone wants to revoke (mobile.views.CalendarFeedSettingsView)."""
        self.token = _generate_feed_token()
        self.save(update_fields=["token", "modified"])
