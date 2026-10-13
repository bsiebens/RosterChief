from django.conf import settings
from django.db import models
from django.db.models import Q, UniqueConstraint
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from rosterchief.base import UUIDModel


class Announcement(UUIDModel):
    """A one-off platform-wide (or single-club) message, pushed by a platform
    superuser from the control panel -- see announcements.services. Delivered two
    ways once ``status`` is SENT: a notification (inbox, native/web push, email) to every account holder
    (announcements.services.publish) and a one-time pop-up shown the next time
    each targeted, not-yet-shown user loads the management or mobile app (see
    AnnouncementSeen below, and announcements.views.PendingAnnouncementView).

    Who is reached (``audience``, always limited to members holding a login, see
    announcements.services.audience_members; "attached to a club" = a club membership row,
    a team membership, a staff assignment or a club role, in any season):

    * everyone -- every account-holder attached to the club.
    * admins -- holders of a ClubRole of admin, editor or member admin in the club.
    * staff -- members with a StaffAssignment on one of the club's teams in a season
      covering today (the coaches, managers and other team staff).
    * parents -- attached members with a FamilyMembership of role parent or guardian.
    * players -- members on the roster (TeamMembership) of one of the club's teams in a
      season covering today.

    With ``club`` blank, a person in several clubs is notified once, in the first club
    (by Club.created, then pk) whose audience contains them.

    Never created directly by the compose form -- announcements.services.
    create_and_confirm is the only path that creates a row, and only once a
    superuser has confirmed a preview of it (see that function's own docstring
    for why there is no "draft" status).
    """

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        SENT = "sent", _("Sent")
        CANCELLED = "cancelled", _("Cancelled")

    class Audience(models.TextChoices):
        EVERYONE = "everyone", _("Everyone")
        ADMINS = "admins", _("Club administrators")
        STAFF = "staff", _("Coaches and staff")
        PARENTS = "parents", _("Parents and guardians")
        PLAYERS = "players", _("Players")

    title = models.CharField(_("title"), max_length=255)
    message = models.TextField(_("message"))

    #: Left blank to target every club on the platform. Set to target one club's
    #: own people only -- see announcements.services.audience_members.
    club = models.ForeignKey("club.Club", on_delete=models.CASCADE, related_name="announcements", null=True, blank=True, verbose_name=_("club"), help_text=_("Leave blank to reach every club."))

    audience = models.CharField(
        _("audience"),
        max_length=20,
        choices=Audience.choices,
        default=Audience.EVERYONE,
        help_text=_("Who, within the chosen club(s), receives it. Only people with an account are reached."),
    )

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name=_("created by"))

    #: Left blank to push immediately (announcements.services.create_and_confirm
    #: publishes it in the same request). Set to a future time to have
    #: announcements.management.commands.publish_scheduled_announcements pick it
    #: up once that time arrives.
    scheduled_for = models.DateTimeField(_("scheduled for"), null=True, blank=True, help_text=_("Leave blank to push immediately."))

    status = models.CharField(_("status"), max_length=20, choices=Status.choices, default=Status.PENDING)
    sent_at = models.DateTimeField(_("sent at"), null=True, blank=True)

    class Meta:
        verbose_name = _("announcement")
        verbose_name_plural = _("announcements")
        ordering = ["-created"]

    def __str__(self):
        return self.title

    @property
    def is_due(self) -> bool:
        return self.status == self.Status.PENDING and (self.scheduled_for is None or self.scheduled_for <= timezone.now())


class AnnouncementSeen(UUIDModel):
    """One row the moment one user has actually been shown ``announcement`` --
    created inside a ``get_or_create`` (see announcements.services.consume_for),
    whose ``created`` flag is what decides whether to show the pop-up: this is
    what makes "one time only" hold even across two tabs racing the same
    request at once, not just across separate logins."""

    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name="seen_by", verbose_name=_("announcement"))
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+", verbose_name=_("user"))
    seen_at = models.DateTimeField(_("seen at"), auto_now_add=True)

    class Meta:
        verbose_name = _("announcement seen")
        verbose_name_plural = _("announcements seen")
        constraints = [UniqueConstraint(fields=["announcement", "user"], name="unique_announcement_seen_per_user")]

    def __str__(self):
        return f"{self.announcement} — {self.user}"


def live_announcements_query(user_club) -> Q:
    """Sent, and targeted at either every club or ``user_club`` specifically --
    shared between announcements.services.consume_for (the pop-up) and anywhere
    else that needs "what's currently live for this club"."""
    return Q(status=Announcement.Status.SENT) & (Q(club__isnull=True) | Q(club=user_club))
