import hashlib
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


def hash_feed_token(raw_token: str) -> str:
    """The SHA-256 hex digest a feed token is stored and looked up by."""
    return hashlib.sha256(raw_token.encode()).hexdigest()


class CalendarFeedToken(UUIDModel):
    """The secret key one account's combined iCal subscription feed
    (mobile.views.CalendarFeedView) is served under -- one per account, every
    managed person's events combined into it (see that view's own docstring).

    Only a SHA-256 hash of the token is stored: the feed URL is a bearer
    credential, so a copy of the database must not be enough to read anyone's
    calendar. The raw token exists only in the URL handed to the user once,
    right after ``issue()`` creates or replaces it; the feed view hashes the
    token it is given and looks that up.

    Not club-scoped, unlike PushSubscription: ``User`` is the one global
    model in this platform (CLAUDE.md), and the same token works under
    whichever club subdomain the feed URL is fetched from -- the view
    resolves ``request.club`` and this account's Member/managed people
    within it exactly like every other screen already does, so an account
    active in more than one club still needs only the one token.
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="calendar_feed_token", verbose_name=_("user"))
    token_hash = models.CharField(_("token hash"), max_length=64, unique=True, editable=False)

    class Meta:
        verbose_name = _("calendar feed token")
        verbose_name_plural = _("calendar feed tokens")

    def __str__(self):
        return f"Calendar feed — {self.user}"

    @classmethod
    def issue(cls, user) -> str:
        """Create or replace ``user``'s token and return the raw value -- the
        only time it is available. Replacing one invalidates the old URL
        immediately (e.g. a shared or leaked link someone wants to revoke)."""
        raw_token = _generate_feed_token()
        cls.objects.update_or_create(user=user, defaults={"token_hash": hash_feed_token(raw_token)})
        return raw_token

    @classmethod
    def find(cls, raw_token: str):
        """The token row ``raw_token`` belongs to, or ``None``."""
        return cls.objects.select_related("user").filter(token_hash=hash_feed_token(raw_token)).first()
